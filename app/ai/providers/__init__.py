"""Фабрика ИИ-провайдеров."""

from __future__ import annotations

from app.ai.providers.anthropic_provider import AnthropicProvider
from app.ai.providers.base import LLMProvider
from app.ai.providers.gemini_provider import GeminiProvider
from app.ai.providers.openai_provider import OpenAIProvider

PROVIDERS: dict[str, type[LLMProvider]] = {
    OpenAIProvider.name: OpenAIProvider,
    AnthropicProvider.name: AnthropicProvider,
    GeminiProvider.name: GeminiProvider,
}

PROVIDER_TITLES: dict[str, str] = {
    OpenAIProvider.name: OpenAIProvider.title,
    AnthropicProvider.name: AnthropicProvider.title,
    GeminiProvider.name: GeminiProvider.title,
}

PROVIDER_EMOJI: dict[str, str] = {
    OpenAIProvider.name: "🧠",
    AnthropicProvider.name: "🧠",
    GeminiProvider.name: "✨",
}


def create_provider(name: str, api_key: str) -> LLMProvider:
    """Создать провайдера по имени и ключу."""
    provider_cls = PROVIDERS.get(name)
    if provider_cls is None:
        raise ValueError(f"Неизвестный провайдер: {name}")
    if not api_key:
        raise ValueError(f"Не задан ключ для провайдера {name}")
    return provider_cls(api_key)


def provider_catalog() -> list[tuple[str, str, str]]:
    """Список (id, заголовок, эмодзи) для меню."""
    return [
        (provider_id, PROVIDER_TITLES[provider_id], PROVIDER_EMOJI.get(provider_id, "🤖"))
        for provider_id in PROVIDERS
    ]
