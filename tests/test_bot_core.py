"""Tests for how bot_core uses the participants/accounts Directory (#53).

The rules themselves - which card paid, whose card it is, closed cards, top-ups - are tested on
plain data in tests/test_participants.py. These check the bot around them: who gets in, what the
card says, and what a button tap saves.

No network and no database: bot_core._load_directory is monkeypatched to hand back the fictional
`directory` fixture (tests/conftest.py) or to raise, extraction is faked, and Telegram objects are
MagicMocks - enough here, since this tests our handlers rather than PTB itself (contrast
tests/test_routing.py, which uses real PTB objects).
"""

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock
from datetime import date

import pytest

import app.bot_core as bot_core
from app.services.participants import CLOSED_CARD_WARNING, NO_WALLET_MESSAGE, DirectoryError
from app.services.participants import NO_OUTSIDE_ACCOUNT_MESSAGE, PAYER_UNCLEAR_WARNING
from app.services.participants import drop_unsaid_transfer, says_top_up, says_transfer
from app.services.trips import Trip

# Fictional Telegram IDs, matching the fixture: Alice is 111111, Bob is 222222.
ALICE, BOB = 111111, 222222


def _update(user_id, text=None):
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.text = text
    # Awaited on every rejection path, so it has to be an AsyncMock, not a plain MagicMock.
    update.message.reply_text = AsyncMock()
    update.effective_message = update.message
    return update


def _context():
    context = MagicMock()
    context.user_data = {}
    return context


def _status_msg():
    status_msg = MagicMock()
    status_msg.message_id = 500
    status_msg.edit_text = AsyncMock()
    return status_msg


def _directory_down():
    raise DirectoryError("Participant 1 (Alice) uses the bot but has no default account")


async def _show_card(monkeypatch, directory, sender_id, message="the message", **extracted):
    """Runs process_expense_text for one extracted SGD expense sent by `sender_id`, as if they
    had typed `message`. Returns (context, the card message mock, the faked model call)."""
    transaction = {
        "amount": 12.0, "currency": "SGD", "category": "Food", "date_reference": {"kind": "unstated"},
        "transaction_type": "Expense", "payment_method": None,
    }
    transaction.update(extracted)
    extract = AsyncMock(return_value={"transactions": [transaction]})
    monkeypatch.setattr(bot_core, "extract_transactions", extract)
    monkeypatch.setattr(bot_core, "find_recent_duplicate", MagicMock(return_value=None))

    context, status_msg = _context(), _status_msg()
    await bot_core.process_expense_text(_update(sender_id), context, message, status_msg, directory)
    return context, status_msg, extract


# --- authorize: the allowlist, now read from the database ---

async def test_authorize_returns_the_directory_for_a_known_sender(monkeypatch, directory):
    monkeypatch.setattr(bot_core, "_load_directory", lambda: directory)
    assert await bot_core.authorize(_update(ALICE)) is directory


async def test_authorize_rejects_a_participant_without_a_telegram_id_match(monkeypatch, directory):
    monkeypatch.setattr(bot_core, "_load_directory", lambda: directory)
    update = _update(999999)
    assert await bot_core.authorize(update) is None
    update.message.reply_text.assert_awaited_once()


async def test_authorize_fails_closed_when_the_directory_cannot_load(monkeypatch):
    # Nobody gets in while the directory can't be read: the alternative would let anyone through
    # exactly when the allowlist can't be checked.
    monkeypatch.setattr(bot_core, "_load_directory", _directory_down)
    update = _update(ALICE)
    assert await bot_core.authorize(update) is None
    update.message.reply_text.assert_awaited_once_with("Couldn't reach the ledger, try again in a minute. 🛠️")


async def test_text_handler_does_not_call_the_model_when_the_directory_is_down(monkeypatch):
    monkeypatch.setattr(bot_core, "_load_directory", _directory_down)
    extract = AsyncMock()
    monkeypatch.setattr(bot_core, "extract_transactions", extract)

    await bot_core.handle_text(_update(ALICE, text="lunch 12"), _context())

    extract.assert_not_awaited()


# --- the card ---

async def test_the_model_is_offered_every_account_name(monkeypatch, directory):
    _, _, extract = await _show_card(monkeypatch, directory, ALICE)
    assert extract.await_args.args[2] == ["Card A", "Card B", "Cash", "YouTrip", "Old Card"]


async def test_pending_entry_carries_the_ids(monkeypatch, directory):
    # Bob pays cash: a shared account, so it is Bob's money.
    context, _, _ = await _show_card(monkeypatch, directory, BOB, payment_method="Cash")
    pending = context.user_data["pending_cards"][500]
    assert (pending["account_id"], pending["entered_by_participant_id"]) == (12, 2)
    assert pending["account_owner"] == "Bob"
    assert "entered_by" not in pending


async def test_closed_card_gets_the_warning_and_save_anyway_buttons(monkeypatch, directory):
    # A closed card is used only when the message names it (participants.resolve_payment).
    context, status_msg, _ = await _show_card(
        monkeypatch, directory, ALICE, message="refund 12 old card", payment_method="old card")

    call = status_msg.edit_text.await_args
    assert CLOSED_CARD_WARNING in call.args[0]
    buttons = call.kwargs["reply_markup"].inline_keyboard[0]
    assert [b.text for b in buttons] == ["⚠️ Save anyway", "❌ Cancel"]
    assert [b.callback_data for b in buttons] == ["confirm_save", "cancel_save"]
    assert context.user_data["pending_cards"][500]["closed_account"] is True


async def test_an_open_card_gets_no_closed_warning(monkeypatch, directory):
    _, status_msg, _ = await _show_card(monkeypatch, directory, ALICE, payment_method="Card A")
    assert CLOSED_CARD_WARNING not in status_msg.edit_text.await_args.args[0]


async def test_topup_without_a_wallet_is_refused_and_nothing_is_pending(monkeypatch, directory):
    unfunded = replace(directory, accounts=tuple(
        replace(a, funded_by_account_id=None) if a.name == "YouTrip" else a for a in directory.accounts
    ))
    context, status_msg, _ = await _show_card(monkeypatch, unfunded, ALICE, "top up youtrip 50", category="YouTrip top-up")

    status_msg.edit_text.assert_awaited_once_with(NO_WALLET_MESSAGE)
    assert not context.user_data.get("pending_cards")


# --- the buttons ---

def _pending(**overrides):
    """A pending entry of the shape process_expense_text stashes."""
    txn = {
        "date": "2026-11-11", "description": "refund 20 old card", "amount": 20.0, "category": "Other",
        "currency": "SGD", "transaction_type": "Expense", "payment_method": "Old Card",
        "account_owner": "Alice", "idempotency_key": "key-1",
        "account_id": 14, "entered_by_participant_id": 1, "suspected_duplicate": None,
        "closed_account": False,
    }
    txn.update(overrides)
    return txn


def _tap(data="confirm_save", message_id=500):
    query = MagicMock()
    query.data = data
    query.message.message_id = message_id
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    return update, query


async def test_confirm_saves_without_loading_the_directory(monkeypatch):
    # The pending entry already holds both ids, so a database hiccup between the card and the tap
    # can't change what is saved - and Confirm never needs the directory at all.
    def must_not_load():
        raise AssertionError("Confirm loaded the directory")

    monkeypatch.setattr(bot_core, "_load_directory", must_not_load)
    saves = MagicMock(return_value=True)
    monkeypatch.setattr(bot_core, "add_expense", saves)
    context = _context()
    bot_core._remember_pending_card(context, 500, _pending())

    update, _ = _tap()
    await bot_core.handle_button_click(update, context)

    kwargs = saves.call_args.kwargs
    assert (kwargs["account_id"], kwargs["entered_by_participant_id"]) == (14, 1)
    for key in ("account_desc", "account_owner", "entered_by"):
        assert key not in kwargs


async def test_a_failed_save_on_a_closed_card_brings_back_save_anyway(monkeypatch):
    monkeypatch.setattr(bot_core, "add_expense", MagicMock(return_value=False))
    context = _context()
    bot_core._remember_pending_card(context, 500, _pending(closed_account=True))

    update, query = _tap()
    await bot_core.handle_button_click(update, context)

    call = query.edit_message_text.await_args
    assert [b.text for b in call.kwargs["reply_markup"].inline_keyboard[0]] == ["⚠️ Save anyway", "❌ Cancel"]
    assert "Save anyway" in call.args[0]


@pytest.mark.parametrize("message,members,payer,account,unclear", [
    ("Bob paid 40", None, 2, 11, False),
    ("Carol paid 40", (1, 2, 3), 3, 15, False),
    ("Carol paid 40", (1, 2), 1, 10, True),
    ("Carol paid 40", None, 1, 10, True),
    ("Bob paid 40", (1, 3), 1, 10, True),
])
async def test_payer_candidates_follow_trip_membership(monkeypatch, directory, message, members,
                                                       payer, account, unclear):
    trip = Trip(7, "Beach Trip", date(2026, 12, 1), date(2026, 12, 7), members) if members else None
    monkeypatch.setattr(bot_core, "_load_active_trip", lambda participant_id: trip)
    context, status, _ = await _show_card(monkeypatch, directory, ALICE, message, amount=40)
    entry = context.user_data["pending_cards"][500]
    assert (entry["paid_by_participant_id"], entry["account_id"]) == (payer, account)
    text = status.edit_text.await_args.args[0]
    assert f"Paid by: <b>{directory.participant(payer).display_name}" in text
    assert (PAYER_UNCLEAR_WARNING in text) is unclear
    buttons = status.edit_text.await_args.kwargs["reply_markup"].inline_keyboard[0]
    assert "Confirm" in buttons[0].text
    assert not bot_core._is_flagged(entry)


async def test_failed_trip_read_keeps_both_warnings(monkeypatch, directory):
    def fail(participant_id):
        raise RuntimeError("trip unavailable")

    monkeypatch.setattr(bot_core, "_load_active_trip", fail)
    context, status, _ = await _show_card(monkeypatch, directory, ALICE, "Carol paid 40")
    text = status.edit_text.await_args.args[0]
    assert bot_core.TRIP_UNKNOWN_WARNING in text
    assert PAYER_UNCLEAR_WARNING in text
    assert context.user_data["pending_cards"][500]["paid_by_participant_id"] == 1


async def test_missing_outside_account_refuses_without_stashing(monkeypatch, directory):
    trip = Trip(7, "Beach Trip", date(2026, 12, 1), date(2026, 12, 7), (1, 2, 3))
    monkeypatch.setattr(bot_core, "_load_active_trip", lambda participant_id: trip)
    directory = replace(directory, accounts=tuple(a for a in directory.accounts if a.id != 15))
    context, status, _ = await _show_card(monkeypatch, directory, ALICE, "Carol paid 40")
    status.edit_text.assert_awaited_once_with(NO_OUTSIDE_ACCOUNT_MESSAGE)
    assert not context.user_data.get("pending_cards")


@pytest.mark.parametrize("success", [False, True])
async def test_confirm_preserves_payer_and_only_live_cards_warn(monkeypatch, success):
    save = MagicMock(return_value=success)
    monkeypatch.setattr(bot_core, "add_expense", save)
    context = _context()
    entry = _pending(paid_by_participant_id=2, paid_by="Bob", payer_warning=PAYER_UNCLEAR_WARNING)
    bot_core._remember_pending_card(context, 500, entry)
    update, query = _tap()
    await bot_core.handle_button_click(update, context)
    assert save.call_args.kwargs["paid_by_participant_id"] == 2
    assert "payer_warning" not in save.call_args.kwargs
    text = query.edit_message_text.await_args.args[0]
    assert "Paid by: <b>Bob" in text
    assert (PAYER_UNCLEAR_WARNING in text) is not success
    if not success:
        assert text.endswith("<i>" + PAYER_UNCLEAR_WARNING + "</i>")
        assert "Confirm" in query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0].text


def test_paid_by_name_is_escaped_and_old_cards_fall_back():
    assert r"Paid by: <b>A_name" in bot_core._account_lines({"paid_by": "A_name"})
    assert "Paid by: <b>Alice" in bot_core._account_lines({"account_owner": "Alice"})
    assert "Paid by: <b>Unspecified" in bot_core._account_lines({})


def test_summaries_format_postgres_decimal_sums_through_format_cents():
    # Postgres returns SUM over a BIGINT column as a Decimal; dividing it by a float used to
    # crash /today, /week, /month and the category views whenever there was an entry.
    from decimal import Decimal

    period = bot_core.format_period_summary("Today", [("SGD", 2, Decimal("8000"))], [("SGD", 1, Decimal("1005"))])
    assert "SGD: <b>80.00</b>" in period and "SGD: <b>10.05</b> (1 tx)" in period
    category = bot_core.format_category_summary("Today", [("Food", "SGD", 2, Decimal("8000"))])
    assert "SGD: <b>80.00</b>" in category


# --- a top-up the message never says (ADR-0062) ---

async def test_unsaid_topup_becomes_an_expense_with_every_button(monkeypatch, directory):
    """A money changer isn't a wallet top-up: the card gets its split, payer and card buttons."""
    context, _, _ = await _show_card(monkeypatch, directory, ALICE, "Changed 500sgd to AUD for Carol",
                                     category="YouTrip top-up", transaction_type="Transfer")
    pending = next(iter(context.user_data["pending_cards"].values()))
    assert pending["category"] == "Other" and pending["transaction_type"] == "Expense"
    data = [b.callback_data for row in bot_core._keyboard_for(pending).inline_keyboard for b in row]
    assert {"split:menu", "payer:menu", "card:menu"} <= set(data)


@pytest.mark.parametrize("message", [
    "top up youtrip 200", "Topup 200", "topped-up 50", "topping up 30", "youtrip 100", "TOP UP 20",
])
def test_says_top_up(message):
    assert says_top_up(message)


@pytest.mark.parametrize("message", ["Changed 500sgd to AUD for Carol", "topography book 20", "stop up 5"])
def test_not_a_top_up(message):
    assert not says_top_up(message)


def test_drop_unsaid_transfer_never_makes_a_top_up():
    t = drop_unsaid_transfer({"category": "Food", "transaction_type": "Expense"}, "top up youtrip 50")
    assert t["category"] == "Food"


async def test_unsaid_transfer_becomes_an_expense_with_a_split_button(monkeypatch, directory):
    """A Transfer the message never says keeps its category and gets its split button."""
    context, _, _ = await _show_card(monkeypatch, directory, ALICE, "Changed 500sgd to AUD for Carol",
                                     category="Other", transaction_type="Transfer")
    pending = next(iter(context.user_data["pending_cards"].values()))
    assert pending["category"] == "Other" and pending["transaction_type"] == "Expense"
    data = [b.callback_data for row in bot_core._keyboard_for(pending).inline_keyboard for b in row]
    assert {"split:menu", "payer:menu", "card:menu"} <= set(data)


@pytest.mark.parametrize("message", [
    "transferred 200 to savings", "moved 100 to Card B", "withdrew 300 cash", "ATM 200",
    "paid credit card bill 900", "sent 50 to Bob", "card bill 450", "cc bill 300", "top up youtrip 50", "deposit 100",
])
def test_says_transfer(message):
    assert says_transfer(message)


@pytest.mark.parametrize("message", [
    "Changed 500sgd to AUD for Carol", "electricity bill 120", "movie tickets 30", "dinner 80",
])
def test_not_a_transfer(message):
    assert not says_transfer(message)


def test_a_said_transfer_stays_a_transfer():
    t = drop_unsaid_transfer({"category": "Other", "transaction_type": "Transfer"}, "transferred 200 to Card B")
    assert t["transaction_type"] == "Transfer"
