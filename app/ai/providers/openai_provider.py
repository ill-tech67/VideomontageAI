"""Провайдер OpenAI (GPT)."""

from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from app.ai.providers.base import LLMProvider
from app.ai.types import ChatTurn, LLMResponse, ToolCall, ToolSpec


class OpenAIProvider(LLMProvider):
    name = "openai"
    title = "OpenAI GPT"
    default_model = "gpt-6.1-sol"

    def __init__(self, api_key: str) -> None:
        super().__init__(api_key)
        self._client = AsyncOpenAI(api_key=api_key)

    @staticmethod
    def _to_messages(messages: list[ChatTurn]) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for turn in messages:
            if turn.role == "user":
                payload.append({"role": "user", "content": turn.content})
            elif turn.role == "assistant":
                message: dict[str, Any] = {"role": "assistant", "content": turn.content or None}
                if turn.tool_calls:
                    message["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments, ensure_ascii=False),
                            },
                        }
                        for call in turn.tool_calls
                    ]
                payload.append(message)
            elif turn.role == "tool":
                payload.append(
                    {
                        "role": "tool",
                        "tool_call_id": turn.tool_call_id or "",
                        "content": turn.content,
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
        payload: list[dict[str, Any]] = []
        if system:
            payload.append({"role": "system", "content": system})
        payload.extend(self._to_messages(messages))

        kwargs: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": payload,
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]
        if temperature is not None:
            kwargs["temperature"] = temperature

        completion = await self._client.chat.completions.create(**kwargs)
        choice = completion.choices[0]
        message = choice.message

        calls: list[ToolCall] = []
        for raw_call in message.tool_calls or []:
            try:
                arguments = json.loads(raw_call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            calls.append(
                ToolCall(
                    id=raw_call.id,
                    name=raw_call.function.name,
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )

        return LLMResponse(text=(message.content or "").strip(), tool_calls=calls, raw=completion)

    async def list_models(self) -> list[str]:
        try:
            page = await self._client.models.list()
        except Exception:  # noqa: BLE001 - откатываемся к дефолтам
            return [self.default_model]
        names = sorted({model.id for model in page.data if model.id.startswith(("gpt", "o"))})
        return names or [self.default_model]
