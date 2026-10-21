"""ORM-модели: пользователи, согласия, ключи, коннекторы, проекты, джобы."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return uuid.uuid4().hex


class User(Base):
    """Пользователь Telegram."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), default=None)
    first_name: Mapped[str] = mapped_column(String(128), default="")
    last_name: Mapped[str | None] = mapped_column(String(128), default=None)
    language_code: Mapped[str | None] = mapped_column(String(16), default=None)

    captcha_passed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    consent_version: Mapped[str | None] = mapped_column(String(32), default=None)
    consent_accepted_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked_reason: Mapped[str | None] = mapped_column(String(255), default=None)

    default_provider: Mapped[str | None] = mapped_column(String(32), default=None)
    default_skill: Mapped[str | None] = mapped_column(String(32), default=None)

    settings: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    consents: Mapped[list[ConsentRecord]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    captcha_attempts: Mapped[list[CaptchaAttempt]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    provider_keys: Mapped[list[ProviderKey]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    connector_configs: Mapped[list[ConnectorConfig]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    projects: Mapped[list[Project]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class CaptchaAttempt(Base):
    """Попытка ввода капчи. Ответ хранится только в виде хеша."""

    __tablename__ = "captcha_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    user: Mapped[User] = relationship(back_populates="captcha_attempts")


class ConsentRecord(Base):
    """Зафиксированное согласие на передачу данных третьим лицам."""

    __tablename__ = "consent_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(32))
    text_sha: Mapped[str] = mapped_column(String(64))
    accepted_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    ip: Mapped[str | None] = mapped_column(String(64), default=None)
    user_agent: Mapped[str | None] = mapped_column(String(255), default=None)

    user: Mapped[User] = relationship(back_populates="consents")


class ProviderKey(Base):
    """Зашифрованный ключ ИИ-провайдера пользователя (BYOK)."""

    __tablename__ = "provider_keys"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_provider_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    ciphertext: Mapped[str] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(String(64), default=None)
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    user: Mapped[User] = relationship(back_populates="provider_keys")


class ConnectorConfig(Base):
    """Настройки и статус коннектора у конкретного пользователя."""

    __tablename__ = "connector_configs"
    __table_args__ = (UniqueConstraint("user_id", "connector_id", name="uq_connector_cfg"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    connector_id: Mapped[str] = mapped_column(String(64), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    user: Mapped[User] = relationship(back_populates="connector_configs")


class Project(Base):
    """Проект монтажа."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255), default="Новый проект")
    skill_id: Mapped[str] = mapped_column(String(64), default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str | None] = mapped_column(String(32), default=None)
    model: Mapped[str | None] = mapped_column(String(64), default=None)
    aspect: Mapped[str] = mapped_column(String(16), default="16:9")
    width: Mapped[int] = mapped_column(Integer, default=1920)
    height: Mapped[int] = mapped_column(Integer, default=1080)
    duration: Mapped[float] = mapped_column(default=30.0)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    dir_path: Mapped[str | None] = mapped_column(String(512), default=None)
    result_path: Mapped[str | None] = mapped_column(String(512), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    user: Mapped[User] = relationship(back_populates="projects")
    jobs: Mapped[list[Job]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Job(Base):
    """Фоновая задача (рендер, транскодирование, генерация)."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), default=None, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="render", index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    stage: Mapped[str | None] = mapped_column(String(64), default=None)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text, default=None)
    result_path: Mapped[str | None] = mapped_column(String(512), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    project: Mapped[Project | None] = relationship(back_populates="jobs")


class MessageLog(Base):
    """История диалога с агентом (контекст LLM)."""

    __tablename__ = "message_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


Index("ix_message_logs_user_created", MessageLog.user_id, MessageLog.created_at)
