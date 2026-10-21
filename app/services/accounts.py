"""Работа с ключами провайдеров и настройками коннекторов пользователя."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import ConnectorConfig, ProviderKey, User
from app.security.crypto import decrypt_secret, encrypt_secret

DEFAULT_CONNECTORS = ("hyperframes", "ffmpeg", "workspace")


async def get_provider_key(session: AsyncSession, user_id: int, provider: str) -> str:
    """Ключ провайдера: сначала пользовательский, затем серверный."""
    row = await session.scalar(
        select(ProviderKey).where(
            ProviderKey.user_id == user_id, ProviderKey.provider == provider
        )
    )
    if row is not None:
        secret = decrypt_secret(row.ciphertext)
        if secret:
            return secret
    return get_settings().server_key(provider)


async def has_provider_key(session: AsyncSession, user_id: int, provider: str) -> bool:
    return bool(await get_provider_key(session, user_id, provider))


async def set_provider_key(
    session: AsyncSession, user_id: int, provider: str, key: str, label: str | None = None
) -> None:
    """Сохранить (или обновить) ключ провайдера."""
    ciphertext = encrypt_secret(key.strip())
    row = await session.scalar(
        select(ProviderKey).where(
            ProviderKey.user_id == user_id, ProviderKey.provider == provider
        )
    )
    if row is None:
        row = ProviderKey(
            user_id=user_id, provider=provider, ciphertext=ciphertext, label=label
        )
        session.add(row)
    else:
        row.ciphertext = ciphertext
        row.label = label
        row.is_valid = True
        session.add(row)


async def delete_provider_key(session: AsyncSession, user_id: int, provider: str) -> bool:
    row = await session.scalar(
        select(ProviderKey).where(
            ProviderKey.user_id == user_id, ProviderKey.provider == provider
        )
    )
    if row is None:
        return False
    await session.delete(row)
    return True


async def list_provider_keys(session: AsyncSession, user_id: int) -> list[ProviderKey]:
    result = await session.scalars(
        select(ProviderKey).where(ProviderKey.user_id == user_id).order_by(ProviderKey.provider)
    )
    return list(result)


async def get_connector_configs(session: AsyncSession, user_id: int) -> list[ConnectorConfig]:
    result = await session.scalars(
        select(ConnectorConfig).where(ConnectorConfig.user_id == user_id)
    )
    configs = list(result)
    known = {config.connector_id for config in configs}
    for connector_id in DEFAULT_CONNECTORS:
        if connector_id not in known:
            configs.append(
                ConnectorConfig(
                    user_id=user_id, connector_id=connector_id, enabled=True, options={}
                )
            )
            session.add(configs[-1])
    return configs


async def get_connector_config(
    session: AsyncSession, user_id: int, connector_id: str
) -> ConnectorConfig | None:
    return await session.scalar(
        select(ConnectorConfig).where(
            ConnectorConfig.user_id == user_id, ConnectorConfig.connector_id == connector_id
        )
    )


async def set_connector_enabled(
    session: AsyncSession, user_id: int, connector_id: str, enabled: bool
) -> None:
    config = await get_connector_config(session, user_id, connector_id)
    if config is None:
        config = ConnectorConfig(user_id=user_id, connector_id=connector_id)
        session.add(config)
    config.enabled = enabled
    session.add(config)


async def set_connector_options(
    session: AsyncSession, user_id: int, connector_id: str, options: dict
) -> None:
    config = await get_connector_config(session, user_id, connector_id)
    if config is None:
        config = ConnectorConfig(user_id=user_id, connector_id=connector_id)
        session.add(config)
    merged = dict(config.options or {})
    merged.update(options)
    config.options = merged
    session.add(config)


async def enabled_connector_ids(session: AsyncSession, user_id: int) -> list[str]:
    configs = await get_connector_configs(session, user_id)
    return [config.connector_id for config in configs if config.enabled]


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)
