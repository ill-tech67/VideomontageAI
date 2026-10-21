"""Тесты согласия на передачу данных третьим лицам."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import ConsentRecord, User
from app.services import consent as consent_service


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as sess:
        yield sess
    await engine.dispose()


async def test_consent_text_mentions_providers():
    text = consent_service.consent_text()
    for name in ("Google Gemini", "Anthropic Claude", "OpenAI GPT", "ElevenLabs"):
        assert name in text
    assert "MCP" in text


async def test_accept_and_check(session):
    user = User(id=1, first_name="Тест")
    session.add(user)
    await session.flush()

    assert not await consent_service.has_consent(session, user)

    await consent_service.accept_consent(session, user, ip="1.2.3.4", user_agent="pytest")
    await session.flush()

    assert await consent_service.has_consent(session, user)
    assert user.consent_version == consent_service.get_settings().consent_version


async def test_revoke_consent(session):
    user = User(id=2, first_name="Тест")
    session.add(user)
    await session.flush()
    await consent_service.accept_consent(session, user)
    await session.flush()

    await consent_service.revoke_consent(session, user)
    await session.flush()

    assert not await consent_service.has_consent(session, user)
    assert user.consent_version is None


async def test_consent_record_is_stored(session):
    user = User(id=3, first_name="Тест")
    session.add(user)
    await session.flush()
    await consent_service.accept_consent(session, user)
    await session.flush()

    records = (
        await session.scalars(select(ConsentRecord).where(ConsentRecord.user_id == user.id))
    ).all()
    assert len(records) == 1
    assert records[0].text_sha == consent_service.consent_hash()
