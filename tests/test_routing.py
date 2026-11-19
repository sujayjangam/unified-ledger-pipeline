from datetime import datetime
from unittest.mock import MagicMock

import pytest

from telegram import CallbackQuery, Chat, Message, MessageEntity, Update, User, Voice

from app.bot_core import (
    get_application,
    handle_button_click,
    handle_trip_button,
    handle_text,
    handle_unsupported,
    handle_voice,
    handle_settle_up_button,
)

# CommandHandler.check_update() calls message.get_bot().username to resolve the
# /command@botname form - a fake bot with just a username is enough, no network involved.
FAKE_BOT = MagicMock()
FAKE_BOT.username = "test_bot"


def _make_update(**message_kwargs):
    chat = Chat(id=1, type="private")
    message = Message(message_id=1, date=datetime.now(), chat=chat, **message_kwargs)
    message.set_bot(FAKE_BOT)
    return Update(update_id=1, message=message)


def _first_matching_handler(app, update):
    for handler in app.handlers[0]:
        if handler.check_update(update):
            return handler.callback
    return None


def test_voice_message_routes_to_handle_voice():
    app = get_application()
    update = _make_update(voice=Voice(file_id="x", file_unique_id="x", duration=1))
    assert _first_matching_handler(app, update) is handle_voice


def test_plain_text_routes_to_handle_text():
    app = get_application()
    update = _make_update(text="lunch 12.50")
    assert _first_matching_handler(app, update) is handle_text


def test_slash_command_does_not_route_to_handle_text():
    app = get_application()
    update = _make_update(
        text="/today",
        entities=[MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=6)],
    )
    assert _first_matching_handler(app, update) is not handle_text


def test_captioned_photo_routes_to_handle_unsupported():
    app = get_application()
    update = _make_update(caption="dinner", photo=())
    assert _first_matching_handler(app, update) is handle_unsupported


@pytest.mark.parametrize("data,expected", [
    ("confirm_save", handle_button_click), ("trip_toggle", handle_button_click),
    ("trip:on:7", handle_trip_button), ("trip:off", handle_trip_button),
    ("trip:page:3", handle_trip_button),
    ("su:confirm", handle_settle_up_button),
])
def test_button_press_routes_to_correct_handler(data, expected):
    # The CallbackQueryHandler is registered last and has no pattern=, so it must not be
    # shadowed by any of the MessageHandlers above it.
    app = get_application()
    chat = Chat(id=1, type="private")
    card = Message(message_id=1, date=datetime.now(), chat=chat, text="Please confirm")
    card.set_bot(FAKE_BOT)
    query = CallbackQuery(
        id="1",
        from_user=User(id=1, first_name="Test", is_bot=False),
        chat_instance="1",
        data=data,
        message=card,
    )
    update = Update(update_id=1, callback_query=query)
    assert _first_matching_handler(app, update) is expected


def test_pinned_message_notice_routes_to_delete_pin_notice():
    # Pinning the trip banner makes Telegram send a service update from the bot itself. It must
    # reach delete_pin_notice, never handle_unsupported (which would answer "not authorized").
    from app.bot_core import delete_pin_notice
    app = get_application()
    chat = Chat(id=1, type="private")
    pinned = Message(message_id=2, date=datetime.now(), chat=chat, text="banner")
    update = _make_update(pinned_message=pinned)
    assert _first_matching_handler(app, update) is delete_pin_notice
