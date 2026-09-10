"""Tests for the Confirm/Cancel card callbacks in app/bot_core.py.

These cover issue #29 (a double-tap replaced a correct "Saved to Ledger!" card with a false
"session expired" error) and #55 (the card gave no feedback at all until the blocking database
write finished). No network and no database: add_expense is monkeypatched, and the Telegram
Update is a MagicMock, following the is_authorized tests in tests/test_bot_core.py.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import app.bot_core as bot_core


def _make_callback(data="confirm_save", message_id=100, user_data=None):
    """Builds a fake callback Update plus its context.

    Returns (update, context, query) so a test can assert directly on the query's AsyncMocks:
    query.answer is the toast, query.edit_message_text is the card body, and
    query.edit_message_reply_markup is the button row on its own.
    """
    query = MagicMock()
    query.data = data
    query.message.message_id = message_id
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()

    update = MagicMock()
    update.callback_query = query

    context = MagicMock()
    context.user_data = {} if user_data is None else user_data

    return update, context, query


def _fresh_context():
    """A context whose user_data is a real dict, so the cache helpers behave normally."""
    context = MagicMock()
    context.user_data = {}
    return context


def _txn(idempotency_key="key-1"):
    """A minimal transaction of the shape process_expense_text stashes."""
    return {
        "date": "2026-09-10",
        "description": "coffee 5 dollars",
        "amount": 5.0,
        "category": "Food",
        "currency": "SGD",
        "transaction_type": "Expense",
        "payment_method": "Cash",
        "account_owner": "Alice",
        "idempotency_key": idempotency_key,
    }


# --- the #29 regression: a second tap must not overwrite the success card ---

async def test_second_confirm_leaves_the_saved_card_intact(monkeypatch):
    monkeypatch.setattr(bot_core, "add_expense", MagicMock(return_value=True))

    context = _fresh_context()
    bot_core._remember_pending_card(context, 100, _txn())

    update, _, query = _make_callback(message_id=100)
    await bot_core.handle_button_click(update, context)
    assert query.edit_message_text.await_count == 1  # the "Saved to Ledger!" card

    # Second tap on the same card: toast only, card untouched.
    update2, _, query2 = _make_callback(message_id=100)
    await bot_core.handle_button_click(update2, context)

    query2.edit_message_text.assert_not_awaited()
    assert "Already saved" in query2.answer.await_args.args[0]


async def test_double_confirm_saves_exactly_once(monkeypatch):
    saves = MagicMock(return_value=True)
    monkeypatch.setattr(bot_core, "add_expense", saves)

    context = _fresh_context()
    bot_core._remember_pending_card(context, 100, _txn())

    for _ in range(3):
        update, _, _ = _make_callback(message_id=100)
        await bot_core.handle_button_click(update, context)

    assert saves.call_count == 1


# --- #55: feedback lands before the blocking write ---

async def test_progress_keyboard_is_shown_before_the_save(monkeypatch):
    order = []

    def fake_add_expense(**kwargs):
        order.append("save")
        return True

    monkeypatch.setattr(bot_core, "add_expense", fake_add_expense)

    context = _fresh_context()
    bot_core._remember_pending_card(context, 100, _txn())

    update, _, query = _make_callback(message_id=100)
    query.edit_message_reply_markup.side_effect = lambda **kw: order.append("progress_keyboard")

    await bot_core.handle_button_click(update, context)

    # The swap has to happen first, or the button looks dead for the whole DB round trip.
    assert order == ["progress_keyboard", "save"]

    markup = query.edit_message_reply_markup.await_args.kwargs["reply_markup"]
    buttons = markup.inline_keyboard[0]
    assert len(buttons) == 1  # Confirm and Cancel are both gone
    assert buttons[0].callback_data == "saving_in_progress"


async def test_tapping_the_progress_button_only_toasts():
    update, context, query = _make_callback(data="saving_in_progress")

    await bot_core.handle_button_click(update, context)

    query.edit_message_text.assert_not_awaited()
    query.edit_message_reply_markup.assert_not_awaited()
    assert "moment" in query.answer.await_args.args[0]


# --- genuinely lost entries still say so, without assuming voice input ---

async def test_unknown_card_reports_the_entry_is_gone():
    update, context, query = _make_callback(message_id=999)

    await bot_core.handle_button_click(update, context)

    text = query.edit_message_text.await_args.args[0]
    assert "no longer available" in text
    assert "voice" not in text.lower()  # #16 shipped text ingestion; the old wording was stale


# --- cards are independent, which is what multi-expense capture will need ---

async def test_two_live_cards_are_each_confirmable(monkeypatch):
    saves = MagicMock(return_value=True)
    monkeypatch.setattr(bot_core, "add_expense", saves)

    context = _fresh_context()
    bot_core._remember_pending_card(context, 100, _txn("key-A"))
    bot_core._remember_pending_card(context, 200, _txn("key-B"))

    for message_id in (100, 200):
        update, _, _ = _make_callback(message_id=message_id)
        await bot_core.handle_button_click(update, context)

    assert saves.call_count == 2
    saved_keys = [call.kwargs["idempotency_key"] for call in saves.call_args_list]
    assert saved_keys == ["key-A", "key-B"]


# --- a failed save is recoverable ---

async def test_failed_save_restores_the_entry_and_the_buttons(monkeypatch):
    monkeypatch.setattr(bot_core, "add_expense", MagicMock(return_value=False))

    context = _fresh_context()
    bot_core._remember_pending_card(context, 100, _txn("key-1"))

    update, _, query = _make_callback(message_id=100)
    await bot_core.handle_button_click(update, context)

    # Still pending, same key, so a retry cannot write a second row.
    assert context.user_data["pending_cards"][100]["idempotency_key"] == "key-1"
    assert bot_core._card_outcome(context, 100) is None

    markup = query.edit_message_text.await_args.kwargs["reply_markup"]
    assert [b.callback_data for b in markup.inline_keyboard[0]] == ["confirm_save", "cancel_save"]


# --- cancel ---

async def test_cancel_marks_the_card_and_drops_the_entry():
    context = _fresh_context()
    bot_core._remember_pending_card(context, 100, _txn())

    update, _, _ = _make_callback(data="cancel_save", message_id=100)
    await bot_core.handle_button_click(update, context)

    assert context.user_data["pending_cards"] == {}
    assert bot_core._card_outcome(context, 100) == "cancelled"

    # A later Confirm tap on the cancelled card must not resurrect it.
    update2, _, query2 = _make_callback(data="confirm_save", message_id=100)
    await bot_core.handle_button_click(update2, context)

    query2.edit_message_text.assert_not_awaited()
    assert "cancelled" in query2.answer.await_args.args[0].lower()


# --- the caches stay bounded ---

@pytest.mark.parametrize(
    "remember, cache_name",
    [
        (lambda ctx, i: bot_core._remember_pending_card(ctx, i, _txn()), "pending_cards"),
        (lambda ctx, i: bot_core._mark_card_resolved(ctx, i, "saved"), "resolved_cards"),
    ],
)
def test_card_caches_evict_oldest_first(remember, cache_name):
    context = _fresh_context()

    overflow = bot_core._CARD_CACHE_MAXLEN + 5
    for i in range(overflow):
        remember(context, i)

    cache = context.user_data[cache_name]
    assert len(cache) == bot_core._CARD_CACHE_MAXLEN
    assert 0 not in cache  # the oldest went first
    assert overflow - 1 in cache  # the newest is still there


# --- an unrecognised button still clears its spinner ---

async def test_unknown_callback_data_is_still_answered():
    # A card from an older deployment. An unanswered callback query leaves the button
    # spinning on the user's screen forever, so every path has to answer exactly once.
    update, context, query = _make_callback(data="some_retired_button")

    await bot_core.handle_button_click(update, context)

    query.answer.assert_awaited_once()
    query.edit_message_text.assert_not_awaited()
