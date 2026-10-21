"""Реестр коннекторов: сборка инструментов и их запуск."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from app.ai.types import ToolSpec
from app.connectors.base import Connector, ToolContext, ToolResult


class ConnectorRegistry:
    """Хранит коннекторы и маршрутизирует вызовы инструментов."""

    def __init__(self) -> None:
        self._connectors: dict[str, Connector] = {}
        self._tool_owner: dict[str, tuple[str, str]] = {}

    def register(self, connector: Connector) -> None:
        self._connectors[connector.id] = connector

    def get(self, connector_id: str) -> Connector | None:
        return self._connectors.get(connector_id)

    def all(self) -> list[Connector]:
        return list(self._connectors.values())

    def ids(self) -> list[str]:
        return list(self._connectors)

    def enabled_for(self, enabled_ids: Iterable[str]) -> list[Connector]:
        enabled = set(enabled_ids)
        return [c for c in self._connectors.values() if c.id in enabled]

    async def build_tools(
        self,
        ctx: ToolContext,
        connector_ids: Iterable[str],
        has_key: Callable[[str], bool],
    ) -> list[ToolSpec]:
        """Собрать список инструментов для выбранных коннекторов."""
        specs: list[ToolSpec] = []
        for connector in self.enabled_for(connector_ids):
            if not connector.is_available(has_key(connector.id)):
                continue
            for spec in await connector.tools(ctx):
                self._tool_owner[spec.name] = (connector.id, spec.name)
                specs.append(spec)
        return specs

    async def execute(
        self,
        ctx: ToolContext,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Найти владельца инструмента и выполнить вызов."""
        owner = self._tool_owner.get(tool_name)
        connector = None
        if owner:
            connector = self._connectors.get(owner[0])
        if connector is None:
            for candidate in self._connectors.values():
                if any(spec.name == tool_name for spec in await candidate.tools(ctx)):
                    connector = candidate
                    break
        if connector is None:
            return ToolResult.error(f"Инструмент «{tool_name}» не найден среди подключённых.")
        try:
            return await connector.execute(ctx, tool_name, arguments)
        except Exception as exc:  # noqa: BLE001 - модель должна увидеть причину
            return ToolResult.error(f"Ошибка выполнения «{tool_name}»: {exc}")
