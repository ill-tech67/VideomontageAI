"""Тема интерфейса: «цветные» кнопки.

У Telegram нет API для фона кнопок — зато есть цветные эмодзи-бейджи.
Поэтому семантику (ок / инфо / ошибка / внимание) мы кодируем эмодзи-индикаторами
и типом кнопки: основная — заливка, второстепенная — контур, действие — действие.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class Style(StrEnum):
    """Семантика кнопки."""

    PRIMARY = "primary"
    OK = "ok"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    DANGER = "danger"
    NEUTRAL = "neutral"


BADGE: dict[Style, str] = {
    Style.PRIMARY: "🎬",
    Style.OK: "✅",
    Style.INFO: "ℹ️",
    Style.WARN: "⚠️",
    Style.ERROR: "❌",
    Style.DANGER: "⛔",
    Style.NEUTRAL: "•",
}

TINT: dict[Style, str] = {
    Style.PRIMARY: "🔵",
    Style.OK: "🟢",
    Style.INFO: "🔵",
    Style.WARN: "🟡",
    Style.ERROR: "🔴",
    Style.DANGER: "🔴",
    Style.NEUTRAL: "⚪",
}

TITLE: dict[Style, str] = {
    Style.PRIMARY: "Действие",
    Style.OK: "Готово",
    Style.INFO: "Информация",
    Style.WARN: "Внимание",
    Style.ERROR: "Ошибка",
    Style.DANGER: "Опасная зона",
    Style.NEUTRAL: "Прочее",
}


@dataclass(slots=True)
class Button:
    """Описание кнопки в стиле бота."""

    text: str
    callback_data: str = ""
    style: Style = Style.NEUTRAL
    url: str = ""
    payload: dict[str, object] = field(default_factory=dict)

    def label(self) -> str:
        return f"{BADGE[self.style]} {self.text}"

    def to_aiogram(self) -> InlineKeyboardButton:
        if self.url:
            return InlineKeyboardButton(text=self.label(), url=self.url)
        return InlineKeyboardButton(text=self.label(), callback_data=self.callback_data)


def build_keyboard(rows: list[list[Button]]) -> InlineKeyboardMarkup:
    """Собрать инлайн-клавиатуру из описаний кнопок."""
    keyboard = [
        [button.to_aiogram() for button in row if button is not None]
        for row in rows
    ]
    return InlineKeyboardMarkup(inline_keyboard=[row for row in keyboard if row])


def status_line(style: Style, text: str) -> str:
    """Строка статуса с цветным индикатором."""
    return f"{TINT[style]} {text}"


def legend() -> str:
    """Легенда цветов для раздела «О проекте»."""
    return (
        "🟢 ок   🔵 инфо   🟡 внимание   🔴 ошибка   ⛔ опасное действие"
    )
