"""Основной сценарий: описание от пользователя → монтаж → готовое видео."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import AgentEvent, AgentRunner
from app.ai.providers import PROVIDER_TITLES, create_provider, provider_catalog
from app.ai.types import ChatTurn
from app.config import get_settings
from app.container import get_container
from app.db.base import session_scope
from app.db.models import MessageLog, Project, User
from app.handlers.common import edit_or_answer, split_message
from app.services.accounts import (
    enabled_connector_ids,
    get_connector_config,
    get_provider_key,
)
from app.ui import keyboards, texts
from app.ui.theme import Button, Style, build_keyboard

logger = logging.getLogger(__name__)

router = Router(name="montage")


class MontageFlow(StatesGroup):
    """Шаги сценария монтажа."""

    brief = State()
    pending_provider = State()


@router.callback_query(F.data == "menu:montage")
async def on_montage(callback: CallbackQuery) -> None:
    """Выбор скилла (формата ролика)."""
    await callback.answer()
    container = get_container()
    skills = container.skills.all()
    lines = ["🎬 <b>Что будем делать?</b>\n"]
    for skill in skills:
        lines.append(f"{skill.emoji} <b>{skill.title}</b> — {skill.description}")
        lines.append(
            f"    кадр {skill.defaults.get('width')}×{skill.defaults.get('height')}"
            f" · ~{int(skill.defaults.get('duration', 30))} с"
        )
    lines.append("\nВыберите формат:")
    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=keyboards.skills_keyboard(skills),
    )


@router.callback_query(F.data.startswith("skill:"))
async def on_skill(
    callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext
) -> None:
    """Выбран скилл: готовим проект и просим описание."""
    skill_id = (callback.data or "").split(":")[-1]
    container = get_container()
    skill = container.skills.get(skill_id)
    if skill is None:
        await callback.answer("Скилл не найден", show_alert=True)
        return

    provider = await _pick_provider(session, user)
    if provider is None:
        await callback.answer("Нужен ключ ИИ", show_alert=True)
        await state.set_state(MontageFlow.pending_provider)
        await state.update_data(skill_id=skill_id)
        await callback.message.edit_text(
            texts.NO_AI_KEY,
            parse_mode="HTML",
            reply_markup=build_keyboard(
                [
                    [Button("🤖 Подключить ИИ", "menu:ai", Style.PRIMARY)],
                    [Button("🏠 В главное меню", "menu:home", Style.NEUTRAL)],
                ]
            ),
        )
        return

    if provider == "__choose__":
        await callback.answer()
        await state.set_state(MontageFlow.pending_provider)
        await state.update_data(skill_id=skill_id)
        rows = [
            [
                Button(
                    f"{emoji} {PROVIDER_TITLES.get(provider_id, provider_id)}",
                    f"montage:provider:{provider_id}",
                    Style.INFO,
                )
            ]
            for provider_id, _title, emoji in provider_catalog()
        ]
        rows.append([Button("🏠 В главное меню", "menu:home", Style.NEUTRAL)])
        await callback.message.edit_text(
            "🧠 <b>Какой моделью монтируем?</b>\n\n"
            "У вас подключено несколько провайдеров — выберите модель для этого ролика.",
            parse_mode="HTML",
            reply_markup=build_keyboard(rows),
        )
        return

    await callback.answer()
    project = await _create_project(session, user, skill, provider)
    await state.set_state(MontageFlow.brief)
    await state.update_data(project_id=project.id, skill_id=skill.id, provider=provider)

    await callback.message.edit_text(
        texts.PROJECT_START.format(
            skill=f"{skill.emoji} {skill.title}",
            provider=PROVIDER_TITLES.get(provider, provider),
            model=await _model_for(session, user, provider),
            width=skill.defaults.get("width"),
            height=skill.defaults.get("height"),
            duration=int(skill.defaults.get("duration", 30)),
        )
        + "\n\n<i>Пример: "
        + skill.example
        + "</i>",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("montage:provider:"))
async def on_pending_provider(
    callback: CallbackQuery, user: User, session: AsyncSession, state: FSMContext
) -> None:
    """Выбор модели для отложенного старта."""
    provider = (callback.data or "").split(":")[-1]
    key = await get_provider_key(session, user.id, provider)
    if not key:
        await callback.answer("Сначала добавьте ключ", show_alert=True)
        return
    data = await state.get_data()
    skill_id = str(data.get("skill_id") or "")
    container = get_container()
    skill = container.skills.get(skill_id)
    if skill is None:
        await callback.answer("Скилл потерян, начните заново", show_alert=True)
        return
    await callback.answer("Модель выбрана ✅")
    project = await _create_project(session, user, skill, provider)
    await state.set_state(MontageFlow.brief)
    await state.update_data(project_id=project.id, skill_id=skill.id, provider=provider)
    await callback.message.edit_text(
        texts.PROJECT_START.format(
            skill=f"{skill.emoji} {skill.title}",
            provider=PROVIDER_TITLES.get(provider, provider),
            model=await _model_for(session, user, provider),
            width=skill.defaults.get("width"),
            height=skill.defaults.get("height"),
            duration=int(skill.defaults.get("duration", 30)),
        ),
        parse_mode="HTML",
    )


@router.message(MontageFlow.brief, F.photo | F.video | F.audio | F.document)
async def on_material(message: Message, state: FSMContext, user: User) -> None:
    """Пользователь прислал материал — сохраняем в проект."""
    data = await state.get_data()
    project_id = str(data.get("project_id") or "")
    if not project_id:
        return
    container = get_container()
    project_dir = container.settings.projects_dir / str(user.id) / project_id
    assets = project_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    content = (
        message.photo[-1]
        if message.photo
        else message.video
        if message.video
        else message.audio
        if message.audio
        else message.document
    )
    if content is None:
        return

    extension = getattr(content, "file_name", None) or "file.bin"
    if message.video:
        extension = "mp4"
    elif message.audio:
        extension = "mp3"
    elif message.photo:
        extension = "jpg"
    filename = f"{message.message_id}_{Path(str(extension)).name}"
    target = assets / filename
    await message.bot.download(content, destination=target)

    await message.answer(
        f"📥 Материал сохранён: <code>assets/{filename}</code>\n\n"
        "Теперь опишите ролик текстом — что показать, какой текст на экране, "
        "какой стиль и настроение.",
        parse_mode="HTML",
    )


@router.message(MontageFlow.brief)
async def on_brief(message: Message, state: FSMContext, user: User, session: AsyncSession) -> None:
    """Описание ролика → запуск агента."""
    brief = (message.text or "").strip()
    if not brief:
        await message.answer("⚠️ Пришлите текстовое описание ролика.")
        return

    data = await state.get_data()
    project_id = str(data.get("project_id") or "")
    skill_id = str(data.get("skill_id") or "")
    provider_id = str(data.get("provider") or "")
    container = get_container()
    skill = container.skills.get(skill_id)
    if skill is None or not project_id:
        await state.clear()
        await message.answer("⚠️ Проект потерян. Начните монтаж заново: /menu")
        return

    project_dir = container.settings.projects_dir / str(user.id) / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    key = await get_provider_key(session, user.id, provider_id)
    if not key:
        await state.clear()
        await message.answer(texts.NO_AI_KEY, parse_mode="HTML")
        return

    model = await _model_for(session, user, provider_id)
    await state.clear()

    status = await message.answer(
        texts.RENDER_STARTED.format(
            provider=PROVIDER_TITLES.get(provider_id, provider_id),
            model=model,
            skill=f"{skill.emoji} {skill.title}",
        ),
        parse_mode="HTML",
    )

    async with session_scope() as sess:
        project = await sess.get(Project, project_id)
        if project is not None:
            project.status = "running"
            project.prompt = brief
            project.provider = provider_id
            project.model = model
            sess.add(project)

    # Контекст инструментов.
    options = {
        "title": skill.title,
        "elevenlabs_key": await get_provider_key(session, user.id, "elevenlabs"),
        "mcp_servers": await _mcp_servers(session, user.id),
        "status_message_id": status.message_id,
    }

    ctx = _build_context(message, project_id, project_dir, options)

    connector_ids = await enabled_connector_ids(session, user.id)
    allowed = _allowed_connectors(skill.connectors, connector_ids)
    if "mcp" in allowed and not options["mcp_servers"]:
        allowed = [c for c in allowed if c != "mcp"]

    def _has_key(connector_id: str) -> bool:
        return connector_id != "elevenlabs" or bool(options["elevenlabs_key"])

    tools = await container.registry.build_tools(ctx, allowed, _has_key)

    provider = create_provider(provider_id, key)
    last_update = 0.0

    async def on_event(event: AgentEvent) -> None:
        nonlocal last_update
        loop_time = asyncio.get_event_loop().time()
        if loop_time - last_update < 4.0:
            return
        last_update = loop_time
        if event.kind in {"tool", "tool_error"}:
            icon = "⚠️" if event.kind == "tool_error" else "🔧"
            await edit_or_answer(status, f"{icon} {event.text[:900]}", parse_mode=None)

    runner = AgentRunner(
        provider,
        container.registry,
        ctx,
        max_steps=get_settings().agent_max_steps,
        on_event=on_event,
    )

    result = await runner.run(system=skill.prompt, history=[], user_text=brief, tools=tools)

    if result.error:
        await edit_or_answer(
            status, texts.AGENT_ERROR.format(error=result.error[:800]), parse_mode="HTML"
        )
        return

    for part in split_message(result.text or "Готово ✅"):
        await message.answer(part, parse_mode=None)

    for artifact in result.artifacts:
        path = Path(artifact.path)
        if not await asyncio.to_thread(path.exists):
            continue
        try:
            if artifact.kind == "audio":
                await message.answer_audio(FSInputFile(path), caption=artifact.caption)
            elif artifact.kind == "image":
                await message.answer_photo(FSInputFile(path), caption=artifact.caption)
            else:
                await message.answer_document(FSInputFile(path), caption=artifact.caption)
        except Exception:  # noqa: BLE001
            logger.debug("Не удалось отправить артефакт", exc_info=True)

    # Сохраняем историю диалога.
    await _log_message(session, user.id, "user", brief)
    await _log_message(session, user.id, "assistant", result.text[:4000])


# --- вспомогательное --------------------------------------------
REQUIRED_CONNECTORS = ("hyperframes", "workspace", "ffmpeg")


def _allowed_connectors(skill_connectors: list[str], enabled: list[str]) -> list[str]:
    """Инструменты скилла: обязательные всегда, опциональные — если включены."""
    allowed = [c for c in skill_connectors if c in REQUIRED_CONNECTORS]
    allowed += [c for c in skill_connectors if c not in REQUIRED_CONNECTORS and c in enabled]
    return allowed


async def _pick_provider(session: AsyncSession, user: User) -> str | None:
    """Определить модель.

    Возвращает id провайдера, ``None`` (нет ни одного ключа) или
    ``"__choose__"`` (ключей несколько и нет модели по умолчанию).
    """
    from app.ai.providers import PROVIDERS

    if user.default_provider:
        key = await get_provider_key(session, user.id, user.default_provider)
        if key:
            return user.default_provider

    with_key: list[str] = []
    for provider_id in PROVIDERS:
        if await get_provider_key(session, user.id, provider_id):
            with_key.append(provider_id)
    if not with_key:
        return None
    if len(with_key) == 1:
        return with_key[0]
    return "__choose__"


async def _model_for(session: AsyncSession, user: User, provider_id: str) -> str:
    models = (user.settings or {}).get("models", {})
    return str(models.get(provider_id) or "")


async def _create_project(
    session: AsyncSession, user: User, skill: object, provider_id: str
) -> Project:
    """Создать проект и каталог для материалов."""
    container = get_container()
    project = Project(
        user_id=user.id,
        title=str(getattr(skill, "title", "Ролик")),
        skill_id=str(getattr(skill, "id", "")),
        provider=provider_id,
        aspect=str(getattr(skill, "defaults", {}).get("aspect", "16:9")),
        width=int(getattr(skill, "defaults", {}).get("width", 1920)),
        height=int(getattr(skill, "defaults", {}).get("height", 1080)),
        duration=float(getattr(skill, "defaults", {}).get("duration", 30)),
        status="draft",
    )
    session.add(project)
    await session.flush()

    project_dir = container.settings.projects_dir / str(user.id) / project.id
    (project_dir / "assets").mkdir(parents=True, exist_ok=True)
    project.dir_path = str(project_dir)
    session.add(project)
    return project


def _build_context(
    message: Message, project_id: str, project_dir: Path, options: dict
):
    """Контекст инструментов для агента."""
    from app.connectors.base import ToolContext

    async def notify(text: str) -> None:
        await message.answer(text, parse_mode=None)

    async def progress(pct: int, code: str) -> None:
        await message.answer(f"⏳ Рендер {pct}% ({code})", parse_mode=None)

    return ToolContext(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        project_id=project_id,
        project_dir=project_dir,
        options=options,
        notify=notify,
        progress=progress,
    )


async def _mcp_servers(session: AsyncSession, user_id: int) -> list[dict]:
    config = await get_connector_config(session, user_id, "mcp")
    if config is None:
        return []
    return list((config.options or {}).get("mcp_servers") or [])


async def _log_message(
    session: AsyncSession, user_id: int, role: str, content: str
) -> None:
    session.add(MessageLog(user_id=user_id, role=role, content=content[:8000]))


__all__ = ["router", "MontageFlow", "ChatTurn"]
