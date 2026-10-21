"""Подключение ИИ-провайдеров: ключи, модели, модель по умолчанию."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers import PROVIDER_TITLES, create_provider, provider_catalog
from app.config import get_settings
from app.db.models import User
from app.services.accounts import (
    delete_provider_key,
    get_provider_key,
    set_provider_key,
)
from app.ui import texts
from app.ui.theme import Button, Style, build_keyboard

logger = logging.getLogger(__name__)

router = Router(name="ai_menu")


class AIFlow(StatesGroup):
    """Состояния подключения ИИ."""

    key = State()
    model_choice = State()


@router.callback_query(F.data == "menu:ai")
async def on_ai_menu(callback: CallbackQuery, user: User, session: AsyncSession) -> None:
    """Список провайдеров."""
    await callback.answer()
    lines = ["🤖 <b>Подключить ИИ</b>\n"]
    for provider_id, title, emoji in provider_catalog():
        key = await get_provider_key(session, user.id, provider_id)
        mark = "🟢" if key else "⚪"
        source = "ваш ключ" if key else "не подключён"
        if not key and get_settings().server_key(provider_id):
            source = "ключ сервера"
        lines.append(f"{mark} {emoji} <b>{title}</b> — {source}")
    lines.append(
        "\nКлюч хранится в базе в зашифрованном виде (Fernet) и используется "
        "только для ваших запросов.\n\nВыберите провайдера:"
    )
    rows = [
        [
            Button(
                f"{emoji} {title}",
                f"ai:provider:{provider_id}",
                Style.OK if await get_provider_key(session, user.id, provider_id) else Style.NEUTRAL,
            )
        ]
        for provider_id, title, emoji in provider_catalog()
    ]
    rows.append([Button("🏠 В главное меню", "menu:home", Style.NEUTRAL)])
    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=build_keyboard(rows),
    )


@router.callback_query(F.data.startswith("ai:provider:"))
async def on_provider(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    """Карточка провайдера."""
    provider_id = (callback.data or "").split(":")[-1]
    await callback.answer()
    if provider_id not in PROVIDER_TITLES:
        await callback.message.edit_text("❌ Неизвестный провайдер.")
        return

    key = await get_provider_key(session, user.id, provider_id)
    server_key = get_settings().server_key(provider_id)
    is_default = user.default_provider == provider_id

    lines = [f"🧠 <b>{PROVIDER_TITLES[provider_id]}</b>\n"]
    if key:
        origin = "ваш ключ" if key != server_key else "ключ сервера"
        lines.append(f"🔑 Ключ подключён ({origin})")
    else:
        lines.append("🔑 Ключ не подключён")
    if is_default:
        lines.append("⭐ Используется по умолчанию")

    rows = [
        [
            Button("🔑 Добавить ключ", f"ai:key:add:{provider_id}", Style.PRIMARY),
            Button("🧪 Проверить", f"ai:key:test:{provider_id}", Style.INFO),
        ],
        [
            Button("🎚 Модели", f"ai:models:{provider_id}", Style.NEUTRAL),
            Button(
                "⭐ По умолчанию" if not is_default else "⭐ По умолчанию ✓",
                f"ai:default:{provider_id}",
                Style.OK if is_default else Style.NEUTRAL,
            ),
        ],
    ]
    if key and key != server_key:
        rows.append([Button("🗑 Удалить ключ", f"ai:key:del:{provider_id}", Style.DANGER)])
    rows.append([Button("🏠 В главное меню", "menu:home", Style.NEUTRAL)])

    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=build_keyboard(rows),
    )


@router.callback_query(F.data.startswith("ai:key:add:"))
async def on_ask_key(callback: CallbackQuery, state: FSMContext) -> None:
    """Запросить API-ключ."""
    provider_id = (callback.data or "").split(":")[-1]
    await callback.answer()
    await state.set_state(AIFlow.key)
    await state.update_data(provider=provider_id)
    await callback.message.edit_text(
        f"🔑 <b>Ключ {PROVIDER_TITLES.get(provider_id, provider_id)}</b>\n\n"
        "Пришлите API-ключ следующим сообщением. Он будет зашифрован и сохранён "
        "в вашей профиле.\n\n"
        "⚠️ Ключ не должен попадать в канал или группу — пишите в личку.",
        parse_mode="HTML",
    )


@router.message(AIFlow.key)
async def on_key_received(
    message: Message, state: FSMContext, user: User, session: AsyncSession
) -> None:
    """Сохранить и проверить ключ."""
    data = await state.get_data()
    provider_id = str(data.get("provider") or "")
    key = (message.text or "").strip()

    if not key or len(key) < 10:
        await message.answer("⚠️ Ключ выглядит слишком коротким. Пришлите его ещё раз.")
        return

    ok, detail = await _check_key(provider_id, key)
    if not ok:
        await message.answer(
            texts.AI_KEY_INVALID.format(error=detail[:300]), parse_mode="HTML"
        )
        return

    await set_provider_key(session, user.id, provider_id, key)
    await state.clear()
    await message.answer(
        texts.AI_KEY_SAVED.format(
            provider=PROVIDER_TITLES.get(provider_id, provider_id), check=detail
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("ai:key:test:"))
async def on_test_key(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    """Проверить ключ."""
    provider_id = (callback.data or "").split(":")[-1]
    await callback.answer("Проверяю…")
    key = await get_provider_key(session, user.id, provider_id)
    if not key:
        await callback.message.edit_text(
            "⚠️ Ключ не найден. Добавьте его через «🔑 Добавить ключ».",
            parse_mode="HTML",
        )
        return
    ok, detail = await _check_key(provider_id, key)
    mark = "✅" if ok else "❌"
    await callback.message.edit_text(
        f"{mark} <b>{PROVIDER_TITLES.get(provider_id, provider_id)}</b>\n\n{detail[:600]}",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("ai:key:del:"))
async def on_delete_key(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    """Удалить ключ."""
    provider_id = (callback.data or "").split(":")[-1]
    await delete_provider_key(session, user.id, provider_id)
    await callback.answer("Ключ удалён 🗑")
    await callback.message.edit_text(
        f"🗑 Ключ {PROVIDER_TITLES.get(provider_id, provider_id)} удалён.",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("ai:default:"))
async def on_set_default(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    """Модель по умолчанию."""
    provider_id = (callback.data or "").split(":")[-1]
    key = await get_provider_key(session, user.id, provider_id)
    if not key:
        await callback.answer("Сначала добавьте ключ", show_alert=True)
        return
    user.default_provider = provider_id
    session.add(user)
    await callback.answer("Готово ⭐")
    await callback.message.edit_text(
        f"⭐ Модель по умолчанию: <b>{PROVIDER_TITLES.get(provider_id, provider_id)}</b>",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("ai:models:"))
async def on_models(callback: CallbackQuery, user: User, session: AsyncSession) -> None:
    """Список моделей провайдера."""
    provider_id = (callback.data or "").split(":")[-1]
    await callback.answer("Получаю список моделей…")
    key = await get_provider_key(session, user.id, provider_id)
    if not key:
        await callback.message.edit_text(
            "⚠️ Сначала добавьте ключ.", parse_mode="HTML"
        )
        return
    try:
        provider = create_provider(provider_id, key)
        models = await provider.list_models()
    except Exception as exc:  # noqa: BLE001
        await callback.message.edit_text(f"❌ Не удалось получить модели: {exc}"[:900])
        return

    rows = []
    for model in models[:24]:
        rows.append([Button(model, f"ai:model:set:{provider_id}:{model}", Style.NEUTRAL)])
    rows.append([Button("🏠 В главное меню", "menu:home", Style.NEUTRAL)])
    await callback.message.edit_text(
        f"🎚 <b>Модели {PROVIDER_TITLES.get(provider_id, provider_id)}</b>\n\n"
        "Выберите модель по умолчанию для этого провайдера:",
        parse_mode="HTML",
        reply_markup=build_keyboard(rows),
    )


@router.callback_query(F.data.startswith("ai:model:set:"))
async def on_model_set(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    """Выбрать конкретную модель."""
    parts = (callback.data or "").split(":")
    provider_id = parts[2] if len(parts) > 2 else ""
    model = ":".join(parts[3:]) if len(parts) > 3 else ""
    settings = await state_user_settings(session, user)
    settings.setdefault("models", {})[provider_id] = model
    user.settings = settings
    session.add(user)
    await callback.answer("Модель выбрана 🎚")
    await callback.message.edit_text(
        f"🎚 Модель <b>{PROVIDER_TITLES.get(provider_id, provider_id)}</b>: "
        f"<code>{model}</code>",
        parse_mode="HTML",
    )


async def state_user_settings(session: AsyncSession, user: User) -> dict:
    """Вернуть dict-настройки пользователя (JSON в SQLite)."""
    return dict(user.settings or {})


async def _check_key(provider_id: str, key: str) -> tuple[bool, str]:
    """Проверить ключ вызовом модели."""
    try:
        provider = create_provider(provider_id, key)
    except ValueError as exc:
        return False, str(exc)
    try:
        return await provider.check_key()
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"[:400]
