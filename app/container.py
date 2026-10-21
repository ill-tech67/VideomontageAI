"""Контейнер сервисов (собирается один раз при старте)."""

from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot

from app.config import Settings, get_settings
from app.connectors.base import Connector
from app.connectors.elevenlabs import ElevenLabsConnector
from app.connectors.ffmpeg_tools import FFmpegConnector
from app.connectors.hyperframes import HyperFramesConnector
from app.connectors.mcp_connector import McpConnector
from app.connectors.registry import ConnectorRegistry
from app.connectors.workspace import WorkspaceConnector
from app.services.jobs import JobManager
from app.services.rendering import HyperFramesRunner
from app.skills.base import SkillRegistry
from app.skills.builtin import build_registry


@dataclass(slots=True)
class Container:
    """Все долгоживущие сервисы бота."""

    settings: Settings
    bot: Bot
    runner: HyperFramesRunner
    job_manager: JobManager
    registry: ConnectorRegistry
    skills: SkillRegistry

    def connectors(self) -> list[Connector]:
        return self.registry.all()


_container: Container | None = None


def build_container(bot: Bot) -> Container:
    """Собрать контейнер сервисов."""
    settings = get_settings()
    settings.ensure_dirs()

    runner = HyperFramesRunner(settings)
    job_manager = JobManager(bot, runner, settings)

    registry = ConnectorRegistry()
    registry.register(HyperFramesConnector(runner, job_manager, settings))
    registry.register(FFmpegConnector(settings))
    registry.register(WorkspaceConnector())
    registry.register(ElevenLabsConnector(settings))
    registry.register(McpConnector())

    return Container(
        settings=settings,
        bot=bot,
        runner=runner,
        job_manager=job_manager,
        registry=registry,
        skills=build_registry(),
    )


def set_container(container: Container) -> None:
    global _container
    _container = container


def get_container() -> Container:
    if _container is None:
        raise RuntimeError("Контейнер сервисов не инициализирован")
    return _container
