"""Скиллы — готовые сценарии работы агента (промпт + набор коннекторов + пресеты)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Skill:
    """Описание скилла."""

    id: str
    title: str
    emoji: str
    description: str
    prompt: str
    """Системный промпт, который добавляется к базовому."""
    connectors: list[str] = field(default_factory=list)
    defaults: dict[str, Any] = field(default_factory=dict)
    """Пресеты кадра/длительности."""
    example: str = ""
    """Пример запроса пользователя для подсказки."""


class SkillRegistry:
    """Реестр скиллов."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        self._skills[skill.id] = skill

    def get(self, skill_id: str) -> Skill | None:
        return self._skills.get(skill_id)

    def all(self) -> list[Skill]:
        return list(self._skills.values())

    def ids(self) -> list[str]:
        return list(self._skills)
