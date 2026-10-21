"""Тесты агентного цикла и конвертации сообщений между провайдерами."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.ai.agent import AgentRunner
from app.ai.providers.anthropic_provider import AnthropicProvider
from app.ai.providers.gemini_provider import GeminiProvider
from app.ai.providers.openai_provider import OpenAIProvider
from app.ai.types import ChatTurn, LLMResponse, ToolCall, ToolSpec
from app.connectors.base import ToolContext
from app.connectors.registry import ConnectorRegistry
from app.connectors.workspace import WorkspaceConnector


class FakeProvider:
    """Провайдер-заглушка: отдаёт заранее заданные ответы."""

    name = "fake"

    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def complete(self, *, system, messages, tools, model=None, temperature=0.4):
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        if self.responses:
            return self.responses.pop(0)
        return LLMResponse(text="готово")


def make_ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(user_id=1, chat_id=1, project_dir=tmp_path, options={})


async def test_agent_calls_tool_then_finishes(tmp_path: Path):
    registry = ConnectorRegistry()
    registry.register(WorkspaceConnector())

    provider = FakeProvider(
        [
            LLMResponse(
                text="пишу файл",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="workspace_write_file",
                        arguments={"path": "story.txt", "content": "сюжет"},
                    )
                ],
            ),
            LLMResponse(text="Готово, ролик собран ✅"),
        ]
    )

    ctx = make_ctx(tmp_path)
    runner = AgentRunner(provider, registry, ctx, max_steps=5)
    tools = await registry.build_tools(ctx, ["workspace"], lambda _cid: True)

    result = await runner.run(system="система", history=[], user_text="сделай ролик", tools=tools)

    assert result.text.startswith("Готово")
    assert result.steps == 2
    assert (tmp_path / "story.txt").read_text(encoding="utf-8") == "сюжет"
    assert len(provider.calls) == 2


async def test_agent_reports_tool_error_to_model(tmp_path: Path):
    registry = ConnectorRegistry()
    registry.register(WorkspaceConnector())

    provider = FakeProvider(
        [
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="workspace_write_file",
                        arguments={"path": "../evil.txt", "content": "x"},
                    )
                ]
            ),
            LLMResponse(text="Понял, исправлю"),
        ]
    )

    ctx = make_ctx(tmp_path)
    runner = AgentRunner(provider, registry, ctx, max_steps=5)
    tools = await registry.build_tools(ctx, ["workspace"], lambda _cid: True)
    result = await runner.run(system="s", history=[], user_text="x", tools=tools)

    assert result.text == "Понял, исправлю"
    second_call = provider.calls[1]["messages"]
    assert any(turn.role == "tool" for turn in second_call)


async def test_agent_stops_at_max_steps(tmp_path: Path):
    registry = ConnectorRegistry()
    registry.register(WorkspaceConnector())

    provider = FakeProvider(
        [
            LLMResponse(
                tool_calls=[
                    ToolCall(id=f"c{i}", name="workspace_list_assets", arguments={})
                ]
            )
            for i in range(10)
        ]
    )

    ctx = make_ctx(tmp_path)
    runner = AgentRunner(provider, registry, ctx, max_steps=3)
    tools = await registry.build_tools(ctx, ["workspace"], lambda _cid: True)
    result = await runner.run(system="s", history=[], user_text="x", tools=tools)

    assert result.steps == 3
    assert result.error


def test_openai_message_conversion():
    messages = [
        ChatTurn(role="user", content="привет"),
        ChatTurn(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="1", name="tool_a", arguments={"x": 1})],
        ),
        ChatTurn(role="tool", content="ok", tool_call_id="1", name="tool_a"),
    ]
    payload = OpenAIProvider._to_messages(messages)
    assert payload[0] == {"role": "user", "content": "привет"}
    assert payload[1]["tool_calls"][0]["function"]["name"] == "tool_a"
    assert payload[2]["role"] == "tool"


def test_anthropic_message_conversion():
    messages = [
        ChatTurn(role="user", content="привет"),
        ChatTurn(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="1", name="tool_a", arguments={"x": 1})],
        ),
        ChatTurn(role="tool", content="ok", tool_call_id="1", name="tool_a"),
    ]
    payload = AnthropicProvider._to_messages(messages)
    assert payload[0]["content"][0]["text"] == "привет"
    assert payload[1]["content"][0]["type"] == "tool_use"
    assert payload[2]["content"][0]["type"] == "tool_result"


def test_gemini_contents_conversion():
    messages = [
        ChatTurn(role="user", content="привет"),
        ChatTurn(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="1", name="tool_a", arguments={"x": 1})],
        ),
        ChatTurn(role="tool", content="ok", tool_call_id="1", name="tool_a"),
    ]
    contents = GeminiProvider._to_contents(messages)
    assert contents[0].role == "user"
    assert contents[1].role == "model"
    assert contents[1].parts[0].function_call.name == "tool_a"
    assert contents[2].parts[0].function_response.name == "tool_a"


def test_tool_spec_defaults():
    spec = ToolSpec(name="x", description="y")
    assert spec.parameters["type"] == "object"
