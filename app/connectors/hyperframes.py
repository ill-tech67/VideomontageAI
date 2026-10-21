"""Коннектор HyperFrames: ИИ пишет HTML-композицию, CLI рендерит видео."""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from typing import Any

from app.ai.types import ToolSpec
from app.config import Settings, get_settings
from app.connectors.base import Artifact, Connector, ToolContext, ToolResult
from app.services.jobs import JobManager, RenderRequest
from app.services.rendering import HyperFramesRunner, RenderOptions

logger = logging.getLogger(__name__)

_DANGEROUS_TAGS = {"iframe", "object", "embed", "applet", "base", "meta"}
_DANGEROUS_SCRIPT_PATTERNS = ("eval(", "fetch(", "xmlhttprequest", "import(", "websocket", "worker(")
_DANGEROUS_SCHEMES = ("javascript:", "vbscript:", "file:", "about:", "blob:")
_URL_ATTRIBUTES = {"href", "src", "poster", "action"}


def _is_safe_url(value: str) -> bool:
    """Разрешить относительные пути и http(s)/data:image, запретить опасные схемы."""
    lowered = value.strip().lower()
    if lowered.startswith(_DANGEROUS_SCHEMES):
        return False
    return not (lowered.startswith("data:") and not lowered.startswith("data:image/"))


class _Sanitizer(HTMLParser):
    """Убирает из композиции потенциально опасные конструкции."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._skip_tag: str | None = None
        self._in_script = False
        self._script_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        if tag in _DANGEROUS_TAGS:
            self._skip_depth = 1
            self._skip_tag = tag
            return
        if tag == "script":
            self._in_script = True
            self._script_buffer = []
        safe_attrs = []
        for name, value in attrs:
            lowered = name.lower()
            if lowered.startswith("on"):
                continue
            if value and lowered in _URL_ATTRIBUTES and not _is_safe_url(value):
                continue
            safe_attrs.append((name, value))
        attr_text = "".join(
            f' {name}="{value}"' if value is not None else f" {name}" for name, value in safe_attrs
        )
        self.parts.append(f"<{tag}{attr_text}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth:
            return
        if tag in _DANGEROUS_TAGS:
            return
        safe_attrs = []
        for name, value in attrs:
            lowered = name.lower()
            if lowered.startswith("on"):
                continue
            if value and lowered in _URL_ATTRIBUTES and not _is_safe_url(value):
                continue
            safe_attrs.append((name, value))
        attr_text = "".join(
            f' {name}="{value}"' if value is not None else f" {name}" for name, value in safe_attrs
        )
        self.parts.append(f"<{tag}{attr_text} />")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth == 0:
                    self._skip_tag = None
            return
        if tag == "script":
            self._in_script = False
            body = "".join(self._script_buffer)
            if not any(pattern in body.lower() for pattern in _DANGEROUS_SCRIPT_PATTERNS):
                self.parts.append(f"<script>{body}</script>")
            else:
                self.parts.append("<script>/* небезопасный код удалён */</script>")
            self._script_buffer = []
            return
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_script:
            self._script_buffer.append(data)
            return
        self.parts.append(data)

    def handle_comment(self, data: str) -> None:
        if self._skip_depth or self._in_script:
            return
        self.parts.append(f"<!--{data}-->")

    def result(self) -> str:
        return "".join(self.parts)


def sanitize_composition(html: str) -> str:
    """Почистить присланный моделью HTML."""
    parser = _Sanitizer()
    parser.feed(html)
    parser.close()
    return parser.result()


def extract_root_dimensions(html: str) -> tuple[int, int]:
    """Достать data-width/data-height корня композиции."""
    width = re.search(r'data-width="(\d+)"', html)
    height = re.search(r'data-height="(\d+)"', html)
    return (
        int(width.group(1)) if width else 1920,
        int(height.group(1)) if height else 1080,
    )


class HyperFramesConnector(Connector):
    """Инструменты рендера видео через HyperFrames CLI."""

    id = "hyperframes"
    title = "HyperFrames — рендер видео"
    emoji = "🎬"
    description = (
        "Пишет HTML-композицию и рендерит её в MP4 через HyperFrames CLI. "
        "Основной «движок монтажа» бота."
    )

    def __init__(
        self,
        runner: HyperFramesRunner | None = None,
        job_manager: JobManager | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.runner = runner or HyperFramesRunner(self.settings)
        self.job_manager = job_manager

    async def tools(self, ctx: ToolContext) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="hyperframes_write_composition",
                description=(
                    "Записать HTML-композицию HyperFrames в проект и сразу прогнать lint. "
                    "Верни результат линтера, чтобы исправить замечания. "
                    "Корень: <div id=\"root\" data-composition-id=\"main\" "
                    "data-width=\"1920\" data-height=\"1080\" data-duration=\"30\">."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "html": {"type": "string", "description": "Полный HTML композиции"},
                        "filename": {
                            "type": "string",
                            "description": "Имя файла, по умолчанию index.html",
                        },
                    },
                    "required": ["html"],
                },
            ),
            ToolSpec(
                name="hyperframes_check",
                description=(
                    "Полная проверка композиции в headless-браузере (JS-ошибки, "
                    "незагруженные ассеты, вёрстка, контраст). Используй перед рендером."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            ToolSpec(
                name="hyperframes_render",
                description=(
                    "Отрисовать композицию в MP4. Рендер идёт в фоне, готовое видео "
                    "бот пришлёт пользователю отдельным сообщением."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "quality": {
                            "type": "string",
                            "enum": ["draft", "looks", "delivery"],
                            "description": "Качество: draft — быстро, delivery — финал",
                        },
                        "fps": {"type": "integer", "description": "Кадров в секунду"},
                        "resolution": {
                            "type": "string",
                            "enum": ["landscape", "portrait", "square", "landscape-4k", "portrait-4k"],
                        },
                        "output": {"type": "string", "description": "Имя выходного файла"},
                        "wait": {
                            "type": "boolean",
                            "description": "true — дождаться рендера сейчас, false — в фоне",
                        },
                    },
                },
            ),
            ToolSpec(
                name="hyperframes_doctor",
                description="Проверить готовность окружения рендера (Chrome, ffmpeg, кодеки).",
                parameters={"type": "object", "properties": {}},
            ),
        ]

    async def execute(
        self, ctx: ToolContext, tool_name: str, arguments: dict[str, Any]
    ) -> ToolResult:
        match tool_name:
            case "hyperframes_write_composition":
                return await self._write_composition(ctx, arguments)
            case "hyperframes_check":
                return await self._check(ctx)
            case "hyperframes_render":
                return await self._render(ctx, arguments)
            case "hyperframes_doctor":
                return await self._doctor()
        return ToolResult.error(f"Неизвестный инструмент {tool_name}")

    # --- реализация ------------------------------------------------
    async def _write_composition(
        self, ctx: ToolContext, arguments: dict[str, Any]
    ) -> ToolResult:
        html = str(arguments.get("html") or "")
        if not html.strip():
            return ToolResult.error("HTML пустой.")
        filename = self._safe_name(str(arguments.get("filename") or "index.html"))
        if not filename.endswith(".html"):
            filename += ".html"

        clean = sanitize_composition(html)
        target = ctx.project_dir / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(clean, encoding="utf-8")

        width, height = extract_root_dimensions(clean)
        ctx.options["width"] = width
        ctx.options["height"] = height

        lint = await self.runner.lint(ctx.project_dir)
        message = f"Композиция записана: {filename}"
        if lint.findings:
            message += "\nЗамечания линтера:\n" + "\n".join(f"• {f}" for f in lint.findings[:15])
        elif lint.ok:
            message += "\nЛинтер: ошибок нет ✅"
        else:
            message += f"\nЛинтер завершился с кодом {lint.code}."
        return ToolResult.ok(message)

    async def _check(self, ctx: ToolContext) -> ToolResult:
        result = await self.runner.check(ctx.project_dir)
        if result.ok and not result.findings:
            return ToolResult.ok("Проверка пройдена: ошибок и предупреждений нет ✅")
        lines = [f"• {finding}" for finding in result.findings[:20]]
        tail = result.summary(800)
        return ToolResult.ok(
            "Проверка выявила замечания (исправь и запиши композицию заново):\n"
            + "\n".join(lines)
            + ("\n" + tail if tail and not lines else "")
        )

    async def _render(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        quality = str(arguments.get("quality") or "delivery")
        if quality not in {"draft", "looks", "delivery"}:
            quality = "delivery"
        fps = arguments.get("fps")
        resolution = arguments.get("resolution")
        output = self._safe_name(str(arguments.get("output") or "out.mp4"))
        wait = bool(arguments.get("wait", False))

        options = RenderOptions(
            composition="index.html",
            output=output,
            quality=quality,
            fps=int(fps) if fps else None,
            resolution=str(resolution) if resolution else None,
            docker=self.settings.render_mode == "docker",
        )
        if self.settings.render_mode == "cloud":
            options.docker = False

        if wait or self.job_manager is None:
            result = await self.runner.render(
                ctx.project_dir,
                options,
                on_progress=lambda pct, code: ctx.progress(pct, code),
            )
            if not result.ok:
                return ToolResult.error("Рендер не удался:\n" + result.summary(1200))
            output_path = ctx.project_dir / options.output
            return ToolResult.ok(
                f"Рендер готов: {output_path.name}",
                artifacts=[Artifact(path=str(output_path), kind="video", caption="Готовое видео")],
            )

        request = RenderRequest(
            user_id=ctx.user_id,
            chat_id=ctx.chat_id,
            project_id=ctx.project_id,
            project_dir=ctx.project_dir,
            options=options,
            status_message_id=ctx.options.get("status_message_id"),
            title=ctx.options.get("title", "Видео"),
        )
        if ctx.project_id:
            existing = await self.job_manager.active_job(ctx.project_id)
            if existing is not None:
                return ToolResult.ok(
                    f"Рендер этого проекта уже идёт (задача {existing.id[:8]}). "
                    "Дождитесь результата — он придёт в чат отдельным сообщением."
                )
        job = await self.job_manager.submit_render(request)
        return ToolResult.ok(
            f"Рендер поставлен в фон (задача {job.id[:8]}). "
            "Готовое видео бот отправит пользователю отдельным сообщением."
        )

    async def _doctor(self) -> ToolResult:
        payload = await self.runner.doctor()
        checks = payload.get("checks", []) if isinstance(payload, dict) else []
        lines = [
            f"{'✅' if check.get('ok') else '⚠️'} {check.get('name')}: {check.get('detail')}"
            for check in checks
        ]
        ffmpeg = self.runner.ffmpeg_binary()
        header = "Окружение рендера:"
        if ffmpeg:
            header += f"\nffmpeg: {ffmpeg}"
        return ToolResult.ok(header + "\n" + "\n".join(lines[:20]))

    @staticmethod
    def _safe_name(name: str) -> str:
        """Защита от выхода за пределы каталога проекта."""
        cleaned = re.sub(r"[^A-Za-z0-9._\- ]", "", name).strip().replace(" ", "_")
        cleaned = cleaned.lstrip(".")
        return cleaned or "index.html"
