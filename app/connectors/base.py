"""Базовые интерфейсы коннекторов — «руки» ИИ-агента."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.ai.types import ToolSpec


@dataclass(slots=True)
class Artifact:
    """Файл, полученный в результате работы инструмента."""

    path: str
    kind: str = "file"  # video | audio | image | file
    caption: str = ""
    mime: str | None = None


@dataclass(slots=True)
class ToolResult:
    """Результат вызова инструмента."""

    text: str = ""
    artifacts: list[Artifact] = field(default_factory=list)
    is_error: bool = False

    @classmethod
    def error(cls, message: str) -> ToolResult:
        return cls(text=message, is_error=True)

    @classmethod
    def ok(cls, message: str, artifacts: list[Artifact] | None = None) -> ToolResult:
        return cls(text=message, artifacts=artifacts or [])


@dataclass(slots=True)
class ToolContext:
    """Контекст выполнения инструмента."""

    user_id: int
    chat_id: int
    project_dir: Path
    project_id: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    notify: Callable[[str], Awaitable[None]] = field(default_factory=lambda: _noop)
    progress: Callable[[int, str], Awaitable[None]] = field(default_factory=lambda: _noop)


async def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


class Connector(ABC):
    """Коннектор — внешний сервис, доступный ИИ-агенту как набор инструментов."""

    id: str = "base"
    title: str = "Base"
    emoji: str = "🔌"
    description: str = ""
    requires_key: str | None = None
    """id провайдера, чей ключ нужен для работы (например, elevenlabs)."""

    def is_available(self, has_key: bool) -> bool:
        return True

    @abstractmethod
    async def tools(self, ctx: ToolContext) -> list[ToolSpec]:
        """Список инструментов коннектора."""

    @abstractmethod
    async def execute(self, ctx: ToolContext, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """Выполнить инструмент."""

    def status_hint(self) -> str:
        """Подсказка для меню коннекторов."""
        return ""
