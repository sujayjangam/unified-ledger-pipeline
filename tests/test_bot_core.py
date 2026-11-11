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

import app.bot_core as bot_core
from app.services.participants import CLOSED_CARD_WARNING, NO_WALLET_MESSAGE, DirectoryError

# Fictional Telegram IDs, matching the fixture: Alice is 111111, Bob is 222222.
ALICE, BOB = 111111, 222222


def _update(user_id, text=None):
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.text = text
    # Awaited on every rejection path, so it has to be an AsyncMock, not a plain MagicMock.
    update.message.reply_text = AsyncMock()
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


async def _show_card(monkeypatch, directory, sender_id, **extracted):
    """Runs process_expense_text for one extracted SGD expense sent by `sender_id`.
    Returns (context, the card message mock, the faked model call)."""
    transaction = {
        "amount": 12.0, "currency": "SGD", "category": "Food", "date_reference": {"kind": "unstated"},
        "transaction_type": "Expense", "payment_method": None,
    }
    transaction.update(extracted)
    extract = AsyncMock(return_value={"transactions": [transaction]})
    monkeypatch.setattr(bot_core, "extract_transactions", extract)
    monkeypatch.setattr(bot_core, "find_recent_duplicate", MagicMock(return_value=None))

    context, status_msg = _context(), _status_msg()
    await bot_core.process_expense_text(_update(sender_id), context, "the message", status_msg, directory)
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
    assert (pending["account_owner"], pending["entered_by"]) == ("Bob", "222222")


async def test_closed_card_gets_the_warning_and_save_anyway_buttons(monkeypatch, directory):
    context, status_msg, _ = await _show_card(monkeypatch, directory, ALICE, payment_method="old card")

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
    context, status_msg, _ = await _show_card(monkeypatch, unfunded, ALICE, category="YouTrip top-up")

    status_msg.edit_text.assert_awaited_once_with(NO_WALLET_MESSAGE)
    assert not context.user_data.get("pending_cards")


# --- the buttons ---

def _pending(**overrides):
    """A pending entry of the shape process_expense_text stashes."""
    txn = {
        "date": "2026-11-11", "description": "refund 20 old card", "amount": 20.0, "category": "Other",
        "currency": "SGD", "transaction_type": "Expense", "payment_method": "Old Card",
        "account_owner": "Alice", "idempotency_key": "key-1", "entered_by": "111111",
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
    # Still written until the old columns are dropped, so reverting this deploy is safe.
    assert (kwargs["account_desc"], kwargs["account_owner"], kwargs["entered_by"]) == ("Old Card", "Alice", "111111")


async def test_a_failed_save_on_a_closed_card_brings_back_save_anyway(monkeypatch):
    monkeypatch.setattr(bot_core, "add_expense", MagicMock(return_value=False))
    context = _context()
    bot_core._remember_pending_card(context, 500, _pending(closed_account=True))

    update, query = _tap()
    await bot_core.handle_button_click(update, context)

    call = query.edit_message_text.await_args
    assert [b.text for b in call.kwargs["reply_markup"].inline_keyboard[0]] == ["⚠️ Save anyway", "❌ Cancel"]
    assert "Save anyway" in call.args[0]
