"""Проекты: история роликов и загрузка готовых файлов."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Project, User
from app.ui import keyboards
from app.ui.theme import Button, Style, build_keyboard

logger = logging.getLogger(__name__)

router = Router(name="projects")

STATUS_STYLE: dict[str, tuple[str, Style]] = {
    "done": ("✅", Style.OK),
    "failed": ("❌", Style.ERROR),
    "running": ("⏳", Style.WARN),
    "draft": ("📝", Style.NEUTRAL),
}

STATUS_TEXT: dict[str, str] = {
    "done": "готов",
    "failed": "ошибка",
    "running": "в работе",
    "draft": "черновик",
}


@router.callback_query(F.data == "menu:projects")
async def on_projects(callback: CallbackQuery, user: User, session: AsyncSession) -> None:
    """Список проектов."""
    await callback.answer()
    projects = list(
        await session.scalars(
            select(Project)
            .where(Project.user_id == user.id)
            .order_by(Project.created_at.desc())
            .limit(30)
        )
    )
    if not projects:
        await callback.message.edit_text(
            "📂 <b>Проектов пока нет</b>\n\n"
            "Нажмите «🎬 Монтировать», чтобы создать первый ролик.",
            parse_mode="HTML",
            reply_markup=keyboards.back_to_menu(),
        )
        return

    rows = []
    for project in projects:
        icon, style = STATUS_STYLE.get(project.status, ("•", Style.NEUTRAL))
        rows.append(
            [
                Button(
                    f"{icon} {project.title[:26]} · {project.created_at:%d.%m}",
                    f"project:{project.id}",
                    style,
                )
            ]
        )
    rows.append([Button("🏠 В главное меню", "menu:home", Style.NEUTRAL)])
    await callback.message.edit_text(
        "📂 <b>Ваши проекты</b>\n\nВыберите ролик, чтобы посмотреть детали и скачать файл.",
        parse_mode="HTML",
        reply_markup=build_keyboard(rows),
    )


@router.callback_query(F.data.startswith("project:"))
async def on_project(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    """Детали проекта."""
    project_id = (callback.data or "").split(":")[-1]
    await callback.answer()
    project = await session.get(Project, project_id)
    if project is None or project.user_id != user.id:
        await callback.message.edit_text("❌ Проект не найден.")
        return

    icon, style = STATUS_STYLE.get(project.status, ("•", Style.NEUTRAL))
    status_text = STATUS_TEXT.get(project.status, project.status)
    lines = [
        f"{icon} <b>{project.title}</b>",
        f"Статус: {status_text}",
        f"Кадр: {project.width}×{project.height} · ~{int(project.duration)} с",
        f"Модель: {project.provider or '—'} {project.model or ''}".strip(),
        f"Создан: {project.created_at:%d.%m.%Y %H:%M}",
    ]
    if project.prompt:
        lines.append(f"\n<i>Задание: {project.prompt[:400]}</i>")
    if project.error:
        lines.append(f"\n❌ <i>{project.error[:500]}</i>")

    rows = []
    if project.result_path and await asyncio.to_thread(Path(project.result_path).exists):
        rows.append([Button("⬇️ Скачать видео", f"project:file:{project.id}", Style.OK)])
    rows.append([Button("🏠 В главное меню", "menu:home", Style.NEUTRAL)])
    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=build_keyboard(rows),
    )


@router.callback_query(F.data.startswith("project:file:"))
async def on_project_file(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    """Отправить готовый файл."""
    project_id = (callback.data or "").split(":")[-1]
    await callback.answer("Отправляю…")
    project = await session.get(Project, project_id)
    if project is None or project.user_id != user.id or not project.result_path:
        await callback.message.answer("❌ Файл не найден.")
        return
    path = Path(project.result_path)
    if not await asyncio.to_thread(path.exists):
        await callback.message.answer("❌ Файл уже удалён с сервера.")
        return
    await callback.message.answer_video(
        FSInputFile(path), caption=f"🎬 {project.title}", supports_streaming=True
    )


@router.message(F.text == "/projects")
async def cmd_projects(message: Message, user: User, session: AsyncSession) -> None:
    """Список проектов командой."""
    projects = list(
        await session.scalars(
            select(Project)
            .where(Project.user_id == user.id)
            .order_by(Project.created_at.desc())
            .limit(10)
        )
    )
    if not projects:
        await message.answer("📂 Проектов пока нет.")
        return
    lines = ["📂 <b>Ваши проекты</b>\n"]
    for project in projects:
        icon, _style = STATUS_STYLE.get(project.status, ("•", Style.NEUTRAL))
        status_text = STATUS_TEXT.get(project.status, project.status)
        lines.append(f"{icon} {project.title} — {status_text} ({project.created_at:%d.%m})")
    await message.answer("\n".join(lines))
