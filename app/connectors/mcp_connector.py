"""Коннектор MCP: подключение к внешним MCP-серверам по протоколу Model Context Protocol.

Поддерживаются три транспорта:
  * ``http``  — streamable HTTP (новый стандарт), например ElevenLabs: https://api.elevenlabs.io/v1/mcp
  * ``sse``   — старый SSE-транспорт
  * ``stdio`` — локальный процесс (например ``npx -y @elevenlabs/mcp-server``)

Инструменты сервера становятся обычными инструментами агента: модель видит их
в списке функций и может вызывать так же, как встроенные.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from app.ai.types import ToolSpec
from app.connectors.base import Connector, ToolContext, ToolResult

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")


@dataclass(slots=True)
class McpServerConfig:
    """Настройки одного MCP-сервера."""

    id: str
    mode: str = "http"  # http | sse | stdio
    url: str = ""
    token: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    name: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> McpServerConfig:
        args = raw.get("args") or []
        if isinstance(args, str):
            args = args.split()
        env = raw.get("env") or {}
        if not isinstance(env, dict):
            env = {}
        return cls(
            id=str(raw.get("id") or "mcp"),
            mode=str(raw.get("mode") or "http"),
            url=str(raw.get("url") or ""),
            token=str(raw.get("token") or ""),
            command=str(raw.get("command") or ""),
            args=[str(item) for item in args],
            env={str(k): str(v) for k, v in env.items()},
            name=str(raw.get("name") or raw.get("id") or "MCP"),
        )

    def resolved_env(self, secrets: dict[str, str]) -> dict[str, str]:
        """Подставить ключи пользователя в переменные окружения (поддерживает {{key}})."""

        def substitute(value: str) -> str:
            for name, secret in secrets.items():
                value = value.replace("{{" + name + "}}", secret)
            return value

        return {substitute(key): substitute(value) for key, value in self.env.items()}


@dataclass(slots=True)
class McpTool:
    """Инструмент, объявленный MCP-сервером."""

    server_id: str
    tool_name: str
    description: str
    schema: dict[str, Any]


class McpConnector(Connector):
    """Агрегатор внешних MCP-серверов."""

    id = "mcp"
    title = "MCP — внешние сервисы"
    emoji = "🔌"
    description = (
        "Подключение к любым MCP-серверам (ElevenLabs, HyperFrames, Zapier, свои сервисы). "
        "Их инструменты становятся доступны модели как обычные функции."
    )

    CONNECT_TIMEOUT = 15

    async def tools(self, ctx: ToolContext) -> list[ToolSpec]:
        servers = self._servers(ctx)
        if not servers:
            return []
        specs: list[ToolSpec] = []
        for server in servers:
            try:
                remote_tools = await asyncio.wait_for(
                    self._list_tools(server, ctx), timeout=self.CONNECT_TIMEOUT
                )
            except (TimeoutError, Exception):  # noqa: BLE001
                logger.info("MCP-сервер %s недоступен", server.id)
                continue
            for remote in remote_tools:
                specs.append(
                    ToolSpec(
                        name=self._tool_name(server, remote.tool_name),
                        description=(
                            f"[{server.name}] {remote.description or remote.tool_name}"
                        ).strip(),
                        parameters=remote.schema
                        or {"type": "object", "properties": {}},
                    )
                )
        return specs

    async def execute(
        self, ctx: ToolContext, tool_name: str, arguments: dict[str, Any]
    ) -> ToolResult:
        servers = self._servers(ctx)
        for server in servers:
            try:
                remote_tools = await asyncio.wait_for(
                    self._list_tools(server, ctx), timeout=self.CONNECT_TIMEOUT
                )
            except (TimeoutError, Exception):  # noqa: BLE001
                continue
            for remote in remote_tools:
                if self._tool_name(server, remote.tool_name) != tool_name:
                    continue
                return await self._call(server, remote, ctx, arguments)
        return ToolResult.error(f"Инструмент «{tool_name}» недоступен: сервер не отвечает.")

    # --- вспомогательное --------------------------------------------
    @staticmethod
    def _servers(ctx: ToolContext) -> list[McpServerConfig]:
        raw = ctx.options.get("mcp_servers") or []
        configs: list[McpServerConfig] = []
        for item in raw:
            if isinstance(item, dict):
                configs.append(McpServerConfig.from_dict(item))
        return configs

    @staticmethod
    def _tool_name(server: McpServerConfig, tool_name: str) -> str:
        slug = _NAME_RE.sub("_", f"{server.id}_{tool_name}")
        return f"mcp_{slug}"[:64]

    @staticmethod
    def _secrets(ctx: ToolContext) -> dict[str, str]:
        secrets: dict[str, str] = {}
        for name in ("elevenlabs_key", "openai_key", "anthropic_key", "gemini_key"):
            value = ctx.options.get(name)
            if value:
                secrets[name] = str(value)
        return secrets

    @asynccontextmanager
    async def _session(self, server: McpServerConfig, ctx: ToolContext) -> AsyncIterator[Any]:
        from mcp import ClientSession

        secrets = self._secrets(ctx)
        if server.mode == "stdio":
            import os

            from mcp.client.stdio import StdioServerParameters, stdio_client

            env = dict(os.environ)
            env.update(server.resolved_env(secrets))
            params = StdioServerParameters(
                command=server.command,
                args=server.args,
                env=env,
            )
            async with (
                stdio_client(params) as (read_stream, write_stream),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                yield session
            return

        from mcp.client.sse import sse_client
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        headers: dict[str, str] = {}
        if server.token:
            headers["Authorization"] = server.token
            if not server.token.lower().startswith("bearer "):
                headers["Authorization"] = f"Bearer {server.token}"

        if server.mode == "sse":
            async with (
                sse_client(server.url, headers=headers or None) as (read_stream, write_stream),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                yield session
            return

        http_client = create_mcp_http_client(headers=headers or None)
        async with streamable_http_client(
            server.url, http_client=http_client
        ) as (read_stream, write_stream), ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session

    async def _list_tools(
        self, server: McpServerConfig, ctx: ToolContext
    ) -> list[McpTool]:
        async with self._session(server, ctx) as session:
            listing = await session.list_tools()
        return [
            McpTool(
                server_id=server.id,
                tool_name=tool.name,
                description=getattr(tool, "description", "") or "",
                schema=dict(getattr(tool, "input_schema", None) or {}),
            )
            for tool in listing.tools
        ]

    async def _call(
        self,
        server: McpServerConfig,
        remote: McpTool,
        ctx: ToolContext,
        arguments: dict[str, Any],
    ) -> ToolResult:
        async with self._session(server, ctx) as session:
            result = await session.call_tool(remote.tool_name, arguments or {})
        texts: list[str] = []
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                texts.append(str(text))
        if not texts:
            structured = getattr(result, "structured_content", None)
            if structured:
                texts.append(str(structured))
        body = "\n".join(texts) or "Сервер не вернул текста."
        if getattr(result, "is_error", False):
            return ToolResult.error(f"[{server.name}] {body}"[:4000])
        return ToolResult.ok(f"[{server.name}] {body}"[:8000])

    def status_hint(self) -> str:
        return "MCP-серверы настраиваются в меню коннекторов."
