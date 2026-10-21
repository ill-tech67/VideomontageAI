"""Провайдер Google Gemini."""

from __future__ import annotations

from typing import Any

from google import genai
from google.genai import types

from app.ai.providers.base import LLMProvider
from app.ai.types import ChatTurn, LLMResponse, ToolCall, ToolSpec


class GeminiProvider(LLMProvider):
    name = "gemini"
    title = "Google Gemini"
    default_model = "gemini-3.8-flash"

    def __init__(self, api_key: str) -> None:
        super().__init__(api_key)
        self._client = genai.Client(api_key=api_key)

    @staticmethod
    def _to_contents(messages: list[ChatTurn]) -> list[types.Content]:
        contents: list[types.Content] = []
        for turn in messages:
            if turn.role == "user":
                contents.append(
                    types.Content(role="user", parts=[types.Part.from_text(text=turn.content)])
                )
            elif turn.role == "assistant":
                parts: list[types.Part] = []
                if turn.content:
                    parts.append(types.Part.from_text(text=turn.content))
                for call in turn.tool_calls:
                    parts.append(
                        types.Part(
                            function_call=types.FunctionCall(
                                id=call.id or None,
                                name=call.name,
                                args=call.arguments or {},
                            )
                        )
                    )
                if parts:
                    contents.append(types.Content(role="model", parts=parts))
            elif turn.role == "tool":
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                function_response=types.FunctionResponse(
                                    id=turn.tool_call_id or None,
                                    name=turn.name or "",
                                    response={"result": turn.content},
                                )
                            )
                        ],
                    )
                )
        return contents

    async def complete(
        self,
        *,
        system: str,
        messages: list[ChatTurn],
        tools: list[ToolSpec],
        model: str | None = None,
        temperature: float = 0.4,
    ) -> LLMResponse:
        config_kwargs: dict[str, Any] = {
            "system_instruction": system or None,
            "temperature": temperature,
            "max_output_tokens": 8192,
        }
        if tools:
            config_kwargs["tools"] = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=tool.name,
                            description=tool.description,
                            parameters_json_schema=tool.parameters,
                        )
                        for tool in tools
                    ]
                )
            ]

        response = await self._client.aio.models.generate_content(
            model=model or self.default_model,
            contents=self._to_contents(messages),  # type: ignore[arg-type]
            config=types.GenerateContentConfig(**config_kwargs),
        )

        text = ""
        try:
            text = (response.text or "").strip()
        except (ValueError, AttributeError):
            text = ""

        calls: list[ToolCall] = []
        for index, raw_call in enumerate(response.function_calls or []):
            calls.append(
                ToolCall(
                    id=getattr(raw_call, "id", "") or f"call_{index}",
                    name=str(raw_call.name or ""),
                    arguments=dict(raw_call.args or {}),
                )
            )

        return LLMResponse(text=text, tool_calls=calls, raw=response)

    async def list_models(self) -> list[str]:
        try:
            page = await self._client.aio.models.list()
        except Exception:  # noqa: BLE001
            return [self.default_model]
        names: set[str] = set()
        async for model in page:
            model_name = getattr(model, "name", None)
            if model_name and "generateContent" in (
                getattr(model, "supported_actions", None) or []
            ):
                names.add(str(model_name).split("/")[-1])
        return sorted(names) or [self.default_model]
