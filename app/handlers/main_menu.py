"""Главное меню и справочные разделы."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.config import get_settings
from app.container import get_container
from app.db.models import User
from app.handlers.common import send_home
from app.services import consent as consent_service
from app.services.accounts import get_connector_configs, list_provider_keys
from app.ui import keyboards, texts
from app.ui.theme import Button, Style, build_keyboard, legend

logger = logging.getLogger(__name__)

router = Router(name="main_menu")


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext, user: User) -> None:
    """Вернуться в главное меню."""
    await state.clear()
    await send_home(message.bot, message.chat.id, user)


@router.callback_query(F.data == "menu:home")
async def on_home(callback: CallbackQuery, user: User) -> None:
    await callback.answer()
    await send_home(callback.bot, callback.message.chat.id, user)


@router.callback_query(F.data == "menu:about")
async def on_about(callback: CallbackQuery) -> None:
    """О проекте."""
    await callback.answer()
    await callback.message.edit_text(
        texts.ABOUT.format(
            name="VideoTG",
            tagline=texts.APP_TAGLINE,
            legend=legend(),
            version=__version__,
            consent=get_settings().consent_version,
        ),
        parse_mode="HTML",
        reply_markup=keyboards.back_to_menu(),
    )


@router.callback_query(F.data == "menu:help")
async def on_help(callback: CallbackQuery) -> None:
    """Справка."""
    await callback.answer()
    await callback.message.edit_text(
        texts.HELP, parse_mode="HTML", reply_markup=keyboards.back_to_menu()
    )


@router.callback_query(F.data == "menu:skills")
async def on_skills(callback: CallbackQuery) -> None:
    """Список скиллов."""
    await callback.answer()
    container = get_container()
    skills = container.skills.all()
    lines = ["🧩 <b>Скиллы — готовые сценарии монтажа</b>\n"]
    for skill in skills:
        lines.append(f"{skill.emoji} <b>{skill.title}</b> — {skill.description}")
        lines.append(
            f"    кадр {skill.defaults.get('width')}×{skill.defaults.get('height')}"
            f" · ~{int(skill.defaults.get('duration', 30))} с"
        )
    lines.append("\nВыберите скилл — он задаст формат и правила для модели.")
    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=keyboards.skills_keyboard(skills),
    )


@router.callback_query(F.data == "menu:settings")
async def on_settings(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    """Настройки пользователя."""
    await callback.answer()
    settings = get_settings()
    container = get_container()

    keys = await list_provider_keys(session, user.id)
    configs = await get_connector_configs(session, user.id)
    enabled = [cfg.connector_id for cfg in configs if cfg.enabled]

    lines = ["⚙️ <b>Настройки</b>\n"]
    lines.append(f"🧠 Модель по умолчанию: <b>{user.default_provider or 'не выбрана'}</b>")
    lines.append(f"🎨 Скилл по умолчанию: <b>{user.default_skill or 'не выбран'}</b>")

    if keys:
        lines.append("\n<b>Ключи ИИ</b>")
        for key in keys:
            lines.append(f"🔑 {key.provider} — подключён")
    else:
        lines.append("\n🔑 Ключи ИИ не подключены")

    lines.append("\n<b>Коннекторы</b>")
    for connector in container.connectors():
        mark = "🟢" if connector.id in enabled else "⚪"
        lines.append(f"{mark} {connector.emoji} {connector.title}")

    consent_ok = await consent_service.has_consent(session, user)
    lines.append(
        f"\n📜 Согласие: {'принято ✅' if consent_ok else 'не принято ⚠️'} "
        f"(версия {settings.consent_version})"
    )

    rows = [
        [Button("Проверить окружение", "menu:env", Style.INFO)],
        [Button("Отозвать согласие", "settings:revoke", Style.DANGER)],
        [Button("🏠 В главное меню", "menu:home", Style.NEUTRAL)],
    ]
    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=build_keyboard(rows),
    )


@router.callback_query(F.data == "menu:env")
async def on_env(callback: CallbackQuery) -> None:
    """Проверка окружения рендера."""
    await callback.answer("Проверяю окружение…")
    container = get_container()
    payload = await container.runner.doctor()
    checks = payload.get("checks", []) if isinstance(payload, dict) else []
    lines = []
    for check in checks:
        mark = "✅" if check.get("ok") else "⚠️"
        lines.append(f"{mark} {check.get('name')}: {check.get('detail')}")
    ffmpeg = container.runner.ffmpeg_binary()
    lines.insert(0, f"ffmpeg: {ffmpeg or 'не найден'}")
    header = "✅ <b>Окружение готово</b>" if payload.get("ok") else "⚠️ <b>Окружение</b>"
    await callback.message.edit_text(
        f"{header}\n\n" + "\n".join(lines[:24]),
        parse_mode="HTML",
        reply_markup=keyboards.back_to_menu(),
    )


@router.callback_query(F.data == "settings:revoke")
async def on_revoke(callback: CallbackQuery, user: User, session: AsyncSession) -> None:
    """Отзыв согласия."""
    await consent_service.revoke_consent(session, user)
    await callback.answer("Согласие отозвано")
    await callback.message.edit_text(
        "⛔ <b>Согласие отозвано</b>\n\n"
        "ИИ-функции недоступны, пока согласие не принято снова. "
        "Нажмите /start, чтобы вернуться.",
        parse_mode="HTML",
        reply_markup=keyboards.back_to_menu(),
    )
