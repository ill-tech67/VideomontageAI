from datetime import datetime
from unittest.mock import AsyncMock

from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, Update
from aiogram.types import User as TgUser

from app.middlewares import PhotoMenuMiddleware


async def test_photo_menu_replaced_with_text_message():
    chat = Chat(id=1, type="private")
    user = TgUser(id=1, is_bot=False, first_name="a")
    photo = Message(
        message_id=5, date=datetime.now(), chat=chat,
        photo=[PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)],
    )
    new = Message(message_id=6, date=datetime.now(), chat=chat, text="⏳")
    object.__setattr__(photo, "answer", AsyncMock(return_value=new))
    object.__setattr__(photo, "delete", AsyncMock())
    cb = CallbackQuery(id="1", from_user=user, chat_instance="c", data="menu:help", message=photo)
    update = Update(update_id=1, callback_query=cb)
    seen = {}

    async def handler(event, data):
        seen["message"] = event.callback_query.message

    await PhotoMenuMiddleware()(handler, update, {})
    assert seen["message"] is new


def test_main_menu_buttons_have_emoji_and_valid_callbacks():
    from app.ui import keyboards

    buttons = [b for row in keyboards.main_menu().inline_keyboard for b in row]
    assert len(buttons) == 8
    for b in buttons:
        assert ord(b.text[0]) > 0x2000, f"нет эмодзи: {b.text}"
        assert b.callback_data.startswith("menu:")
        assert len(b.callback_data.encode()) <= 64


def test_no_double_badge_on_emoji_text():
    from app.ui.theme import Button, Style

    assert Button("🏠 В меню", "menu:home", Style.NEUTRAL).label() == "🏠 В меню"
    assert Button("Текст", "x", Style.NEUTRAL).label() == "Текст"


def test_project_file_callback_not_shadowed():
    from app.bot import build_dispatcher
    from app.handlers import projects_menu

    names = [h.callback.__name__ for h in projects_menu.router.callback_query.handlers]
    assert "on_project_file" in names and "on_project" in names
    on_project = next(h for h in projects_menu.router.callback_query.handlers if h.callback.__name__ == "on_project")
    from aiogram.types import CallbackQuery
    from aiogram.types import User as TgUser

    cb = CallbackQuery(id="1", from_user=TgUser(id=1, is_bot=False, first_name="a"), chat_instance="c", data="project:file:abc")
    # фильтры не должны пропускать project:file:* в общий хендлер
    import asyncio
    result = asyncio.run(on_project.filters[0].call(cb)) if on_project.filters else True
    assert not result
    build_dispatcher()
