"""Коннекторы: включение, настройка ключей и MCP-серверов."""

from __future__ import annotations

import json
import logging
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.container import get_container
from app.db.models import User
from app.services.accounts import (
    get_connector_config,
    get_connector_configs,
    get_provider_key,
    set_connector_enabled,
    set_connector_options,
    set_provider_key,
)
from app.ui import keyboards
from app.ui.theme import Button, Style, build_keyboard

logger = logging.getLogger(__name__)

router = Router(name="connectors")

ELEVENLABS_HOSTED_MCP = "https://api.elevenlabs.io/v1/mcp"

MCP_PRESETS: dict[str, dict[str, Any]] = {
    "elevenlabs_hosted": {
        "id": "elevenlabs_hosted",
        "name": "ElevenLabs (облачный MCP)",
        "mode": "http",
        "url": ELEVENLABS_HOSTED_MCP,
        "token": "",
    },
    "elevenlabs_local": {
        "id": "elevenlabs_local",
        "name": "ElevenLabs (локальный MCP-сервер)",
        "mode": "stdio",
        "command": "npx",
        "args": ["-y", "@elevenlabs/mcp-server"],
        "env": {"ELEVENLABS_API_KEY": "{{elevenlabs_key}}"},
    },
    "hyperframes": {
        "id": "hyperframes",
        "name": "HyperFrames",
        "mode": "stdio",
        "command": "npx",
        "args": ["-y", "hyperframes-mcp-server"],
        "env": {},
    },
}


class ConnectorFlow(StatesGroup):
    """Состояния настройки коннекторов."""

    elevenlabs_key = State()
    mcp_url = State()
    mcp_token = State()
    mcp_stdio = State()


@router.callback_query(F.data == "menu:connectors")
async def on_connectors(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    """Список коннекторов."""
    await callback.answer()
    container = get_container()
    configs = await get_connector_configs(session, user.id)
    enabled = {cfg.connector_id for cfg in configs if cfg.enabled}

    lines = ["🔌 <b>Коннекторы</b>\n"]
    lines.append("Это внешние сервисы, которыми агент может пользоваться.\n")
    for connector in container.connectors():
        mark = "🟢" if connector.id in enabled else "⚪"
        lines.append(f"{mark} {connector.emoji} <b>{connector.title}</b>")
        lines.append(f"    {connector.description}")
    lines.append("\n🟢 включён · ⚪ выключен")

    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=keyboards.connectors_keyboard(container.connectors(), enabled),
    )


@router.callback_query(F.data.startswith("conn:toggle:"))
async def on_toggle(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    """Включить/выключить коннектор."""
    connector_id = (callback.data or "").split(":")[-1]
    config = await get_connector_config(session, user.id, connector_id)
    new_state = not (config.enabled if config else False)
    await set_connector_enabled(session, user.id, connector_id, new_state)
    await callback.answer("Включено 🟢" if new_state else "Выключено ⚪")
    container = get_container()
    configs = await get_connector_configs(session, user.id)
    enabled = {cfg.connector_id for cfg in configs if cfg.enabled}
    await callback.message.edit_reply_markup(
        reply_markup=keyboards.connectors_keyboard(container.connectors(), enabled)
    )


@router.callback_query(F.data.startswith("conn:info:"))
async def on_connector_info(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    """Подробности о коннекторе."""
    connector_id = (callback.data or "").split(":")[-1]
    await callback.answer()
    container = get_container()
    connector = container.registry.get(connector_id)
    if connector is None:
        await callback.message.edit_text("❌ Коннектор не найден.")
        return

    config = await get_connector_config(session, user.id, connector_id)
    enabled = bool(config and config.enabled)
    options = dict(config.options or {}) if config else {}

    lines = [
        f"{connector.emoji} <b>{connector.title}</b>\n",
        connector.description,
        f"\nСтатус: {'🟢 включён' if enabled else '⚪ выключен'}",
    ]

    rows: list[list[Button]] = []
    if connector_id == "elevenlabs":
        key = await get_provider_key(session, user.id, "elevenlabs")
        lines.append(f"\n🔑 Ключ ElevenLabs: {'подключён ✅' if key else 'не задан ⚠️'}")
        rows.append(
            [
                Button(
                    "🔑 Добавить ключ" if not key else "🔑 Заменить ключ",
                    "conn:elevenlabs:key",
                    Style.PRIMARY,
                )
            ]
        )
    elif connector_id == "mcp":
        servers = options.get("mcp_servers") or []
        lines.append(f"\n🔗 Подключено MCP-серверов: {len(servers)}")
        for server in servers:
            lines.append(
                f"   • {server.get('name', server.get('id'))} "
                f"[{server.get('mode')}] {server.get('url') or server.get('command', '')}"
            )
        rows.append([Button("➕ Подключить MCP-сервер", "conn:mcp:menu", Style.PRIMARY)])
        for index in range(len(servers)):
            rows.append(
                [
                    Button(
                        f"🗑 Отключить «{servers[index].get('name', index + 1)}»",
                        f"conn:mcp:del:{index}",
                        Style.DANGER,
                    )
                ]
            )
    elif connector_id == "ffmpeg":
        ffmpeg = container.runner.ffmpeg_binary()
        lines.append(f"\n⚙️ ffmpeg: {ffmpeg or 'не найден ⚠️'}")
    elif connector_id == "hyperframes":
        lines.append(
            f"\n⚙️ Режим рендера: {get_settings().render_mode}\n"
            f"⚙️ CLI: {get_settings().hyperframes_cli} "
            f"({get_settings().hyperframes_version or 'latest'})"
        )
        rows.append([Button("🧪 Проверить окружение", "menu:env", Style.INFO)])

    rows.append([Button("⬅️ К коннекторам", "menu:connectors", Style.NEUTRAL)])
    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=build_keyboard(rows),
    )


@router.callback_query(F.data == "conn:elevenlabs:key")
async def on_elevenlabs_key(callback: CallbackQuery, state: FSMContext) -> None:
    """Запрос ключа ElevenLabs."""
    await callback.answer()
    await state.set_state(ConnectorFlow.elevenlabs_key)
    await callback.message.edit_text(
        "🔑 <b>Ключ ElevenLabs</b>\n\n"
        "Пришлите API-ключ следующим сообщением. Он зашифруется и будет использован "
        "только для синтеза речи и расшифровки.",
        parse_mode="HTML",
    )


@router.message(ConnectorFlow.elevenlabs_key)
async def on_elevenlabs_key_received(
    message: Message, state: FSMContext, user: User, session: AsyncSession
) -> None:
    """Сохранить ключ ElevenLabs."""
    key = (message.text or "").strip()
    if len(key) < 10:
        await message.answer("⚠️ Ключ выглядит слишком коротким. Пришлите ещё раз.")
        return
    await set_provider_key(session, user.id, "elevenlabs", key)
    await state.clear()
    await message.answer(
        "✅ <b>Ключ ElevenLabs сохранён</b>\n\n"
        "Коннектор озвучки готов к работе.",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "conn:mcp:menu")
async def on_mcp_menu(callback: CallbackQuery) -> None:
    """Меню добавления MCP-сервера."""
    await callback.answer()
    rows = [
        [Button("☁️ Облачный HTTP-сервер", "conn:mcp:add:http", Style.PRIMARY)],
        [Button("💻 Локальный процесс (stdio)", "conn:mcp:add:stdio", Style.NEUTRAL)],
        [Button("⬅️ Назад", "menu:connectors", Style.NEUTRAL)],
    ]
    await callback.message.edit_text(
        "🔗 <b>Подключение MCP-сервера</b>\n\n"
        "<b>Облачный (HTTP)</b> — например ElevenLabs: "
        f"<code>{ELEVENLABS_HOSTED_MCP}</code>. "
        "При необходимости потребуется токен (Bearer).\n\n"
        "<b>Локальный (stdio)</b> — команда запуска сервера, например:\n"
        "<code>npx -y @elevenlabs/mcp-server</code>\n\n"
        "Готовые пресеты:",
        parse_mode="HTML",
        reply_markup=build_keyboard(rows),
    )


@router.callback_query(F.data.startswith("conn:mcp:add:"))
async def on_mcp_add(callback: CallbackQuery, state: FSMContext) -> None:
    """Добавить MCP-сервер (пресет или вручную)."""
    mode = (callback.data or "").split(":")[-1]
    await callback.answer()
    if mode in MCP_PRESETS:
        await callback.message.edit_text(
            f"✅ Пресет доступен в разделе настроек.\n\n"
            f"<b>{MCP_PRESETS[mode]['name']}</b>\n"
            f"Добавьте его через «➕ Подключить MCP-сервер» → ручное подключение.",
            parse_mode="HTML",
        )
        return
    if mode == "http":
        await state.set_state(ConnectorFlow.mcp_url)
        await state.update_data(mcp_mode="http")
        await callback.message.edit_text(
            "🔗 <b>URL MCP-сервера</b>\n\nПришлите адрес, например:\n"
            f"<code>{ELEVENLABS_HOSTED_MCP}</code>",
            parse_mode="HTML",
        )
        return
    if mode == "stdio":
        await state.set_state(ConnectorFlow.mcp_stdio)
        await state.update_data(mcp_mode="stdio")
        await callback.message.edit_text(
            "💻 <b>Команда запуска MCP-сервера</b>\n\n"
            "Пришлите команду целиком, например:\n"
            "<code>npx -y @elevenlabs/mcp-server</code>",
            parse_mode="HTML",
        )


@router.message(ConnectorFlow.mcp_url)
async def on_mcp_url(
    message: Message, state: FSMContext, user: User, session: AsyncSession
) -> None:
    """Сохранить URL MCP-сервера."""
    url = (message.text or "").strip()
    if not url.startswith(("http://", "https://")):
        await message.answer("⚠️ Нужен полный URL (http:// или https://).")
        return
    await state.set_state(ConnectorFlow.mcp_token)
    await state.update_data(mcp_url=url)
    await message.answer(
        "🔑 <b>Токен доступа</b>\n\n"
        "Если сервер требует авторизацию — пришлите токен (Bearer). "
        "Если не требует — отправьте «-».",
        parse_mode="HTML",
    )


@router.message(ConnectorFlow.mcp_token)
async def on_mcp_token(
    message: Message, state: FSMContext, user: User, session: AsyncSession
) -> None:
    """Сохранить MCP-сервер с токеном."""
    data = await state.get_data()
    token = (message.text or "").strip()
    if token == "-":
        token = ""
    server = {
        "id": f"mcp{len(await _mcp_servers(session, user.id)) + 1}",
        "name": (await _mcp_servers(session, user.id))[-1].get("url", "MCP")
        if await _mcp_servers(session, user.id)
        else "MCP",
        "mode": "http",
        "url": str(data.get("mcp_url") or ""),
        "token": token,
    }
    await _append_mcp_server(session, user.id, server)
    await state.clear()
    await message.answer(
        f"✅ <b>MCP-сервер подключён</b>\n\n<code>{server['url']}</code>\n\n"
        "Инструменты сервера станут доступны модели на следующем задании.",
        parse_mode="HTML",
    )


@router.message(ConnectorFlow.mcp_stdio)
async def on_mcp_stdio(
    message: Message, state: FSMContext, user: User, session: AsyncSession
) -> None:
    """Сохранить stdio MCP-сервер."""
    command_line = (message.text or "").strip()
    if not command_line:
        await message.answer("⚠️ Пришлите команду запуска сервера.")
        return
    parts = command_line.split()
    server = {
        "id": f"mcp{len(await _mcp_servers(session, user.id)) + 1}",
        "name": " ".join(parts[:2]),
        "mode": "stdio",
        "command": parts[0],
        "args": parts[1:],
        "env": {},
    }
    await _append_mcp_server(session, user.id, server)
    await state.clear()
    await message.answer(
        f"✅ <b>MCP-сервер подключён</b>\n\n<code>{command_line}</code>\n\n"
        "Инструменты сервера станут доступны модели на следующем задании.",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("conn:mcp:del:"))
async def on_mcp_delete(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    """Удалить MCP-сервер."""
    index = int((callback.data or "").split(":")[-1])
    servers = await _mcp_servers(session, user.id)
    if 0 <= index < len(servers):
        servers.pop(index)
        await set_connector_options(session, user.id, "mcp", {"mcp_servers": servers})
    await callback.answer("Сервер отключён 🗑")
    await callback.message.edit_text(
        "🗑 MCP-сервер отключён.", parse_mode="HTML"
    )


# --- вспомогательное --------------------------------------------
async def _mcp_servers(session: AsyncSession, user_id: int) -> list[dict[str, Any]]:
    config = await get_connector_config(session, user_id, "mcp")
    if config is None:
        return []
    servers = (config.options or {}).get("mcp_servers") or []
    return list(servers)


async def _append_mcp_server(
    session: AsyncSession, user_id: int, server: dict[str, Any]
) -> None:
    servers = await _mcp_servers(session, user_id)
    servers.append(server)
    await set_connector_options(session, user_id, "mcp", {"mcp_servers": servers})


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
