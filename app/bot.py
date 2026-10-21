"""Сборка и запуск бота."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.dispatcher.router import Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent, Message

from app.config import get_settings
from app.container import build_container, set_container
from app.db.base import close_db, init_db
from app.handlers import (
    ai_menu,
    connectors_menu,
    main_menu,
    montage,
    projects_menu,
    start,
)
from app.middlewares import AccessMiddleware, UserMiddleware
from app.ui import texts
from app.ui.theme import Style

logger = logging.getLogger(__name__)


async def _on_cancel(message: Message, state: FSMContext) -> None:
    """Команда /cancel."""
    await state.clear()
    await message.answer(texts.CANCELLED, parse_mode="HTML")


async def _on_error(event: ErrorEvent) -> None:
    """Глобальный обработчик ошибок."""
    logger.exception("Ошибка при обработке апдейта: %s", event.exception)
    message: Message | None = event.update.message if event.update else None
    if message is not None:
        await message.answer(
            "❌ <b>Что-то пошло не так</b>\n\n"
            "Ошибка зафиксирована, попробуйте ещё раз или вернитесь в меню: /menu",
            parse_mode="HTML",
        )


def clone_router(source: Router) -> Router:
    """Копия роутера.

    aiogram разрешает подключить роутер только к одному Dispatcher, а боту
    нужно собирать диспетчер заново (например, в тестах).
    """
    clone = Router(name=source.name)
    for event_type, observer in source.observers.items():
        target = clone.observers[event_type]
        target.handlers = list(observer.handlers)
        target.middleware = observer.middleware
        target.outer_middleware = observer.outer_middleware
    return clone


def build_dispatcher() -> Dispatcher:
    """Собрать диспетчер со всеми роутерами."""
    dispatcher = Dispatcher(storage=MemoryStorage())

    # В aiogram внешние middleware выполняются в порядке регистрации:
    # первый зарегистрированный — самый внешний. Пользователь должен быть
    # загружен до проверки доступа.
    dispatcher.update.outer_middleware(UserMiddleware())
    dispatcher.update.outer_middleware(AccessMiddleware())

    dispatcher.include_router(clone_router(start.router))
    dispatcher.include_router(clone_router(main_menu.router))
    dispatcher.include_router(clone_router(ai_menu.router))
    dispatcher.include_router(clone_router(connectors_menu.router))
    dispatcher.include_router(clone_router(montage.router))
    dispatcher.include_router(clone_router(projects_menu.router))

    dispatcher.message.register(_on_cancel, F.text.regexp(r"^/cancel(@\w+)?$"))
    dispatcher.errors.register(_on_error)
    return dispatcher


async def _run_webhook(bot: Bot, dispatcher: Dispatcher, settings) -> None:  # noqa: ANN001
    """Режим webhook: aiogram 3 не имеет dp.start_webhook — поднимаем aiohttp."""
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
    from aiohttp import web

    if not settings.webhook_url:
        raise RuntimeError("Для webhook-режима укажите WEBHOOK_URL в .env")

    await bot.set_webhook(
        settings.webhook_url,
        secret_token=settings.webhook_secret or None,
        drop_pending_updates=True,
    )

    app = web.Application()
    app.router.add_route(
        "POST",
        "/webhook",
        SimpleRequestHandler(
            dispatcher=dispatcher,
            bot=bot,
            secret_token=settings.webhook_secret or None,
        ),
    )
    setup_application(app, dispatcher, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logger.info("Webhook запущен: %s", settings.webhook_url)
    await asyncio.Event().wait()


async def run() -> None:
    """Точка входа."""
    settings = get_settings()
    if not settings.bot_token or settings.bot_token.startswith("123456789"):
        raise RuntimeError(
            "BOT_TOKEN не задан. Скопируйте .env.example в .env и укажите токен от @BotFather."
        )

    settings.ensure_dirs()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    await init_db()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    container = build_container(bot)
    set_container(container)

    dispatcher = build_dispatcher()
    me = await bot.get_me()
    logger.info("Бот запущен: @%s (%s)", me.username, me.full_name)

    try:
        if settings.long_polling:
            await dispatcher.start_polling(
                bot,
                allowed_updates=dispatcher.resolve_used_update_types(),
                drop_pending_updates=True,
            )
        else:
            await _run_webhook(bot, dispatcher, settings)
    finally:
        await bot.session.close()
        await close_db()


__all__ = ["run", "build_dispatcher", "Style"]
