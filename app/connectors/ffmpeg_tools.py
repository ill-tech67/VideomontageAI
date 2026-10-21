"""Коннектор FFmpeg:probe, нарезка, склейка, звук, кадры."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from app.ai.types import ToolSpec
from app.config import Settings, get_settings
from app.connectors.base import Artifact, Connector, ToolContext, ToolResult
from app.services.rendering import HyperFramesRunner

logger = logging.getLogger(__name__)


class FFmpegConnector(Connector):
    """Базовые видео-операции поверх FFmpeg."""

    id = "ffmpeg"
    title = "FFmpeg — нарезка и склейка"
    emoji = "✂️"
    description = "Проба файлов, вырезание фрагментов, склейка, извлечение звука и кадров."

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.runner = HyperFramesRunner(self.settings)

    async def tools(self, ctx: ToolContext) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="ffprobe_media",
                description="Получить длительность, разрешение, fps и потоки медиафайла.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Путь к файлу"}},
                    "required": ["path"],
                },
            ),
            ToolSpec(
                name="ffmpeg_trim",
                description="Вырезать фрагмент из видео (по секундам) без перекодирования.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Исходное видео"},
                        "start": {"type": "number", "description": "Начало, сек"},
                        "end": {"type": "number", "description": "Конец, сек"},
                        "output": {"type": "string", "description": "Имя результата в assets/"},
                    },
                    "required": ["path", "start", "end"],
                },
            ),
            ToolSpec(
                name="ffmpeg_concat",
                description="Склеить несколько файлов в один (порядок сохраняется).",
                parameters={
                    "type": "object",
                    "properties": {
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Файлы для склейки",
                        },
                        "output": {"type": "string", "description": "Имя результата в assets/"},
                    },
                    "required": ["paths"],
                },
            ),
            ToolSpec(
                name="ffmpeg_extract_audio",
                description="Извлечь звуковую дорожку в отдельный файл.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Исходный файл"},
                        "output": {"type": "string", "description": "Имя результата в assets/"},
                    },
                    "required": ["path"],
                },
            ),
            ToolSpec(
                name="ffmpeg_thumbnail",
                description="Снять кадр из видео в JPG (для превью или фона композиции).",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Исходное видео"},
                        "at": {"type": "number", "description": "Время кадра, сек"},
                        "output": {"type": "string", "description": "Имя результата в assets/"},
                    },
                    "required": ["path"],
                },
            ),
        ]

    async def execute(
        self, ctx: ToolContext, tool_name: str, arguments: dict[str, Any]
    ) -> ToolResult:
        match tool_name:
            case "ffprobe_media":
                return await self._probe(ctx, arguments)
            case "ffmpeg_trim":
                return await self._trim(ctx, arguments)
            case "ffmpeg_concat":
                return await self._concat(ctx, arguments)
            case "ffmpeg_extract_audio":
                return await self._extract_audio(ctx, arguments)
            case "ffmpeg_thumbnail":
                return await self._thumbnail(ctx, arguments)
        return ToolResult.error(f"Неизвестный инструмент {tool_name}")

    # --- реализация ------------------------------------------------
    async def _probe(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        source = self._resolve(ctx, str(arguments.get("path") or ""))
        if source is None or not source.exists():
            return ToolResult.error("Файл не найден.")
        ffprobe = self.runner.ffprobe_binary()
        if not ffprobe:
            return ToolResult.error("ffprobe не найден в системе.")
        process = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(source),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            return ToolResult.error(stderr.decode("utf-8", "replace")[:600])
        try:
            data = json.loads(stdout.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            return ToolResult.error("Не удалось разобрать вывод ffprobe.")
        fmt = data.get("format", {})
        streams = data.get("streams", [])
        summary = {
            "duration": round(float(fmt.get("duration", 0)), 2),
            "size_mb": round(float(fmt.get("size", 0)) / 1048576, 2),
            "streams": [
                {
                    "type": stream.get("codec_type"),
                    "codec": stream.get("codec_name"),
                    "width": stream.get("width"),
                    "height": stream.get("height"),
                    "fps": stream.get("r_frame_rate"),
                    "channels": stream.get("channels"),
                }
                for stream in streams
            ],
        }
        return ToolResult.ok(json.dumps(summary, ensure_ascii=False, indent=2))

    async def _trim(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        source = self._resolve(ctx, str(arguments.get("path") or ""))
        if source is None or not source.exists():
            return ToolResult.error("Файл не найден.")
        start = float(arguments.get("start", 0))
        end = float(arguments.get("end", 0))
        if end <= start:
            return ToolResult.error("end должен быть больше start.")
        output = self._asset_path(ctx, str(arguments.get("output") or "clip.mp4"), ".mp4")
        args = [
            self._ffmpeg(),
            "-y",
            "-ss",
            str(start),
            "-to",
            str(end),
            "-i",
            str(source),
            "-c",
            "copy",
            str(output),
        ]
        code, stdout, stderr = await self._exec(args)
        if code != 0:
            return ToolResult.error(stderr[-800:] or stdout[-800:])
        return ToolResult.ok(
            f"Фрагмент {start:.1f}–{end:.1f} с готов: assets/{output.name}",
            artifacts=[Artifact(path=str(output), kind="video", caption="Фрагмент")],
        )

    async def _concat(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        raw_paths = arguments.get("paths") or []
        if not isinstance(raw_paths, list) or not raw_paths:
            return ToolResult.error("Не переданы файлы для склейки.")
        files: list[Path] = []
        for item in raw_paths:
            resolved = self._resolve(ctx, str(item))
            if resolved is None or not resolved.exists():
                return ToolResult.error(f"Файл не найден: {item}")
            files.append(resolved)
        output = self._asset_path(ctx, str(arguments.get("output") or "merged.mp4"), ".mp4")

        listing = ctx.project_dir / "concat.txt"
        listing.write_text(
            "\n".join(f"file '{path.as_posix()}'" for path in files), encoding="utf-8"
        )
        args = [
            self._ffmpeg(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-c",
            "copy",
            str(output),
        ]
        code, stdout, stderr = await self._exec(args)
        listing.unlink(missing_ok=True)
        if code != 0:
            return ToolResult.error(stderr[-800:] or stdout[-800:])
        return ToolResult.ok(
            f"Склеено {len(files)} файлов: assets/{output.name}",
            artifacts=[Artifact(path=str(output), kind="video", caption="Склейка")],
        )

    async def _extract_audio(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        source = self._resolve(ctx, str(arguments.get("path") or ""))
        if source is None or not source.exists():
            return ToolResult.error("Файл не найден.")
        output = self._asset_path(ctx, str(arguments.get("output") or "audio.mp3"), ".mp3")
        args = [
            self._ffmpeg(),
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "2",
            "-ar",
            "44100",
            str(output),
        ]
        code, stdout, stderr = await self._exec(args)
        if code != 0:
            return ToolResult.error(stderr[-800:] or stdout[-800:])
        return ToolResult.ok(
            f"Звук извлечён: assets/{output.name}",
            artifacts=[Artifact(path=str(output), kind="audio", caption="Звук", mime="audio/mpeg")],
        )

    async def _thumbnail(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        source = self._resolve(ctx, str(arguments.get("path") or ""))
        if source is None or not source.exists():
            return ToolResult.error("Файл не найден.")
        at = float(arguments.get("at", 0))
        output = self._asset_path(ctx, str(arguments.get("output") or "frame.jpg"), ".jpg")
        args = [
            self._ffmpeg(),
            "-y",
            "-ss",
            str(at),
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(output),
        ]
        code, stdout, stderr = await self._exec(args)
        if code != 0:
            return ToolResult.error(stderr[-800:] or stdout[-800:])
        return ToolResult.ok(
            f"Кадр на {at:.1f} с готов: assets/{output.name}",
            artifacts=[Artifact(path=str(output), kind="image", caption="Кадр", mime="image/jpeg")],
        )

    # --- вспомогательное --------------------------------------------
    def _ffmpeg(self) -> str:
        binary = self.runner.ffmpeg_binary()
        if not binary:
            raise FileNotFoundError("ffmpeg не найден в системе")
        return binary

    @staticmethod
    async def _exec(args: list[str]) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        return (
            process.returncode or 0,
            stdout.decode("utf-8", "replace"),
            stderr.decode("utf-8", "replace"),
        )

    @staticmethod
    def _resolve(ctx: ToolContext, raw_path: str) -> Path | None:
        if not raw_path:
            return None
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = ctx.project_dir / raw_path
        try:
            resolved = candidate.resolve()
            resolved.relative_to(ctx.project_dir.resolve())
        except (ValueError, OSError):
            return None
        return resolved

    @staticmethod
    def _asset_path(ctx: ToolContext, name: str, suffix: str) -> Path:
        assets = ctx.project_dir / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        safe = Path(name).name
        if not safe.lower().endswith(suffix):
            safe = f"{safe}{suffix}"
        return assets / safe
