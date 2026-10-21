"""Промежуточные обработчики: загрузка пользователя и контроль доступа."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import session_scope
from app.db.models import User
from app.handlers.common import upsert_user
from app.services import consent as consent_service

logger = logging.getLogger(__name__)

ONBOARDING_STATES = {"Onboarding:captcha", "Onboarding:consent"}
FREE_CALLBACKS = ("consent:", "menu:about", "menu:help")
FREE_COMMANDS = ("/start", "/consent", "/captcha")

Handler = Callable[[TelegramObject, dict[str, object]], Awaitable[object]]


def _split_event(event: TelegramObject) -> tuple[Message | None, CallbackQuery | None]:
    """Достать сообщение и колбэк из апдейта.

    Middleware на уровне update получает объект Update, а не само сообщение.
    """
    if isinstance(event, Update):
        return event.message, event.callback_query
    if isinstance(event, Message):
        return event, None
    if isinstance(event, CallbackQuery):
        return None, event
    return None, None


class UserMiddleware(BaseMiddleware):
    """Кладёт в data объект User (ORM) и сессию БД."""

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, object],
    ) -> object:
        tg_user = data.get("event_from_user")
        if not isinstance(tg_user, TgUser):
            return await handler(event, data)

        async with session_scope() as session:
            user = await upsert_user(session, tg_user)
            data["session"] = session
            data["user"] = user
            data["user_id"] = user.id
            return await handler(event, data)


class AccessMiddleware(BaseMiddleware):
    """Пропускает только пользователей, прошедших капчу и принявших согласие."""

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, object],
    ) -> object:
        user = data.get("user")
        session = data.get("session")
        state = data.get("state")
        if not isinstance(user, User) or not isinstance(session, AsyncSession):
            return await handler(event, data)

        message, callback = _split_event(event)

        current_state = await state.get_state() if isinstance(state, FSMContext) else None
        if current_state in ONBOARDING_STATES:
            return await handler(event, data)

        if callback is not None and (callback.data or "").startswith(FREE_CALLBACKS):
            return await handler(event, data)

        if message is not None and message.text:
            command = message.text.split("@")[0].strip()
            if command in FREE_COMMANDS:
                return await handler(event, data)

        if not user.captcha_passed_at:
            await self._ask_captcha(message, callback)
            return None

        if not await consent_service.has_consent(session, user):
            await self._ask_consent(message, callback)
            return None

        return await handler(event, data)

    @staticmethod
    async def _ask_captcha(
        message: Message | None, callback: CallbackQuery | None
    ) -> None:
        if message is not None:
            await message.answer(
                "🧩 <b>Сначала пройдите капчу</b>\n\nОтправьте команду /start — "
                "бот покажет картинку с кодом.",
                parse_mode="HTML",
            )
        elif callback is not None:
            await callback.answer("Сначала пройдите капчу: отправьте /start", show_alert=True)

    @staticmethod
    async def _ask_consent(
        message: Message | None, callback: CallbackQuery | None
    ) -> None:
        from app.services.consent import consent_text
        from app.ui import keyboards, texts

        if message is not None:
            await message.answer(
                texts.CONSENT_HEADER + consent_text(),
                parse_mode="HTML",
                reply_markup=keyboards.consent_keyboard(),
            )
        elif callback is not None:
            await callback.answer(
                "Нужно принять согласие на передачу данных ИИ-провайдерам.",
                show_alert=True,
            )
