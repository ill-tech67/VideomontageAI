"""Агентный цикл: модель + инструменты коннекторов."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.ai.providers.base import LLMProvider
from app.ai.types import ChatTurn, LLMResponse, ToolSpec
from app.connectors.base import Artifact, ToolContext, ToolResult
from app.connectors.registry import ConnectorRegistry

logger = logging.getLogger(__name__)

EventHandler = Callable[["AgentEvent"], Awaitable[None]]


@dataclass(slots=True)
class AgentEvent:
    """Событие агентного цикла (для отчётов в Telegram)."""

    kind: str  # step | tool | tool_error | final
    text: str
    tool_name: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentResult:
    """Итог работы агента."""

    text: str = ""
    steps: int = 0
    artifacts: list[Artifact] = field(default_factory=list)
    tool_log: list[str] = field(default_factory=list)
    error: str | None = None


class AgentRunner:
    """Прогоняет цикл «модель → инструмент → модель»."""

    def __init__(
        self,
        provider: LLMProvider,
        registry: ConnectorRegistry,
        ctx: ToolContext,
        *,
        max_steps: int = 12,
        on_event: EventHandler | None = None,
        tools: list[ToolSpec] | None = None,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.ctx = ctx
        self.max_steps = max_steps
        self.on_event = on_event
        self._tools = tools

    async def _emit(self, event: AgentEvent) -> None:
        if self.on_event is None:
            return
        try:
            await self.on_event(event)
        except Exception:  # noqa: BLE001 - прогресс не должен ломать агента
            logger.debug("Не удалось отправить событие агента", exc_info=True)

    async def run(
        self,
        *,
        system: str,
        history: list[ChatTurn],
        user_text: str,
        tools: list[ToolSpec] | None = None,
    ) -> AgentResult:
        """Выполнить задачу до финального текстового ответа."""
        tool_specs = tools if tools is not None else (self._tools or [])
        messages: list[ChatTurn] = [*history, ChatTurn(role="user", content=user_text)]

        result = AgentResult()
        for step in range(1, self.max_steps + 1):
            result.steps = step
            try:
                response: LLMResponse = await self.provider.complete(
                    system=system,
                    messages=messages,
                    tools=tool_specs,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Ошибка обращения к модели")
                result.error = str(exc)
                return result

            if not response.tool_calls:
                result.text = response.text or "Готово."
                await self._emit(AgentEvent(kind="final", text=result.text))
                return result

            messages.append(
                ChatTurn(
                    role="assistant",
                    content=response.text,
                    tool_calls=response.tool_calls,
                )
            )
            if response.text:
                await self._emit(
                    AgentEvent(kind="step", text=response.text[:1500], payload={"step": step})
                )

            for call in response.tool_calls:
                await self._emit(
                    AgentEvent(kind="tool", text=f"Вызываю {call.name}", tool_name=call.name)
                )
                outcome: ToolResult = await self.registry.execute(
                    self.ctx, call.name, call.arguments
                )
                result.tool_log.append(f"{call.name}: {outcome.text[:400]}")
                if outcome.artifacts:
                    result.artifacts.extend(outcome.artifacts)
                await self._emit(
                    AgentEvent(
                        kind="tool_error" if outcome.is_error else "tool",
                        text=outcome.text[:1200],
                        tool_name=call.name,
                    )
                )
                messages.append(
                    ChatTurn(
                        role="tool",
                        content=outcome.text[:12000],
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )

        result.error = "Лимит шагов агента исчерпан."
        result.text = result.text or (
            "Не успел завершить работу за отведённое число шагов. "
            "Попробуйте уточнить задачу или разбить её на части."
        )
        return result
