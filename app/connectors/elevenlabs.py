"""Коннектор ElevenLabs: озвучка (TTS) и расшифровка (STT)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.ai.types import ToolSpec
from app.config import Settings, get_settings
from app.connectors.base import Artifact, Connector, ToolContext, ToolResult

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"  # Rachel
DEFAULT_MODEL = "eleven_multilingual_v2"
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"


class ElevenLabsConnector(Connector):
    """Голосовая озвучка через ElevenLabs."""

    id = "elevenlabs"
    title = "ElevenLabs — озвучка и расшифровка"
    emoji = "🎙"
    description = (
        "Синтез речи для закадрового голоса, список голосов и расшифровка аудио "
        "в текст с таймкодами."
    )
    requires_key = "elevenlabs"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def tools(self, ctx: ToolContext) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="elevenlabs_tts",
                description=(
                    "Озвучить текст: создать MP3 с речью и сохранить в ассеты проекта. "
                    "Результат можно подключить в композицию как <audio>."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Текст для озвучки"},
                        "voice_id": {"type": "string", "description": "ID голоса"},
                        "model_id": {"type": "string", "description": "ID модели синтеза"},
                        "filename": {"type": "string", "description": "Имя файла в assets/"},
                    },
                    "required": ["text"],
                },
            ),
            ToolSpec(
                name="elevenlabs_list_voices",
                description="Список доступных голосов с ID (нужен для выбора voice_id).",
                parameters={"type": "object", "properties": {}},
            ),
            ToolSpec(
                name="elevenlabs_transcribe",
                description="Расшифровать аудиофайл в текст с таймкодами.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Путь к файлу в проекте"},
                        "model_id": {"type": "string", "description": "ID модели STT"},
                    },
                    "required": ["path"],
                },
            ),
        ]

    async def execute(
        self, ctx: ToolContext, tool_name: str, arguments: dict[str, Any]
    ) -> ToolResult:
        api_key = str(ctx.options.get("elevenlabs_key") or "")
        if not api_key:
            return ToolResult.error(
                "Нет ключа ElevenLabs. Подключите его в разделе «🔌 Коннекторы → ElevenLabs»."
            )
        from elevenlabs import AsyncElevenLabs

        client = AsyncElevenLabs(api_key=api_key)
        match tool_name:
            case "elevenlabs_tts":
                return await self._tts(client, ctx, arguments)
            case "elevenlabs_list_voices":
                return await self._voices(client)
            case "elevenlabs_transcribe":
                return await self._transcribe(client, ctx, arguments)
        return ToolResult.error(f"Неизвестный инструмент {tool_name}")

    # --- реализация ------------------------------------------------
    async def _tts(self, client: Any, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        text = str(arguments.get("text") or "").strip()
        if not text:
            return ToolResult.error("Текст для озвучки пуст.")
        voice_id = str(arguments.get("voice_id") or DEFAULT_VOICE)
        model_id = str(arguments.get("model_id") or DEFAULT_MODEL)
        filename = str(arguments.get("filename") or "voice.mp3")
        if not filename.endswith(".mp3"):
            filename = f"{filename}.mp3"

        assets = ctx.project_dir / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        target = assets / Path(filename).name

        chunks: list[bytes] = []
        try:
            stream = client.text_to_speech.convert(
                voice_id=voice_id,
                text=text,
                model_id=model_id,
                output_format=DEFAULT_OUTPUT_FORMAT,
            )
            async for chunk in stream:
                chunks.append(chunk)
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"ElevenLabs вернул ошибку: {exc}")

        if not chunks:
            return ToolResult.error("ElevenLabs вернул пустое аудио.")
        target.write_bytes(b"".join(chunks))
        seconds = max(1, len(text) // 14)
        return ToolResult.ok(
            f"Озвучка готова: assets/{target.name} (~{seconds} с, голос {voice_id}).",
            artifacts=[
                Artifact(
                    path=str(target),
                    kind="audio",
                    caption="Озвучка",
                    mime="audio/mpeg",
                )
            ],
        )

    async def _voices(self, client: Any) -> ToolResult:
        try:
            response = await client.voices.get_all()
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"Не удалось получить список голосов: {exc}")
        lines = []
        for voice in getattr(response, "voices", [])[:40]:
            labels = getattr(voice, "labels", None) or {}
            lines.append(f"• {voice.voice_id} — {voice.name} ({labels.get('accent', '?')})")
        return ToolResult.ok("Доступные голоса:\n" + "\n".join(lines))

    async def _transcribe(
        self, client: Any, ctx: ToolContext, arguments: dict[str, Any]
    ) -> ToolResult:
        raw_path = str(arguments.get("path") or "")
        source = self._resolve(ctx, raw_path)
        if source is None or not source.exists():
            return ToolResult.error(f"Файл не найден: {raw_path}")
        model_id = str(arguments.get("model_id") or "scribe_v1")
        try:
            with source.open("rb") as handle:
                response = await client.speech_to_text.convert(
                    model_id=model_id, file=handle
                )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"Расшифровка не удалась: {exc}")
        text = getattr(response, "text", "") or ""
        return ToolResult.ok(f"Расшифровка ({len(text)} символов):\n{text[:3000]}")

    @staticmethod
    def _resolve(ctx: ToolContext, raw_path: str) -> Path | None:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = ctx.project_dir / raw_path
        try:
            resolved = candidate.resolve()
            resolved.relative_to(ctx.project_dir.resolve())
        except (ValueError, OSError):
            return None
        return resolved
