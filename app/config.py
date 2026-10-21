"""Конфигурация приложения (env → pydantic-settings)."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

RenderMode = Literal["local", "docker", "cloud"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Telegram -------------------------------------------------
    bot_token: str = Field(default="", description="Токен от @BotFather")
    admin_ids: list[int] = Field(default_factory=list)

    # --- Хранилище ------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./data/videotg.db"
    data_dir: Path = Path("./data")

    # --- Безопасность ---------------------------------------------
    encryption_key: str = "change-me-development-only"

    # --- Ключи провайдеров (серверные умолчания, BYOK) -------------
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    elevenlabs_api_key: str = ""

    # --- Рендеринг ------------------------------------------------
    render_mode: RenderMode = "local"
    hyperframes_cli: str = "npx"
    hyperframes_version: str = "0.8.181"
    ffmpeg_binary: str = ""
    ffprobe_binary: str = ""
    require_modern_ffmpeg: bool = True
    max_concurrent_renders: int = 2
    render_timeout: int = 1800
    hyperframes_cloud_token: str = ""

    # --- Политика и лимиты ----------------------------------------
    consent_version: str = "2026-10-20"
    captcha_ttl_seconds: int = 300
    captcha_max_attempts: int = 5
    captcha_length: int = 4
    agent_max_steps: int = 12
    max_upload_mb: int = 200

    # --- Прочее -----------------------------------------------------
    log_level: str = "INFO"
    long_polling: bool = True
    webhook_secret: str = ""
    webhook_url: str = ""

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            raw = value.replace(" ", "")
            if not raw:
                return []
            return [int(chunk) for chunk in raw.split(",") if chunk]
        return value

    @field_validator("data_dir", mode="before")
    @classmethod
    def _expand_data_dir(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(value).expanduser()
        return value

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def assets_dir(self) -> Path:
        return self.data_dir / "assets"

    @property
    def renders_dir(self) -> Path:
        return self.data_dir / "renders"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.projects_dir,
            self.assets_dir,
            self.renders_dir,
            self.cache_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def server_key(self, provider: str) -> str:
        """Серверный (глобальный) ключ провайдера, если он задан админом."""
        return {
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "gemini": self.gemini_api_key,
            "elevenlabs": self.elevenlabs_api_key,
        }.get(provider, "")

    def encryption_secret(self) -> bytes:
        """Секрет для вывода ключа шифрования (Fernet)."""
        return hashlib.sha256(self.encryption_key.encode("utf-8")).hexdigest().encode("ascii")

    def fernet_key(self) -> bytes:

        digest = hashlib.sha256(self.encryption_key.encode("utf-8")).digest()
        import base64

        return base64.urlsafe_b64encode(digest)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
