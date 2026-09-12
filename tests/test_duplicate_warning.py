"""Tests for the duplicate-entry warning (#58, ADR-0027).

No network and no database: wherever the bot calls find_recent_duplicate it is monkeypatched,
and the tests of the query function itself fake its connection. So these cover everything around
the SQL - the wording, which card appears, what each button does - but not the SQL against real
Postgres, which was checked against the Neon test branch instead (see the PR).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import app.bot_core as bot_core
import app.services.ledger_queries as ledger_queries

# Fictional Telegram user IDs - real ones never enter this public repo.
SENDER = "111111"
OTHER = "222222"


@pytest.fixture(autouse=True)
def household(monkeypatch):
    monkeypatch.setattr(bot_core, "ALLOWED_TG_IDS", {SENDER: "Alice", OTHER: "Bob"})
    monkeypatch.setattr(bot_core, "ACCOUNT_OWNERS", {"Alice": ["Card A"], "Bob": ["Card B"]})


def _earlier(entered_by=OTHER, seconds_ago=40, description="flat white 5.50"):
    """What find_recent_duplicate returns when it finds a match."""
    return {"description": description, "seconds_ago": seconds_ago, "entered_by": entered_by}


def _context():
    context = MagicMock()
    context.user_data = {}
    return context


async def _show_card(monkeypatch, duplicate):
    """Runs process_expense_text for an SGD 5.50 entry sent by SENDER, with the duplicate check
    returning `duplicate`. Returns (context, card text, button row, the faked check)."""
    monkeypatch.setattr(bot_core, "extract_transactions", AsyncMock(return_value={"transactions": [{
        "amount": 5.5, "currency": "SGD", "category": "Food", "date": "2026-09-12",
        "transaction_type": "Expense", "payment_method": None,
    }]}))
    check = MagicMock(return_value=duplicate)
    monkeypatch.setattr(bot_core, "find_recent_duplicate", check)

    update = MagicMock()
    update.effective_user.id = int(SENDER)
    status_msg = MagicMock()
    status_msg.message_id = 500
    status_msg.edit_text = AsyncMock()
    context = _context()

    await bot_core.process_expense_text(update, context, "coffee 5.50", status_msg)

    call = status_msg.edit_text.await_args
    return context, call.args[0], call.kwargs["reply_markup"].inline_keyboard[0], check


def _make_callback(data, message_id=500):
    """A fake button tap. Returns (update, query)."""
    query = MagicMock()
    query.data = data
    query.message.message_id = message_id
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    return update, query


def _flagged_txn(idempotency_key="key-1"):
    """A pending entry of the shape process_expense_text stashes for a flagged card."""
    return {
        "date": "2026-09-12", "description": "coffee 5.50", "amount": 5.5, "category": "Food",
        "currency": "SGD", "transaction_type": "Expense", "payment_method": "Card A",
        "account_owner": "Alice", "idempotency_key": idempotency_key, "entered_by": SENDER,
        "suspected_duplicate": _earlier(),
    }


# --- the wording ---

@pytest.mark.parametrize("seconds, expected", [
    (0, "just now"),
    (1, "1 second ago"),
    (40, "40 seconds ago"),
    (60, "1 minute ago"),
    (150, "2 minutes ago"),
    (-3, "just now"),  # never "-3 seconds ago"
])
def test_format_age(seconds, expected):
    assert bot_core._format_age(seconds) == expected


@pytest.mark.parametrize("entered_by, expected", [
    (SENDER, "You logged SGD 5.50 40 seconds ago."),
    (OTHER, "Bob logged SGD 5.50 40 seconds ago."),
    (None, "SGD 5.50 was logged 40 seconds ago."),       # written before entered_by existed
    ("999999", "SGD 5.50 was logged 40 seconds ago."),   # an ID no longer in the allowlist
])
def test_banner_names_whoever_logged_the_earlier_entry(entered_by, expected):
    banner = bot_core._duplicate_banner(_earlier(entered_by=entered_by), "SGD 5.50", SENDER)

    assert expected in banner
    assert "flat white 5.50" in banner  # the earlier entry's own wording
    assert "999999" not in banner       # a Telegram ID is never shown


# --- which card appears ---

async def test_a_match_gets_the_warning_card(monkeypatch):
    context, text, buttons, _ = await _show_card(monkeypatch, _earlier())

    assert "Possible duplicate" in text
    assert "Bob logged SGD 5.50" in text
    assert [b.text for b in buttons] == ["⚠️ Save anyway", "❌ Cancel"]
    assert [b.callback_data for b in buttons] == ["confirm_save", "cancel_save"]
    assert context.user_data["pending_cards"][500]["suspected_duplicate"] == _earlier()


async def test_no_match_gets_a_normal_card(monkeypatch):
    # Covers every "no match" the query can return: outside the window, a different amount or
    # currency, and a failed check - find_recent_duplicate returns None for all of them.
    context, text, buttons, _ = await _show_card(monkeypatch, None)

    assert "Possible duplicate" not in text
    assert [b.text for b in buttons] == ["✅ Confirm", "❌ Cancel"]
    assert context.user_data["pending_cards"][500]["suspected_duplicate"] is None


async def test_the_check_asks_for_this_amount_in_cents_and_currency(monkeypatch):
    _, _, _, check = await _show_card(monkeypatch, None)

    check.assert_called_once_with(550, "SGD", bot_core.DUPLICATE_WINDOW_MINUTES)
    assert bot_core.DUPLICATE_WINDOW_MINUTES == 5


def test_an_amount_that_cannot_be_converted_skips_the_check(monkeypatch):
    check = MagicMock()
    monkeypatch.setattr(bot_core, "find_recent_duplicate", check)

    assert bot_core._find_duplicate_of({"amount": 0, "currency": "SGD"}) is None
    check.assert_not_called()


# --- the query function, with its connection faked ---

def _fake_connection(monkeypatch, row):
    conn = MagicMock()
    conn.execute.return_value.mappings.return_value.first.return_value = row
    context_manager = MagicMock()
    context_manager.__enter__.return_value = conn
    monkeypatch.setattr(ledger_queries, "get_connection", lambda: context_manager)
    return conn


def test_query_returns_the_match_as_a_dict(monkeypatch):
    conn = _fake_connection(monkeypatch, _earlier(entered_by=SENDER))

    result = ledger_queries.find_recent_duplicate(550, "SGD", 5)

    assert result == _earlier(entered_by=SENDER)
    params = conn.execute.call_args.args[1]
    assert params == {"amount": 550, "currency": "SGD", "within_minutes": 5}


def test_query_returns_none_when_nothing_matches(monkeypatch):
    _fake_connection(monkeypatch, None)

    assert ledger_queries.find_recent_duplicate(550, "SGD", 5) is None


def test_a_failed_query_returns_none_rather_than_raising(monkeypatch):
    def unreachable():
        raise ConnectionError("Neon is down")

    monkeypatch.setattr(ledger_queries, "get_connection", unreachable)

    assert ledger_queries.find_recent_duplicate(550, "SGD", 5) is None


# --- what the buttons on a flagged card do ---

async def test_save_anyway_saves_once_with_the_original_key(monkeypatch):
    saves = MagicMock(return_value=True)
    monkeypatch.setattr(bot_core, "add_expense", saves)
    context = _context()
    bot_core._remember_pending_card(context, 500, _flagged_txn("key-1"))

    for _ in range(3):  # an impatient triple tap
        update, _ = _make_callback("confirm_save")
        await bot_core.handle_button_click(update, context)

    assert saves.call_count == 1
    assert saves.call_args.kwargs["idempotency_key"] == "key-1"
    assert bot_core._card_outcome(context, 500) == "saved"


async def test_cancel_on_a_flagged_card_says_duplicate_cancelled():
    context = _context()
    bot_core._remember_pending_card(context, 500, _flagged_txn())

    update, query = _make_callback("cancel_save")
    await bot_core.handle_button_click(update, context)

    assert "Duplicate transaction cancelled" in query.edit_message_text.await_args.args[0]
    assert context.user_data["pending_cards"] == {}
    assert bot_core._card_outcome(context, 500) == "cancelled"


async def test_cancel_on_a_normal_card_keeps_the_plain_wording():
    context = _context()
    txn = _flagged_txn()
    txn["suspected_duplicate"] = None
    bot_core._remember_pending_card(context, 500, txn)

    update, query = _make_callback("cancel_save")
    await bot_core.handle_button_click(update, context)

    text = query.edit_message_text.await_args.args[0]
    assert "Cancelled." in text
    assert "Duplicate" not in text


async def test_a_failed_save_on_a_flagged_card_brings_back_the_warning_buttons(monkeypatch):
    monkeypatch.setattr(bot_core, "add_expense", MagicMock(return_value=False))
    context = _context()
    bot_core._remember_pending_card(context, 500, _flagged_txn("key-1"))

    update, query = _make_callback("confirm_save")
    await bot_core.handle_button_click(update, context)

    call = query.edit_message_text.await_args
    assert [b.text for b in call.kwargs["reply_markup"].inline_keyboard[0]] == ["⚠️ Save anyway", "❌ Cancel"]
    assert "Save anyway" in call.args[0]  # the retry hint names the button that's actually there
    assert context.user_data["pending_cards"][500]["idempotency_key"] == "key-1"
