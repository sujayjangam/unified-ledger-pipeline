"""/help: the topic buttons and what a tap shows, with fakes only (no network, no database)."""
from unittest.mock import AsyncMock

import pytest

import app.bot_core as bot_core
from app.services.help_topics import INTRO, TOPICS
from test_bot_core import ALICE, _context, _update


@pytest.fixture(autouse=True)
def _directory(monkeypatch, directory):
    monkeypatch.setattr(bot_core, "_load_directory", lambda: directory)


def _tap(data, user_id=ALICE):
    update = _update(user_id)
    query = update.callback_query
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    return update, query


def _buttons(markup):
    return [(button.text, button.callback_data) for row in markup.inline_keyboard for button in row]


async def test_help_command_shows_one_button_per_topic():
    update = _update(ALICE)
    await bot_core.help_command(update, _context())
    reply = update.message.reply_text.await_args
    assert reply.args[0] == INTRO
    assert _buttons(reply.kwargs["reply_markup"]) == [(label, f"help:{key}") for key, (label, _) in TOPICS.items()]
    # Plain text: the examples' apostrophes and quotes can't break a Markdown parse.
    assert "parse_mode" not in reply.kwargs


@pytest.mark.parametrize("key", list(TOPICS))
async def test_a_topic_tap_shows_it_with_a_back_button(key):
    update, query = _tap(f"help:{key}")
    await bot_core.handle_help_button(update, _context())
    query.answer.assert_awaited_once_with()
    edit = query.edit_message_text.await_args
    assert edit.args[0] == TOPICS[key][1]
    assert _buttons(edit.kwargs["reply_markup"]) == [("◀ Back", "help:menu")]


async def test_back_shows_the_topic_list_again():
    update, query = _tap("help:menu")
    await bot_core.handle_help_button(update, _context())
    edit = query.edit_message_text.await_args
    assert edit.args[0] == INTRO
    assert len(_buttons(edit.kwargs["reply_markup"])) == len(TOPICS)


async def test_an_unknown_topic_gets_a_toast_and_no_redraw():
    """An old button from a topic that was later removed."""
    update, query = _tap("help:gone")
    await bot_core.handle_help_button(update, _context())
    query.answer.assert_awaited_once_with(bot_core.HELP_UNKNOWN_TOAST)
    query.edit_message_text.assert_not_awaited()


async def test_a_stranger_gets_no_help():
    update, query = _tap("help:split", user_id=999999)
    await bot_core.handle_help_button(update, _context())
    query.answer.assert_awaited_once_with()
    query.edit_message_text.assert_not_awaited()


async def test_a_failed_redraw_is_swallowed():
    """"Message is not modified" (a double tap) must not raise out of the handler."""
    update, query = _tap("help:dates")
    query.edit_message_text = AsyncMock(side_effect=RuntimeError("not modified"))
    await bot_core.handle_help_button(update, _context())
    query.answer.assert_awaited_once_with()


def test_topic_keys_fit_in_callback_data():
    """Telegram caps callback data at 64 bytes."""
    assert all(len(f"help:{key}".encode()) <= 64 for key in TOPICS)


def test_every_example_is_in_the_model_check():
    """Each "• ..." example must be a message scripts/check_extraction.py runs against the real
    model (ignoring capitals), so the help never promises a wording nobody has checked. Read as
    text: importing the script would load .env and build an OpenAI client."""
    from pathlib import Path
    script = (Path(__file__).parent.parent / "scripts" / "check_extraction.py").read_text(encoding="utf-8").casefold()
    examples = [line.removeprefix("• ") for key, (_, text) in TOPICS.items() if key != 'settle' for line in text.splitlines()
                # An example has an amount and no double quotes; the rule lines quote wordings.
                if line.startswith("• ") and any(ch.isdigit() for ch in line) and '"' not in line]
    assert examples  # the filter below must still be finding them
    missing = [e for e in examples if e.casefold() not in script]
    assert not missing


def test_settle_examples_use_the_python_reader():
    """Settle examples have no model dependency: exercise their actual reader."""
    from test_settle import PEOPLE, FAMILIES
    from app.services.settle import read_settle

    examples = [line.removeprefix('• ') for line in TOPICS['settle'][1].splitlines() if line.startswith('• ')]
    assert len(examples) == 3
    for example in examples:
        assert read_settle(example, PEOPLE, PEOPLE[0], FAMILIES).cents > 0
