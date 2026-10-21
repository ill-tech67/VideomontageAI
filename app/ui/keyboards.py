"""Клавиатуры бота (собраны на theme.Button — с цветными бейджами)."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from app.ai.providers import provider_catalog
from app.connectors.base import Connector
from app.skills.base import Skill
from app.ui.theme import Button, Style, build_keyboard


def main_menu() -> InlineKeyboardMarkup:
    """Главное меню."""
    return build_keyboard(
        [
            [
                Button("Монтировать", "menu:montage", Style.PRIMARY),
                Button("Подключить ИИ", "menu:ai", Style.INFO),
            ],
            [
                Button("Коннекторы", "menu:connectors", Style.INFO),
                Button("Скиллы", "menu:skills", Style.NEUTRAL),
            ],
            [
                Button("Проекты", "menu:projects", Style.NEUTRAL),
                Button("Настройки", "menu:settings", Style.NEUTRAL),
            ],
            [
                Button("О проекте", "menu:about", Style.INFO),
                Button("Помощь", "menu:help", Style.NEUTRAL),
            ],
        ]
    )


def back_to_menu() -> InlineKeyboardMarkup:
    return build_keyboard(
        [[Button("🏠 В главное меню", "menu:home", Style.NEUTRAL)]]
    )


def consent_keyboard() -> InlineKeyboardMarkup:
    return build_keyboard(
        [
            [Button("Принимаю", "consent:accept", Style.OK)],
            [Button("Не принимаю", "consent:decline", Style.DANGER)],
        ]
    )


def skills_keyboard(skills: list[Skill]) -> InlineKeyboardMarkup:
    rows = [
        [
            Button(
                f"{skill.emoji} {skill.title}",
                f"skill:{skill.id}",
                Style.PRIMARY if skill.id == "shorts" else Style.NEUTRAL,
            )
        ]
        for skill in skills
    ]
    rows.append([Button("🏠 В главное меню", "menu:home", Style.NEUTRAL)])
    return build_keyboard(rows)


def providers_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            Button(
                f"{emoji} {title}",
                f"ai:provider:{provider_id}",
                Style.INFO,
            )
        ]
        for provider_id, title, emoji in provider_catalog()
    ]
    rows.append([Button("🏠 В главное меню", "menu:home", Style.NEUTRAL)])
    return build_keyboard(rows)


def connectors_keyboard(connectors: list[Connector], enabled: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for connector in connectors:
        is_on = connector.id in enabled
        rows.append(
            [
                Button(
                    f"{connector.emoji} {connector.title}",
                    f"conn:info:{connector.id}",
                    Style.OK if is_on else Style.NEUTRAL,
                ),
                Button(
                    "🔴 Выключить" if is_on else "🟢 Включить",
                    f"conn:toggle:{connector.id}",
                    Style.WARN if is_on else Style.OK,
                ),
            ]
        )
    rows.append([Button("🏠 В главное меню", "menu:home", Style.NEUTRAL)])
    return build_keyboard(rows)


def projects_keyboard(projects: list[tuple[str, str, str]]) -> InlineKeyboardMarkup:
    """projects: (id, title, status)."""
    rows = []
    for project_id, title, status in projects:
        style = {
            "done": Style.OK,
            "failed": Style.ERROR,
            "running": Style.WARN,
        }.get(status, Style.NEUTRAL)
        rows.append(
            [
                Button(
                    f"{title[:28]}",
                    f"project:{project_id}",
                    style,
                )
            ]
        )
    if not rows:
        rows.append([Button("Пока пусто", "noop", Style.NEUTRAL)])
    rows.append([Button("🏠 В главное меню", "menu:home", Style.NEUTRAL)])
    return build_keyboard(rows)


def cancel_keyboard() -> InlineKeyboardMarkup:
    return build_keyboard([[Button("Отмена", "cancel", Style.DANGER)]])


def confirm_keyboard(yes_data: str, no_data: str = "menu:home") -> InlineKeyboardMarkup:
    return build_keyboard(
        [
            [Button("Да", yes_data, Style.OK), Button("Нет", no_data, Style.NEUTRAL)]
        ]
    )
