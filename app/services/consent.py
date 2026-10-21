"""Согласие на передачу материалов третьим лицам."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import ConsentRecord, User

THIRD_PARTIES: tuple[tuple[str, str], ...] = (
    ("Google Gemini", "текст запроса, описание ролика и при необходимости материалы"),
    ("Anthropic Claude", "текст запроса, описание ролика и при необходимости материалы"),
    ("OpenAI GPT", "текст запроса, описание ролика и при необходимости материалы"),
    ("ElevenLabs", "текст для озвучки (при использовании коннектора)"),
    ("HyperFrames / HeyGen", "HTML-композиция и локальные ассеты (при облачном рендере)"),
    ("MCP-серверы пользователя", "аргументы вызова инструментов, которые вы сами подключили"),
)

HOSTING_NOTE = (
    "Материалы также обрабатываются на сервере, где запущен бот: "
    "файлы проекта хранятся в рабочем каталоге бота до их удаления."
)


def consent_text() -> str:
    """Полный текст согласия (то, что видит пользователь)."""
    settings = get_settings()
    lines = [
        "<b>Что передаётся</b>",
        "Текст вашего запроса, описание ролика, прикреплённые материалы "
        "(изображения, видео, аудио) и технические параметры проекта.",
        "",
        "<b>Кому</b>",
    ]
    for name, what in THIRD_PARTIES:
        lines.append(f"• {name} — {what}")
    lines.extend(
        [
            "",
            "<b>Где это происходит</b>",
            HOSTING_NOTE,
            "",
            "<b>Чего бот не делает</b>",
            "• не отправляет ваши материалы провайдерам, которых вы не подключили;",
            "• не публикует и не продаёт ваши данные;",
            "• не передаёт картинку капчи ни одному внешнему сервису — "
            "ответ капчи хранится только в виде хеша на сервере.",
            "",
            "<b>Отзыв согласия</b>",
            "В любой момент в разделе «⚙️ Настройки → Согласие». "
            "После отзыва ИИ-функции перестают работать.",
            "",
            f"<b>Версия согласия:</b> {settings.consent_version}",
        ]
    )
    return "\n".join(lines)


def consent_hash() -> str:
    """Хеш текста согласия — фиксирует, какую редакцию принял пользователь."""
    return hashlib.sha256(consent_text().encode("utf-8")).hexdigest()


async def has_consent(session: AsyncSession, user: User) -> bool:
    """Принято ли актуальное согласие."""
    settings = get_settings()
    if not user.consent_accepted_at:
        return False
    return user.consent_version == settings.consent_version


async def accept_consent(
    session: AsyncSession,
    user: User,
    ip: str | None = None,
    user_agent: str | None = None,
) -> ConsentRecord:
    """Зафиксировать согласие."""
    settings = get_settings()
    record = ConsentRecord(
        user_id=user.id,
        version=settings.consent_version,
        text_sha=consent_hash(),
        accepted_at=datetime.now(UTC),
        ip=ip,
        user_agent=(user_agent or "")[:255] or None,
    )
    session.add(record)
    user.consent_version = settings.consent_version
    user.consent_accepted_at = record.accepted_at
    session.add(user)
    return record


async def revoke_consent(session: AsyncSession, user: User) -> None:
    """Отозвать согласие."""
    user.consent_version = None
    user.consent_accepted_at = None
    session.add(user)
    result = await session.scalars(
        select(ConsentRecord).where(ConsentRecord.user_id == user.id)
    )
    for record in result:
        await session.delete(record)
