"""Онбординг: капча и согласие на передачу данных третьим лицам."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import CaptchaAttempt, User
from app.handlers.common import send_home
from app.security.captcha import generate_captcha
from app.security.crypto import verify_captcha
from app.services import consent as consent_service
from app.ui import keyboards, texts

logger = logging.getLogger(__name__)

router = Router(name="onboarding")

BLOCK_MINUTES = 10


class Onboarding(StatesGroup):
    """Шаги онбординга."""

    captcha = State()
    consent = State()


async def _send_captcha(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Показать новую капчу.

    Ответ капчи нигде не сохраняется: в базу уходит только HMAC-хеш, и картинка
    не уходит ни в один внешний сервис — бот не может прочитать её сам.
    """
    settings = get_settings()
    captcha = generate_captcha(length=settings.captcha_length)
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.captcha_ttl_seconds)

    stale = await session.scalars(
        select(CaptchaAttempt).where(CaptchaAttempt.user_id == message.from_user.id)
    )
    for attempt in stale:
        await session.delete(attempt)
    session.add(
        CaptchaAttempt(
            user_id=message.from_user.id,
            code_hash=captcha.code_hash,
            expires_at=expires_at,
        )
    )
    await session.flush()

    await state.set_state(Onboarding.captcha)
    await message.answer_photo(
        photo=BufferedInputFile(captcha.image, filename="captcha.png"),
        caption=texts.CAPTCHA_INTRO.format(
            length=settings.captcha_length,
            ttl=settings.captcha_ttl_seconds // 60 or 1,
        ),
        parse_mode="HTML",
        reply_markup=keyboards.cancel_keyboard(),
    )


@router.message(CommandStart())
async def cmd_start(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    """Точка входа: капча → согласие → главное меню."""
    if not user.captcha_passed_at:
        await _send_captcha(message, state, session)
        return

    if not await consent_service.has_consent(session, user):
        await _send_consent(message, state)
        return

    await send_home(message.bot, message.chat.id, user)
    await state.clear()


@router.message(Onboarding.captcha)
async def on_captcha_input(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    """Проверка ответа капчи."""
    settings = get_settings()
    answer = (message.text or "").strip()

    if user.is_blocked:
        await message.answer(
            texts.CAPTCHA_BLOCKED.format(minutes=BLOCK_MINUTES), parse_mode="HTML"
        )
        return

    attempt = await session.scalar(
        select(CaptchaAttempt)
        .where(CaptchaAttempt.user_id == user.id)
        .order_by(CaptchaAttempt.id.desc())
    )
    if attempt is None:
        await _send_captcha(message, state, session)
        return

    expired = attempt.expires_at.replace(tzinfo=UTC) < datetime.now(UTC)
    correct = (not expired) and verify_captcha(answer, attempt.code_hash)

    if correct:
        user.captcha_passed_at = datetime.now(UTC)
        session.add(user)
        await session.delete(attempt)
        if not await consent_service.has_consent(session, user):
            await _send_consent(message, state)
            return
        await send_home(message.bot, message.chat.id, user)
        await state.clear()
        return

    attempt.attempts += 1
    session.add(attempt)
    if attempt.attempts >= settings.captcha_max_attempts:
        user.is_blocked = True
        user.blocked_reason = "captcha"
        session.add(user)
        await state.clear()
        await message.answer(
            texts.CAPTCHA_BLOCKED.format(minutes=BLOCK_MINUTES), parse_mode="HTML"
        )
        return

    left = settings.captcha_max_attempts - attempt.attempts
    await message.answer(texts.CAPTCHA_WRONG.format(left=left), parse_mode="HTML")
    await _send_captcha(message, state, session)


async def _send_consent(message: Message, state: FSMContext) -> None:
    """Показать текст согласия."""
    await state.set_state(Onboarding.consent)
    await message.answer(
        texts.CONSENT_HEADER + consent_service.consent_text(),
        parse_mode="HTML",
        reply_markup=keyboards.consent_keyboard(),
    )


@router.callback_query(F.data.startswith("consent:"))
async def on_consent(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    """Принятие или отказ от согласия."""
    action = (callback.data or "").split(":", 1)[-1]
    if action == "accept":
        await consent_service.accept_consent(
            session,
            user,
            ip=None,
            user_agent=callback.from_user.language_code,
        )
        await callback.answer("Согласие принято ✅")
        await callback.message.edit_text(
            texts.CONSENT_ACCEPTED.format(
                version=get_settings().consent_version,
                date=datetime.now(UTC).strftime("%d.%m.%Y"),
            ),
            parse_mode="HTML",
        )
        await send_home(callback.bot, callback.message.chat.id, user)
        await state.clear()
        return

    await callback.answer("Согласие не принято", show_alert=True)
    await callback.message.edit_text(
        texts.CONSENT_DECLINED,
        parse_mode="HTML",
        reply_markup=keyboards.back_to_menu(),
    )
    await state.clear()


@router.message(Command("consent"))
async def cmd_consent(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    """Пересмотреть согласие."""
    if not user.captcha_passed_at:
        await _send_captcha(message, state, session)
        return
    await _send_consent(message, state)


@router.message(Command("captcha"))
async def cmd_captcha(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    """Пересдать капчу."""
    user.captcha_passed_at = None
    session.add(user)
    await _send_captcha(message, state, session)
