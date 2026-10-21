"""Базовый интерфейс ИИ-провайдера."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.ai.types import ChatTurn, LLMResponse, ToolSpec


class LLMProvider(ABC):
    """Единый интерфейс для OpenAI / Anthropic / Gemini."""

    name: str = "unknown"
    title: str = "Unknown"
    default_model: str = ""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    @abstractmethod
    async def complete(
        self,
        *,
        system: str,
        messages: list[ChatTurn],
        tools: list[ToolSpec],
        model: str | None = None,
        temperature: float = 0.4,
    ) -> LLMResponse:
        """Выполнить один запрос к модели."""

    async def list_models(self) -> list[str]:
        """Список доступных моделей (если провайдер это умеет)."""
        return [self.default_model] if self.default_model else []

    async def check_key(self) -> tuple[bool, str]:
        """Проверить доступность ключа. Возвращает (ok, сообщение)."""
        try:
            await self.complete(
                system="Ответь одним словом: ok",
                messages=[],
                tools=[],
                model=self.default_model or None,
                temperature=0.0,
            )
        except Exception as exc:  # noqa: BLE001 - текст ошибки уходит пользователю
            return False, str(exc)[:400]
        return True, "Ключ рабочий ✅"
