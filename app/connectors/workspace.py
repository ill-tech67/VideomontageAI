"""Коннектор рабочего пространства: файлы проекта, тексты, субтитры."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.ai.types import ToolSpec
from app.connectors.base import Connector, ToolContext, ToolResult

MAX_TEXT_BYTES = 512 * 1024


class WorkspaceConnector(Connector):
    """Файловая система проекта (в рамках каталога проекта)."""

    id = "workspace"
    title = "Рабочая папка — файлы проекта"
    emoji = "🗂"
    description = "Посмотреть загруженные материалы, записать тексты, субтитры и вспомогательные файлы."

    async def tools(self, ctx: ToolContext) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="workspace_list_assets",
                description="Список файлов проекта: загруженные видео, аудио и созданные ассеты.",
                parameters={"type": "object", "properties": {}},
            ),
            ToolSpec(
                name="workspace_read_file",
                description="Прочитать текстовый файл проекта (например, субтитры или html-фрагмент).",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Путь к файлу"}},
                    "required": ["path"],
                },
            ),
            ToolSpec(
                name="workspace_write_file",
                description=(
                    "Записать текстовый файл в проект (SRT-субтитры, JSON, текст сценария). "
                    "Путь считается от каталога проекта."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Путь файла"},
                        "content": {"type": "string", "description": "Содержимое"},
                    },
                    "required": ["path", "content"],
                },
            ),
        ]

    async def execute(
        self, ctx: ToolContext, tool_name: str, arguments: dict[str, Any]
    ) -> ToolResult:
        match tool_name:
            case "workspace_list_assets":
                return self._list(ctx)
            case "workspace_read_file":
                return self._read(ctx, arguments)
            case "workspace_write_file":
                return self._write(ctx, arguments)
        return ToolResult.error(f"Неизвестный инструмент {tool_name}")

    def _list(self, ctx: ToolContext) -> ToolResult:
        lines: list[str] = []
        for path in sorted(ctx.project_dir.rglob("*")):
            if path.is_dir() or "__pycache__" in path.parts:
                continue
            if path.name.startswith("."):
                continue
            relative = path.relative_to(ctx.project_dir).as_posix()
            size_kb = path.stat().st_size / 1024
            lines.append(f"• {relative} — {size_kb:.1f} КБ")
        if not lines:
            return ToolResult.ok("В проекте пока нет файлов. Можно загрузить материалы в Telegram.")
        return ToolResult.ok("Файлы проекта:\n" + "\n".join(lines[:120]))

    def _read(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        path = self._resolve(ctx, str(arguments.get("path") or ""))
        if path is None or not path.exists() or not path.is_file():
            return ToolResult.error("Файл не найден.")
        if path.stat().st_size > MAX_TEXT_BYTES:
            return ToolResult.error("Файл слишком большой для чтения текстом.")
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult.error(f"Не удалось прочитать файл: {exc}")
        return ToolResult.ok(f"Содержимое {path.name}:\n{content[:20000]}")

    def _write(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        raw_path = str(arguments.get("path") or "").strip()
        if not raw_path:
            return ToolResult.error("Не указан путь файла.")
        path = self._resolve(ctx, raw_path, must_exist=False)
        if path is None:
            return ToolResult.error("Недопустимый путь: запись вне каталога проекта запрещена.")
        content = str(arguments.get("content") or "")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            return ToolResult.error(f"Не удалось записать файл: {exc}")
        return ToolResult.ok(f"Файл записан: {path.relative_to(ctx.project_dir).as_posix()}")

    @staticmethod
    def _resolve(ctx: ToolContext, raw_path: str, must_exist: bool = True) -> Path | None:
        if not raw_path:
            return None
        candidate = Path(raw_path)
        resolved = candidate if candidate.is_absolute() else ctx.project_dir / candidate
        try:
            resolved = resolved.resolve()
            resolved.relative_to(ctx.project_dir.resolve())
        except (ValueError, OSError):
            return None
        if must_exist and not resolved.exists():
            return None
        return resolved
