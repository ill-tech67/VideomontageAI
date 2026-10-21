"""Общие помощники для хендлеров."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.services.banner import get_banner
from app.ui import keyboards, texts

logger = logging.getLogger(__name__)


def display_name(user: User) -> str:
    """Имя для приветствия."""
    if user.first_name:
        return user.first_name
    if user.username:
        return f"@{user.username}"
    return "друг"


async def upsert_user(session: AsyncSession, tg_user: Any) -> User:
    """Найти или создать пользователя."""
    user = await session.get(User, tg_user.id)
    if user is None:
        user = User(
            id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name or "",
            last_name=tg_user.last_name,
            language_code=tg_user.language_code,
        )
        session.add(user)
    else:
        user.username = tg_user.username
        user.first_name = tg_user.first_name or user.first_name
        user.last_name = tg_user.last_name
        user.language_code = tg_user.language_code
        session.add(user)
    return user


async def send_home(bot: Bot, chat_id: int, user: User) -> Message:
    """Показать главное меню: баннер + кнопки."""
    caption = texts.MAIN_MENU.format(name=display_name(user))
    markup = keyboards.main_menu()
    banner = get_banner()

    home_id = (user.settings or {}).get("home_message_id")
    if home_id:
        try:
            await bot.edit_message_caption(
                caption=caption,
                reply_markup=markup,
                chat_id=chat_id,
                message_id=home_id,
            )
            return None  # type: ignore[return-value]
        except TelegramBadRequest:
            pass
        except Exception:  # noqa: BLE001
            logger.debug("Не удалось обновить баннер", exc_info=True)

    message = await bot.send_photo(
        chat_id,
        photo=FSInputFile(banner),
        caption=caption,
        reply_markup=markup,
    )
    user.settings = {**(user.settings or {}), "home_message_id": message.message_id}
    return message


async def edit_or_answer(
    message: Message,
    text: str,
    reply_markup: Any = None,
    parse_mode: str | None = "HTML",
) -> None:
    """Отредактировать сообщение, а если нельзя — отправить новое."""
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest:
        await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:  # noqa: BLE001
        logger.debug("Не удалось отредактировать сообщение", exc_info=True)


def split_message(text: str, limit: int = 4000) -> list[str]:
    """Разбить длинный текст на части (лимит Telegram — 4096 символов)."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) > limit:
            if current:
                parts.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts
