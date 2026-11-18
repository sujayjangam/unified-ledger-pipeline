"""Tests for /recent and its delete and undo buttons (ADR-0050, #115).

No network and no database: the four ledger queries bot_core calls are monkeypatched, the
directory is the fictional fixture, and the Telegram Update is a MagicMock, as in
tests/test_button_callback.py. The query functions themselves are checked against a fake
connection, for the SQL they send.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.bot_core as bot_core
import app.services.ledger_queries as ledger_queries

TODAY = date(2026, 11, 18)
TID = "0b8f6f0e-3c1e-4a57-9d4e-7d2f1c9a0b11"


def _entry(tid=TID, **overrides):
    entry = {
        "transaction_id": tid, "date": "2026-11-18", "description": "dinner at hawker",
        "amount": 4600, "currency": "SGD", "category": "Food", "transaction_type": "Expense",
        "entered_by_participant_id": 2, "trip_name": None, "split_count": 0,
    }
    entry.update(overrides)
    return entry


@pytest.fixture(autouse=True)
def _setup(monkeypatch, directory):
    monkeypatch.setattr(bot_core, "_load_directory", lambda: directory)
    monkeypatch.setattr(bot_core, "get_sgt_now", lambda: MagicMock(date=lambda: TODAY))


def _tap(data):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    update.effective_user.id = 111111  # Alice
    update.effective_message.reply_text = AsyncMock()
    return update, MagicMock(), query


def _buttons(keyboard):
    return [[(b.text, b.callback_data) for b in row] for row in keyboard.inline_keyboard]


def _page(entries, more=False):
    return MagicMock(return_value=(entries, more))


# --- the list -----------------------------------------------------------------------------

async def test_recent_lists_entries_with_a_delete_button_each(monkeypatch):
    entries = [_entry(f"id-{n}", description=f"item {n}") for n in range(1, 6)]
    get_page = _page(entries, more=True)
    monkeypatch.setattr(bot_core, "get_recent_page", get_page)
    update, context, _ = _tap(None)

    await bot_core.recent_command(update, context)

    get_page.assert_called_once_with(0, bot_core.RECENT_PAGE_SIZE)
    text = update.effective_message.reply_text.await_args.args[0]
    kwargs = update.effective_message.reply_text.await_args.kwargs
    assert "parse_mode" not in kwargs  # raw descriptions must not break Markdown
    assert "1. 18 Nov · item 1" in text and "5. 18 Nov · item 5" in text
    assert "SGD 46.00 · logged by Bob" in text
    rows = _buttons(kwargs["reply_markup"])
    assert rows[0] == [(f"🗑 {n}", f"recent:ask:0:id-{n}") for n in range(1, 6)]
    assert rows[1] == [("Older ▸", "recent:page:5")]


async def test_list_marks_transfers_trips_and_splits_and_shows_other_years(monkeypatch):
    monkeypatch.setattr(bot_core, "get_recent_page", _page([
        _entry("a", category="YouTrip top-up", transaction_type="Transfer", description="youtrip top up"),
        _entry("b", trip_name="Bali", split_count=3, date="2025-12-01", entered_by_participant_id=None),
    ]))
    update, context, _ = _tap(None)
    await bot_core.recent_command(update, context)
    text = update.effective_message.reply_text.await_args.args[0]
    assert "18 Nov · youtrip top up [Transfer]" in text
    assert "01 Dec 2025 · dinner at hawker 🧳 ✂️" in text
    # No sender recorded: no "logged by" rather than a guess.
    assert text.splitlines()[-1] == "   SGD 46.00"


async def test_a_failed_read_is_an_error_never_no_entries(monkeypatch):
    monkeypatch.setattr(bot_core, "get_recent_page", MagicMock(return_value=None))
    update, context, _ = _tap(None)
    await bot_core.recent_command(update, context)
    assert update.effective_message.reply_text.await_args.args[0] == bot_core.RECENT_ERROR


async def test_empty_ledger_says_so_with_no_buttons(monkeypatch):
    monkeypatch.setattr(bot_core, "get_recent_page", _page([]))
    update, context, _ = _tap(None)
    await bot_core.recent_command(update, context)
    assert update.effective_message.reply_text.await_args.args[0] == bot_core.RECENT_EMPTY
    assert update.effective_message.reply_text.await_args.kwargs["reply_markup"] is None


async def test_paging_shows_newer_and_older(monkeypatch):
    get_page = _page([_entry()], more=True)
    monkeypatch.setattr(bot_core, "get_recent_page", get_page)
    update, context, query = _tap("recent:page:5")
    await bot_core.handle_recent_button(update, context)
    get_page.assert_called_once_with(5, bot_core.RECENT_PAGE_SIZE)
    assert "(6–6)" in query.edit_message_text.await_args.args[0]
    rows = _buttons(query.edit_message_text.await_args.kwargs["reply_markup"])
    assert rows[0] == [("🗑 6", f"recent:ask:5:{TID}")]
    assert rows[1] == [("◂ Newer", "recent:page:0"), ("Older ▸", "recent:page:10")]


async def test_a_page_emptied_by_deletes_falls_back_to_the_newest(monkeypatch):
    get_page = MagicMock(side_effect=[([], False), ([_entry()], False)])
    monkeypatch.setattr(bot_core, "get_recent_page", get_page)
    update, context, query = _tap("recent:page:5")
    await bot_core.handle_recent_button(update, context)
    assert [c.args[0] for c in get_page.call_args_list] == [5, 0]
    assert "(1–1)" in query.edit_message_text.await_args.args[0]


# --- delete: two taps ---------------------------------------------------------------------

async def test_first_tap_asks_and_changes_nothing(monkeypatch):
    monkeypatch.setattr(bot_core, "get_live_entry", MagicMock(return_value=_entry(trip_name="Bali", split_count=3)))
    delete = MagicMock()
    monkeypatch.setattr(bot_core, "soft_delete_entry", delete)
    update, context, query = _tap(f"recent:ask:5:{TID}")

    await bot_core.handle_recent_button(update, context)

    delete.assert_not_called()
    text = query.edit_message_text.await_args.args[0]
    assert text.startswith("Delete this entry?")
    assert "Bali trip · split 3 ways" in text and "Logged by Bob" in text
    assert "It leaves the totals and /trip_bals." in text
    rows = _buttons(query.edit_message_text.await_args.kwargs["reply_markup"])
    assert rows == [[("Yes, delete", f"recent:del:5:{TID}"), ("◂ Back", "recent:page:5")]]


async def test_an_entry_off_trip_does_not_mention_trip_bals(monkeypatch):
    monkeypatch.setattr(bot_core, "get_live_entry", MagicMock(return_value=_entry()))
    update, context, query = _tap(f"recent:ask:0:{TID}")
    await bot_core.handle_recent_button(update, context)
    text = query.edit_message_text.await_args.args[0]
    assert "It leaves the totals." in text and "/trip_bals" not in text


async def test_yes_deletes_as_the_sender_and_offers_undo(monkeypatch):
    monkeypatch.setattr(bot_core, "get_live_entry", MagicMock(return_value=_entry()))
    delete = MagicMock(return_value=True)
    monkeypatch.setattr(bot_core, "soft_delete_entry", delete)
    update, context, query = _tap(f"recent:del:5:{TID}")

    await bot_core.handle_recent_button(update, context)

    delete.assert_called_once_with(TID, 1)  # Alice's participant id, not her Telegram id
    query.answer.assert_awaited_once_with(bot_core.RECENT_DELETED_TOAST)
    assert query.edit_message_text.await_args.args[0].startswith("🗑 Deleted: 18 Nov · dinner at hawker")
    rows = _buttons(query.edit_message_text.await_args.kwargs["reply_markup"])
    assert rows == [[("↩ Undo", f"recent:undo:5:{TID}"), ("◂ Back to list", "recent:page:5")]]


async def test_a_delete_button_on_an_old_list_gets_a_toast_and_the_fresh_list(monkeypatch):
    monkeypatch.setattr(bot_core, "get_live_entry", MagicMock(return_value={}))
    delete = MagicMock()
    monkeypatch.setattr(bot_core, "soft_delete_entry", delete)
    monkeypatch.setattr(bot_core, "get_recent_page", _page([_entry("other")]))
    update, context, query = _tap(f"recent:ask:0:{TID}")

    await bot_core.handle_recent_button(update, context)

    delete.assert_not_called()
    query.answer.assert_awaited_once_with(bot_core.RECENT_GONE_TOAST)
    assert query.edit_message_text.await_args.args[0].startswith("📊 Recent entries")


@pytest.mark.parametrize("live, deleted", [({}, None), (_entry(), False)])
async def test_a_second_yes_tap_leaves_the_undo_message_alone(monkeypatch, live, deleted):
    """A double-tapped Yes: the first tap drew "Deleted" with Undo. The second (entry already
    gone, or it lost the UPDATE race) must only toast, or it would wipe the Undo button."""
    monkeypatch.setattr(bot_core, "get_live_entry", MagicMock(return_value=live))
    monkeypatch.setattr(bot_core, "soft_delete_entry", MagicMock(return_value=deleted))
    monkeypatch.setattr(bot_core, "get_recent_page", MagicMock(side_effect=AssertionError("redrew")))
    update, context, query = _tap(f"recent:del:0:{TID}")
    await bot_core.handle_recent_button(update, context)
    query.answer.assert_awaited_once_with(bot_core.RECENT_GONE_TOAST)
    query.edit_message_text.assert_not_awaited()


@pytest.mark.parametrize("read, write", [(None, None), ("live", None)])
async def test_a_failed_read_or_write_changes_nothing_and_says_so(monkeypatch, read, write):
    monkeypatch.setattr(bot_core, "get_live_entry", MagicMock(return_value=_entry() if read else None))
    monkeypatch.setattr(bot_core, "soft_delete_entry", MagicMock(return_value=write))
    update, context, query = _tap(f"recent:del:0:{TID}")
    await bot_core.handle_recent_button(update, context)
    assert query.answer.await_args.args[0] in (bot_core.RECENT_ERROR, bot_core.RECENT_CHANGE_ERROR)
    # The confirm screen stays as it was, so Yes can be tapped again.
    query.edit_message_text.assert_not_awaited()


# --- undo ---------------------------------------------------------------------------------

@pytest.mark.parametrize("restored, toast", [
    (True, bot_core.RECENT_RESTORED_TOAST), (False, bot_core.RECENT_ALREADY_LIVE_TOAST)])
async def test_undo_restores_and_goes_back_to_the_list(monkeypatch, restored, toast):
    restore = MagicMock(return_value=restored)
    monkeypatch.setattr(bot_core, "restore_entry", restore)
    monkeypatch.setattr(bot_core, "get_recent_page", _page([_entry()]))
    update, context, query = _tap(f"recent:undo:5:{TID}")

    await bot_core.handle_recent_button(update, context)

    restore.assert_called_once_with(TID)
    query.answer.assert_awaited_once_with(toast)
    assert query.edit_message_text.await_args.args[0].startswith("📊 Recent entries")


async def test_an_undo_that_committed_drops_its_button_when_the_list_fails(monkeypatch):
    monkeypatch.setattr(bot_core, "restore_entry", MagicMock(return_value=True))
    monkeypatch.setattr(bot_core, "get_recent_page", MagicMock(return_value=None))
    update, context, query = _tap(f"recent:undo:0:{TID}")
    await bot_core.handle_recent_button(update, context)
    query.answer.assert_awaited_once_with(bot_core.RECENT_RESTORED_TOAST)
    assert query.edit_message_text.await_args.kwargs["reply_markup"] is None


async def test_a_failed_undo_keeps_the_undo_button(monkeypatch):
    monkeypatch.setattr(bot_core, "restore_entry", MagicMock(return_value=None))
    update, context, query = _tap(f"recent:undo:0:{TID}")
    await bot_core.handle_recent_button(update, context)
    query.answer.assert_awaited_once_with(bot_core.RECENT_CHANGE_ERROR)
    query.edit_message_text.assert_not_awaited()


# --- guards -------------------------------------------------------------------------------

@pytest.mark.parametrize("data", [
    "recent:page:-1", "recent:page:x", "recent:page:0:extra", "recent:del:0", "recent:del:0:",
    "recent:drop:0:" + TID, "recent:del:0:" + "x" * 65,
])
async def test_bad_callback_data_is_refused_without_touching_the_ledger(monkeypatch, data):
    for name in ("get_recent_page", "get_live_entry", "soft_delete_entry", "restore_entry"):
        monkeypatch.setattr(bot_core, name, MagicMock(side_effect=AssertionError(name)))
    update, context, query = _tap(data)
    await bot_core.handle_recent_button(update, context)
    query.answer.assert_awaited_once_with(bot_core.RECENT_ERROR)
    query.edit_message_text.assert_not_awaited()


async def test_a_stranger_cannot_delete(monkeypatch):
    monkeypatch.setattr(bot_core, "soft_delete_entry", MagicMock(side_effect=AssertionError("deleted")))
    update, context, query = _tap(f"recent:del:0:{TID}")
    update.effective_user.id = 999999
    await bot_core.handle_recent_button(update, context)
    query.answer.assert_awaited_once_with()


def test_an_id_too_long_for_a_button_gets_no_button():
    text, keyboard = bot_core._recent_list([_entry("x" * 60)], False, 0, {}, TODAY)
    assert "1. 18 Nov" in text and keyboard is None


def test_long_descriptions_are_cut_in_the_list():
    text, _ = bot_core._recent_list([_entry(description="y" * 200)], False, 0, {}, TODAY)
    assert ("y" * 59 + "…") in text and ("y" * 60) not in text


# --- the queries' SQL ---------------------------------------------------------------------

def _fake_connection(monkeypatch, rowcount=1):
    conn = MagicMock()
    conn.execute.return_value.rowcount = rowcount
    conn.__enter__.return_value = conn
    monkeypatch.setattr(ledger_queries, "get_connection", lambda: conn)
    return conn


def _sql(conn):
    return " ".join(str(conn.execute.call_args.args[0]).split())


def test_reads_go_through_the_view_never_the_raw_table(monkeypatch):
    conn = _fake_connection(monkeypatch)
    ledger_queries.get_recent_page(5, 5)
    assert "FROM transactions t" in _sql(conn) and "raw_transactions" not in _sql(conn)
    assert conn.execute.call_args.args[1] == {"limit": 6, "offset": 5}  # one extra, for "more"
    ledger_queries.get_live_entry(TID)
    assert "FROM transactions t" in _sql(conn) and "raw_transactions" not in _sql(conn)


def test_delete_marks_only_a_live_row_and_records_who(monkeypatch):
    conn = _fake_connection(monkeypatch)
    assert ledger_queries.soft_delete_entry(TID, 1) is True
    sql = _sql(conn)
    assert sql.startswith("UPDATE raw_transactions SET deleted_at = now(), deleted_by_participant_id = :participant_id")
    assert sql.endswith("AND deleted_at IS NULL")
    assert conn.execute.call_args.args[1] == {"transaction_id": TID, "participant_id": 1}
    conn.commit.assert_called_once_with()


def test_undo_clears_both_columns_on_a_deleted_row_only(monkeypatch):
    conn = _fake_connection(monkeypatch, rowcount=0)
    assert ledger_queries.restore_entry(TID) is False
    sql = _sql(conn)
    assert "SET deleted_at = NULL, deleted_by_participant_id = NULL" in sql
    assert sql.endswith("AND deleted_at IS NOT NULL")


@pytest.mark.parametrize("call", [
    lambda: ledger_queries.get_recent_page(0, 5),
    lambda: ledger_queries.get_live_entry(TID),
    lambda: ledger_queries.soft_delete_entry(TID, 1),
    lambda: ledger_queries.restore_entry(TID),
])
def test_every_query_returns_none_on_failure(call):
    # The autouse conftest fixture makes any real connection raise.
    assert call() is None
