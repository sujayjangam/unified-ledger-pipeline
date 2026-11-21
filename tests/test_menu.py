"""The button menu under the text box and the "📊 By category" picker (#163).

Fakes only (no network, no database): the summary queries and every command handler are
replaced, so these tests check the routing, never the totals.
"""
from unittest.mock import AsyncMock

import pytest

import app.bot_core as bot_core
from test_bot_core import ALICE, _context, _update


@pytest.fixture(autouse=True)
def _directory(monkeypatch, directory):
    monkeypatch.setattr(bot_core, "_load_directory", lambda: directory)


def _labels(markup):
    return [[button.text for button in row] for row in markup.keyboard]


def _buttons(markup):
    return [(button.text, button.callback_data) for row in markup.inline_keyboard for button in row]


def _tap(data, user_id=ALICE):
    update = _update(user_id)
    query = update.callback_query
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    return update, query


# --- The keyboard itself ------------------------------------------------------------------

def test_the_menu_has_the_agreed_rows():
    """The layout agreed with the user on 2026-11-21."""
    markup = bot_core._menu_keyboard()
    assert _labels(markup) == [
        ["💰 Who owes", "🧳 Trip"],
        ["🤝 Settle", "👪 Family settle"],
        ["🕘 Recent", "❓ Help"],
        ["Today", "This week", "This month"],
        ["📊 By category"],
    ]
    # Persistent and resized: it stays open after a tap and doesn't fill half the screen.
    assert markup.is_persistent and markup.resize_keyboard
    assert len(markup.input_field_placeholder) <= 64  # Telegram's limit


async def test_start_hands_out_the_menu():
    update = _update(ALICE)
    await bot_core.start_command(update, _context())
    reply = update.message.reply_text.await_args
    assert _labels(reply.kwargs["reply_markup"]) == _labels(bot_core._menu_keyboard())


@pytest.mark.parametrize('label,first_line', [
    (bot_core.MENU_SETTLE, bot_core.SETTLE_PROMPT),
    (bot_core.MENU_FAMILY_SETTLE, bot_core.FAMILY_SETTLE_PROMPT),
])
async def test_settle_menu_prompts_authorize_and_force_reply(label, first_line):
    """Both menu prompts are selective ForceReplies and refuse unauthorized senders."""
    update = _update(ALICE, text=label)
    await bot_core.handle_text(update, _context())
    reply = update.message.reply_text.await_args
    assert reply.args[0] == first_line + '\ne.g. Carol paid 381 for dinner'
    assert reply.kwargs['reply_markup'].force_reply and reply.kwargs['reply_markup'].selective
    stranger = _update(999999, text=label)
    await bot_core.handle_text(stranger, _context())
    assert 'not authorized' in stranger.message.reply_text.await_args.args[0]


async def test_a_stranger_gets_no_menu():
    update = _update(999999)
    await bot_core.start_command(update, _context())
    for call in update.message.reply_text.await_args_list:
        assert "reply_markup" not in call.kwargs


# --- A tap is routed to its command, never to the model ------------------------------------

MENU_TO_HANDLER = [
    ("🤝 Settle", "settle_command"),
    ("👪 Family settle", "family_settle_command"),
    ("💰 Who owes", "trip_bals_command"),
    ("🧳 Trip", "trip_command"),
    ("🕘 Recent", "recent_command"),
    ("❓ Help", "help_command"),
    ("Today", "today_command"),
    ("This week", "week_command"),
    ("This month", "month_command"),
    ("📊 By category", "category_menu"),
]


def test_every_label_on_the_keyboard_has_a_handler():
    """A label added to the rows without a handler would be read as an expense."""
    on_keyboard = {label for row in bot_core.MENU_ROWS for label in row}
    assert on_keyboard == {label for label, _ in MENU_TO_HANDLER}


@pytest.mark.parametrize("label, handler", MENU_TO_HANDLER)
async def test_a_menu_tap_runs_its_command_and_never_extracts(monkeypatch, label, handler):
    called = AsyncMock()
    monkeypatch.setattr(bot_core, handler, called)
    extract = AsyncMock()
    monkeypatch.setattr(bot_core, "process_expense_text", extract)
    update = _update(ALICE, text=label)
    context = _context()
    await bot_core.handle_text(update, context)
    called.assert_awaited_once_with(update, context)
    extract.assert_not_awaited()
    # No "🧠 Extracting data..." status message either.
    update.message.reply_text.assert_not_awaited()


@pytest.mark.parametrize("text", [
    "today", "Today 12 coffee", "This week ", "Who owes", "💰 who owes", "Recent", "By category",
])
async def test_text_that_only_looks_like_a_label_is_still_an_expense(monkeypatch, text):
    """Only the exact label is a button; anything else goes through extraction as before."""
    # No prompt is open: each prompt reader says "not a reply to me".
    for reader in ("_settle_up_reply", "_settlement_reply", "_split_amounts_reply"):
        monkeypatch.setattr(bot_core, reader, AsyncMock(return_value=False))
    extract = AsyncMock()
    monkeypatch.setattr(bot_core, "process_expense_text", extract)
    update = _update(ALICE, text=text)
    await bot_core.handle_text(update, _context())
    extract.assert_awaited_once()
    assert extract.await_args.args[2] == text


async def test_a_menu_tap_from_a_stranger_is_refused_by_the_command(monkeypatch):
    """The command authorizes itself (fail closed), exactly as when it is typed."""
    monkeypatch.setattr(bot_core, "get_period_summary", lambda *a: ([], []))
    update = _update(999999, text="Today")
    await bot_core.handle_text(update, _context())
    reply = update.message.reply_text.await_args
    assert "not authorized" in reply.args[0]


async def test_a_menu_tap_beats_an_open_amount_prompt(monkeypatch):
    """A tap while a prompt is open opens the screen; the prompt is never fed the label."""
    called = AsyncMock()
    monkeypatch.setattr(bot_core, "recent_command", called)
    for reader in ("_settle_up_reply", "_settlement_reply", "_split_amounts_reply"):
        monkeypatch.setattr(bot_core, reader, AsyncMock(return_value=True))
    update = _update(ALICE, text="🕘 Recent")
    await bot_core.handle_text(update, _context())
    called.assert_awaited_once()
    bot_core._settle_up_reply.assert_not_awaited()


# --- 📊 By category ------------------------------------------------------------------------

async def test_by_category_offers_the_three_periods():
    update = _update(ALICE, text="📊 By category")
    await bot_core.handle_text(update, _context())
    reply = update.message.reply_text.await_args
    assert reply.args[0] == bot_core.CATEGORY_PICKER_TEXT
    assert _buttons(reply.kwargs["reply_markup"]) == [
        ("Today", "cat:today"), ("This week", "cat:week"), ("This month", "cat:month")]


@pytest.mark.parametrize("period", ["today", "week", "month"])
async def test_a_period_tap_shows_the_same_summary_as_the_command(monkeypatch, period):
    """The tap and /cat_<period> share one builder, so they can't disagree."""
    monkeypatch.setattr(bot_core, "get_category_summary", lambda start, end: [])
    update, query = _tap(f"cat:{period}")
    await bot_core.handle_category_button(update, _context())
    query.answer.assert_awaited_once_with()
    edit = query.edit_message_text.await_args
    assert edit.args[0] == bot_core._category_text(period)
    assert edit.kwargs["parse_mode"] == "Markdown"

    command = _update(ALICE)
    await getattr(bot_core, f"cat_{period}_command")(command, _context())
    assert command.message.reply_text.await_args.args[0] == edit.args[0]


async def test_an_unknown_period_gets_a_toast_and_no_redraw(monkeypatch):
    read = AsyncMock()
    monkeypatch.setattr(bot_core, "get_category_summary", read)
    update, query = _tap("cat:year")
    await bot_core.handle_category_button(update, _context())
    query.answer.assert_awaited_once_with(bot_core.CATEGORY_GONE_TOAST)
    query.edit_message_text.assert_not_awaited()
    read.assert_not_called()


async def test_a_stranger_gets_no_category_summary(monkeypatch):
    read = AsyncMock()
    monkeypatch.setattr(bot_core, "get_category_summary", read)
    update, query = _tap("cat:today", user_id=999999)
    await bot_core.handle_category_button(update, _context())
    query.answer.assert_awaited_once_with()
    query.edit_message_text.assert_not_awaited()
    read.assert_not_called()


async def test_a_failed_redraw_is_swallowed(monkeypatch):
    """A double tap ("message is not modified") must not raise out of the handler."""
    monkeypatch.setattr(bot_core, "get_category_summary", lambda start, end: [])
    update, query = _tap("cat:week")
    query.edit_message_text = AsyncMock(side_effect=RuntimeError("not modified"))
    await bot_core.handle_category_button(update, _context())
    query.answer.assert_awaited_once_with()


def test_category_callbacks_fit_in_callback_data():
    assert all(len(f"cat:{key}".encode()) <= 64 for key in bot_core.CATEGORY_PERIODS)
