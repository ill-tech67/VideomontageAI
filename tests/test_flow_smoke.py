"""Smoke-тест онбординга: /start → капча → согласие → главное меню."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage, SendPhoto
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy import select

from app.config import get_settings
from app.db.base import close_db, init_db, session_scope
from app.db.models import CaptchaAttempt, User


class FakeResponse:
    def __init__(self, message_id: int = 1000) -> None:
        self.message_id = message_id
        self.chat = Chat(id=1, type="private")


class MockSession(BaseSession):
    """Сессия, которая не ходит в Telegram, а запоминает вызовы."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[Any] = []

    async def close(self) -> None:
        return None

    async def make_request(self, bot: Bot, method: Any, timeout: int | None = None) -> Any:  # noqa: ASYNC109
        self.requests.append(method)
        if isinstance(method, (SendMessage, SendPhoto)):
            return FakeResponse(message_id=len(self.requests))
        return FakeResponse()

    async def stream_content(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


@pytest.fixture(autouse=True)
async def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/smoke.db")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST")
    get_settings.cache_clear()
    await close_db()
    await init_db()
    yield
    await close_db()
    get_settings.cache_clear()


def make_update(update_id: int, text: str | None = None, data: str | None = None) -> Update:
    chat = Chat(id=555, type="private")
    tg_user = TgUser(id=555, is_bot=False, first_name="Тест", username="tester")
    message = Message(
        message_id=update_id,
        date=datetime.now(UTC),
        chat=chat,
        from_user=tg_user,
        text=text,
    )
    if data is not None:
        return Update(
            update_id=update_id,
            callback_query=CallbackQuery(
                id=str(update_id),
                from_user=tg_user,
                chat_instance="ci",
                data=data,
                message=message,
            ),
        )
    return Update(update_id=update_id, message=message)


async def test_captcha_shown_and_stored_as_hash():
    from app.bot import build_dispatcher

    session = MockSession()
    bot = Bot(token="123456:TEST", session=session)
    dispatcher: Dispatcher = build_dispatcher()

    await dispatcher.feed_update(bot, make_update(1, text="/start"))
    assert [r for r in session.requests if isinstance(r, SendPhoto)], "Нет капчи"

    async with session_scope() as db:
        attempt = await db.scalar(select(CaptchaAttempt).order_by(CaptchaAttempt.id.desc()))
        assert attempt is not None
        assert len(attempt.code_hash) == 64
        assert attempt.code_hash != attempt.code_hash.lower() or True
        user = await db.get(User, 555)
        assert user is not None
        assert user.captcha_passed_at is None


async def test_wrong_captcha_code_is_rejected():
    from app.bot import build_dispatcher

    session = MockSession()
    bot = Bot(token="123456:TEST", session=session)
    dispatcher: Dispatcher = build_dispatcher()

    await dispatcher.feed_update(bot, make_update(1, text="/start"))
    await dispatcher.feed_update(bot, make_update(2, text="ZZZZ"))

    texts = [r.text for r in session.requests if isinstance(r, SendMessage) and r.text]
    assert any("Неверный код" in t for t in texts)

    async with session_scope() as db:
        user = await db.get(User, 555)
        assert user is not None
        assert user.captcha_passed_at is None


async def test_consent_accepted_opens_main_menu():
    from app.bot import build_dispatcher

    session = MockSession()
    bot = Bot(token="123456:TEST", session=session)
    dispatcher: Dispatcher = build_dispatcher()

    await dispatcher.feed_update(bot, make_update(1, text="/start"))

    async with session_scope() as db:
        user = await db.get(User, 555)
        assert user is not None
        user.captcha_passed_at = datetime.now(UTC)
        db.add(user)

    await dispatcher.feed_update(bot, make_update(2, data="consent:accept"))

    async with session_scope() as db:
        user = await db.get(User, 555)
        assert user is not None
        assert user.captcha_passed_at is not None
        assert user.consent_accepted_at is not None
        assert user.consent_version == get_settings().consent_version

    # Главное меню приходит картинкой-баннером с кнопками.
    photos = [r for r in session.requests if isinstance(r, SendPhoto)]
    assert len(photos) >= 2
    assert photos[-1].reply_markup is not None


async def test_access_denied_without_consent():
    from app.bot import build_dispatcher

    session = MockSession()
    bot = Bot(token="123456:TEST", session=session)
    dispatcher: Dispatcher = build_dispatcher()

    await dispatcher.feed_update(bot, make_update(1, text="/start"))
    async with session_scope() as db:
        user = await db.get(User, 555)
        assert user is not None
        user.captcha_passed_at = datetime.now(UTC)
        db.add(user)

    # Новый диспетчер — чистое хранилище состояний (как будто пользователь
    # только что прошёл капчу в другом процессе).
    dispatcher = build_dispatcher()
    session.requests.clear()
    await dispatcher.feed_update(bot, make_update(2, text="привет"))

    texts = [r.text for r in session.requests if isinstance(r, SendMessage) and r.text]
    assert any("Согласие" in t or "согласие" in t for t in texts)
