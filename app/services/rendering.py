"""Обёртка над HyperFrames CLI: lint, check, render, doctor."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings, get_settings

ProgressCallback = Callable[[int, str], Awaitable[None]]

_ERROR_RE = re.compile(r"(\d+)\s+error\(s\)", re.IGNORECASE)
_WARNING_RE = re.compile(r"(\d+)\s+warning\(s\)", re.IGNORECASE)
_PROGRESS_RE = re.compile(r"@hf-progress\s+(\{.*\})")
_FINDING_RE = re.compile(r"^\s*[⚠✗]\s*(?:\[[^\]]*\]\s*)?(.+)$", re.MULTILINE)


@dataclass(slots=True)
class CommandResult:
    """Результат запуска внешней команды."""

    ok: bool = False
    code: int = 0
    stdout: str = ""
    stderr: str = ""
    payload: dict | None = None
    errors: int = 0
    warnings: int = 0
    findings: list[str] = field(default_factory=list)

    def summary(self, limit: int = 1500) -> str:
        parts: list[str] = []
        if self.findings:
            parts.append("\n".join(f"• {item}" for item in self.findings[:12]))
        if self.stderr.strip():
            parts.append(self.stderr.strip()[-limit:])
        elif self.stdout.strip():
            parts.append(self.stdout.strip()[-limit:])
        return "\n".join(parts).strip()


@dataclass(slots=True)
class RenderOptions:
    """Параметры рендера."""

    composition: str = "index.html"
    output: str = "out.mp4"
    quality: str = "delivery"
    fps: int | None = None
    resolution: str | None = None
    extra_args: list[str] = field(default_factory=list)
    docker: bool = False


class HyperFramesRunner:
    """Запускает HyperFrames CLI в подпроцессе."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # --- окружение ------------------------------------------------
    def cli_command(self) -> list[str]:
        """Команда запуска HyperFrames CLI (с учётом Windows и npx)."""
        cli = self.settings.hyperframes_cli.strip() or "npx"
        resolved = shutil.which(cli) or cli
        version = (self.settings.hyperframes_version or "").strip()
        is_npx = "npx" in Path(resolved).name.lower()

        if is_npx:
            base = [resolved, "-y"]
            base.append(f"hyperframes@{version}" if version else "hyperframes")
            return base

        base = [resolved]
        return base

    def ffmpeg_binary(self) -> str | None:
        """Найти современный ffmpeg (нужен фильтр setparams, FFmpeg >= 5)."""
        if self.settings.ffmpeg_binary:
            candidate = Path(self.settings.ffmpeg_binary)
            if candidate.exists():
                return str(candidate)
        found = shutil.which("ffmpeg")
        if found and self._is_modern(found):
            return found
        for extra in self._known_ffmpeg_paths():
            if extra.exists() and self._is_modern(str(extra)):
                return str(extra)
        return found

    def ffprobe_binary(self) -> str | None:
        if self.settings.ffprobe_binary:
            candidate = Path(self.settings.ffprobe_binary)
            if candidate.exists():
                return str(candidate)
        found = shutil.which("ffprobe")
        if found:
            return found
        ffmpeg = self.ffmpeg_binary()
        if ffmpeg:
            sibling = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
            if sibling.exists():
                return str(sibling)
        return None

    @staticmethod
    def _known_ffmpeg_paths() -> list[Path]:
        paths: list[Path] = []
        if os.name == "nt":
            winget_root = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
            if winget_root.exists():
                for folder in winget_root.glob("*FFmpeg*"):
                    paths.extend(folder.glob("**/bin/ffmpeg.exe"))
        else:
            paths.extend(
                Path(item)
                for item in (
                    "/usr/bin/ffmpeg",
                    "/usr/local/bin/ffmpeg",
                    "/opt/homebrew/bin/ffmpeg",
                    "/snap/bin/ffmpeg",
                )
            )
        return paths

    @staticmethod
    def _is_modern(path: str) -> bool:
        """Проверить версию ffmpeg: нужен setparams (FFmpeg >= 5.0)."""
        try:
            result = subprocess_run_sync([path, "-hide_banner", "-version"])
        except OSError:
            return False
        if result.returncode != 0:
            return False
        match = re.search(r"ffmpeg version n?(\d+)\.(\d+)", result.stdout)
        if not match:
            return False
        major, minor = int(match.group(1)), int(match.group(2))
        return (major, minor) >= (5, 0)

    def build_env(self) -> dict[str, str]:
        """Окружение для подпроцесса: ffmpeg должен быть первым в PATH."""
        env = dict(os.environ)
        ffmpeg = self.ffmpeg_binary()
        if ffmpeg:
            ffmpeg_dir = str(Path(ffmpeg).parent)
            env["PATH"] = ffmpeg_dir + os.pathsep + env.get("PATH", "")
            env["FFMPEG_BINARY"] = ffmpeg
        ffprobe = self.ffprobe_binary()
        if ffprobe:
            env["FFPROBE_BINARY"] = ffprobe
        env["HYPERFRAMES_RENDER_DETACHED"] = "0"
        return env

    # --- команды ---------------------------------------------------
    async def _run(
        self,
        args: list[str],
        cwd: Path | None = None,
        timeout_seconds: int | None = None,
        on_line: Callable[[str], Awaitable[None]] | None = None,
    ) -> CommandResult:
        command = [*self.cli_command(), *args]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(cwd) if cwd else None,
                env=self.build_env(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError:
            return CommandResult(
                ok=False,
                code=127,
                stderr=f"Команда не найдена: {command[0]}",
            )

        output: list[str] = []

        async def drain() -> None:
            assert process.stdout is not None
            async for raw_line in process.stdout:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                output.append(line)
                if on_line is not None:
                    await on_line(line)

        try:
            await asyncio.wait_for(drain(), timeout=timeout_seconds or self.settings.render_timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            return CommandResult(
                ok=False,
                code=-1,
                stdout="\n".join(output),
                stderr=f"Таймаут {timeout_seconds or self.settings.render_timeout} с",
            )

        code = await process.wait()
        text = "\n".join(output)
        return CommandResult(
            ok=code == 0,
            code=code,
            stdout=text,
            errors=_parse_count(text, _ERROR_RE),
            warnings=_parse_count(text, _WARNING_RE),
            findings=_parse_findings(text),
        )

    async def doctor(self) -> dict:
        """Проверка окружения рендера."""
        result = await self._run(["doctor", "--json"], timeout_seconds=120)
        try:
            return json.loads(result.stdout[result.stdout.find("{") :]) or {}
        except (json.JSONDecodeError, ValueError):
            return {"ok": result.ok, "raw": result.stdout[-2000:]}

    async def lint(self, project_dir: Path) -> CommandResult:
        return await self._run(["lint", "--dir", str(project_dir)], timeout_seconds=300)

    async def check(self, project_dir: Path) -> CommandResult:
        return await self._run(["check", "--dir", str(project_dir)], timeout_seconds=600)

    async def render(
        self,
        project_dir: Path,
        options: RenderOptions,
        on_progress: ProgressCallback | None = None,
    ) -> CommandResult:
        """Отрисовать композицию в MP4."""
        output_path = project_dir / options.output
        args: list[str] = ["render", "--dir", str(project_dir), "-o", str(output_path)]
        args += ["-c", options.composition]
        args += ["--quality", options.quality]
        if options.fps:
            args += ["--fps", str(options.fps)]
        if options.resolution:
            args += ["--resolution", options.resolution]
        if options.docker:
            args.append("--docker")
        args += list(options.extra_args)

        async def handle_line(line: str) -> None:
            if on_progress is None:
                return
            match = _PROGRESS_RE.search(line)
            if not match:
                return
            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                return
            pct = int(payload.get("pct", 0))
            code = str(payload.get("code", "render"))
            await on_progress(max(0, min(100, pct)), code)

        result = await self._run(args, cwd=project_dir, on_line=handle_line)
        if result.ok and output_path.exists():
            result.payload = {"output": str(output_path), "size": output_path.stat().st_size}
        return result


def _parse_count(text: str, pattern: re.Pattern[str]) -> int:
    matches = pattern.findall(text)
    return int(matches[-1]) if matches else 0


def _parse_findings(text: str) -> list[str]:
    findings: list[str] = []
    for match in _FINDING_RE.finditer(text):
        line = match.group(1).strip()
        if line and line not in findings:
            findings.append(line[:300])
    return findings


def subprocess_run_sync(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Синхронный запуск (только для проверки версии ffmpeg)."""
    return subprocess.run(  # noqa: S603 - команда формируется из настроек
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
