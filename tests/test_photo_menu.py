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
