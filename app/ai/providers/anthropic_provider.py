"""Провайдер Anthropic (Claude)."""

from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic

from app.ai.providers.base import LLMProvider
from app.ai.types import ChatTurn, LLMResponse, ToolCall, ToolSpec


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    title = "Anthropic Claude"
    default_model = "claude-opus-5-5"

    def __init__(self, api_key: str) -> None:
        super().__init__(api_key)
        self._client = AsyncAnthropic(api_key=api_key)

    @staticmethod
    def _to_messages(messages: list[ChatTurn]) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for turn in messages:
            if turn.role == "user":
                payload.append({"role": "user", "content": [{"type": "text", "text": turn.content}]})
            elif turn.role == "assistant":
                blocks: list[dict[str, Any]] = []
                if turn.content:
                    blocks.append({"type": "text", "text": turn.content})
                for call in turn.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call.id,
                            "name": call.name,
                            "input": call.arguments,
                        }
                    )
                if blocks:
                    payload.append({"role": "assistant", "content": blocks})
            elif turn.role == "tool":
                payload.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": turn.tool_call_id or "",
                                "content": turn.content,
                            }
                        ],
                    }
                )
        return payload

    async def complete(
        self,
        *,
        system: str,
        messages: list[ChatTurn],
        tools: list[ToolSpec],
        model: str | None = None,
        temperature: float = 0.4,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": model or self.default_model,
            "max_tokens": 8192,
            "messages": self._to_messages(messages),
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters,
                }
                for tool in tools
            ]
        if temperature is not None:
            kwargs["temperature"] = temperature

        message = await self._client.messages.create(**kwargs)

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in message.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(getattr(block, "text", ""))
            elif block_type == "tool_use":
                calls.append(
                    ToolCall(
                        id=getattr(block, "id", ""),
                        name=getattr(block, "name", ""),
                        arguments=dict(getattr(block, "input", {}) or {}),
                    )
                )

        return LLMResponse(text="\n".join(text_parts).strip(), tool_calls=calls, raw=message)

    async def list_models(self) -> list[str]:
        try:
            page = await self._client.models.list()
        except Exception:  # noqa: BLE001
            return [self.default_model]
        names = sorted({model.id for model in page.data if model.id.startswith("claude")})
        return names or [self.default_model]
