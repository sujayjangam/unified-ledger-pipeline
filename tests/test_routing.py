from datetime import datetime
from unittest.mock import MagicMock

from telegram import Chat, Message, MessageEntity, Update, Voice

from app.bot_core import get_application, handle_text, handle_unsupported, handle_voice

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
