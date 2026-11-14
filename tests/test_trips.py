"""Trip queries, /trip commands, and expense-card tagging, toggling and saving without I/O."""

from unittest.mock import MagicMock

import pytest

import app.bot_core as bot_core
from app.services import trips
from test_bot_core import ALICE, _context, _pending, _show_card, _tap, _update


BEACH = trips.Trip(7, "Beach Trip")


@pytest.mark.parametrize("row, expected", [(None, None), ((7, "Beach Trip"), BEACH)])
def test_load_active_trip(row, expected):
    conn = MagicMock()
    conn.execute.return_value.first.return_value = row
    assert trips.load_active_trip(conn) == expected
    assert "WHERE active" in str(conn.execute.call_args.args[0])
    conn.execute.assert_called_once()
    conn.commit.assert_not_called()


@pytest.mark.parametrize("rows, expected", [([], []), ([("Beach Trip",), ("Alice Trip",)], ["Beach Trip", "Alice Trip"])])
def test_trip_names_preserves_database_order(rows, expected):
    conn = MagicMock()
    conn.execute.return_value.all.return_value = rows
    assert trips.trip_names(conn) == expected
    assert "ORDER BY id" in str(conn.execute.call_args.args[0])
    conn.commit.assert_not_called()


def test_unknown_trip_does_not_update_or_commit():
    conn = MagicMock()
    conn.execute.return_value.first.return_value = None
    assert trips.switch_trip_on(conn, "Unknown Trip") is None
    conn.execute.assert_called_once()
    assert str(conn.execute.call_args.args[0]).startswith("SELECT")
    conn.commit.assert_not_called()


def test_switch_on_turns_others_off_before_turning_on_and_committing():
    conn = MagicMock()
    conn.execute.return_value.first.return_value = (7, "Beach Trip")
    assert trips.switch_trip_on(conn, "  beach TRIP  ") == BEACH
    calls = conn.execute.call_args_list
    assert len(calls) == 3
    assert "lower(name) = lower(:name)" in str(calls[0].args[0])
    assert calls[0].args[1] == {"name": "beach TRIP"}
    assert str(calls[1].args[0]) == "UPDATE trips SET active = false WHERE active AND id <> :id"
    assert str(calls[2].args[0]) == "UPDATE trips SET active = true WHERE id = :id"
    assert calls[1].args[1] == calls[2].args[1] == {"id": 7}
    # The unique active-trip index requires two updates in this order, before commit.
    assert [c[0] for c in conn.method_calls] == ["execute", "execute", "execute", "commit"]
    conn.commit.assert_called_once_with()


@pytest.mark.parametrize("row, expected", [(None, None), ((7, "Beach Trip"), BEACH)])
def test_switch_off_returns_previous_trip_and_commits(row, expected):
    conn = MagicMock()
    conn.execute.return_value.first.return_value = row
    assert trips.switch_trip_off(conn) == expected
    conn.execute.assert_called_once()
    assert str(conn.execute.call_args.args[0]) == "UPDATE trips SET active = false WHERE active RETURNING id, name"
    conn.commit.assert_called_once_with()


@pytest.fixture
def command_db(monkeypatch, directory):
    monkeypatch.setattr(bot_core, "_load_directory", lambda: directory)
    manager = MagicMock()
    monkeypatch.setattr(bot_core, "get_connection", lambda: manager)
    operations = {}
    for name in ("load_active_trip", "switch_trip_on", "switch_trip_off", "trip_names"):
        operations[name] = MagicMock()
        monkeypatch.setattr(bot_core, name, operations[name])
    return manager.__enter__.return_value, operations


async def _command(args):
    update, context = _update(ALICE), _context()
    context.args = args
    await bot_core.trip_command(update, context)
    update.message.reply_text.assert_awaited_once()
    return update.message.reply_text.await_args


@pytest.mark.parametrize("trip, status", [(BEACH, "Active trip: **Beach Trip**"), (None, "No trip is on")])
async def test_trip_command_status(command_db, trip, status):
    conn, operations = command_db
    operations["load_active_trip"].return_value = trip
    reply = await _command([])
    assert status in reply.args[0]
    assert bot_core.TRIP_USAGE in reply.args[0]
    operations["load_active_trip"].assert_called_once_with(conn)
    operations["switch_trip_on"].assert_not_called()
    operations["switch_trip_off"].assert_not_called()


async def test_trip_command_on_joins_multiword_name(command_db):
    conn, operations = command_db
    operations["switch_trip_on"].return_value = BEACH
    reply = await _command(["on", "Beach", "Trip"])
    operations["switch_trip_on"].assert_called_once_with(conn, "Beach Trip")
    assert "**Beach Trip** is on" in reply.args[0]


@pytest.mark.parametrize("names, choices", [(["Beach Trip", "Alice Trip"], "Beach Trip, Alice Trip"), ([], "none set up yet")])
async def test_trip_command_unknown_name_is_plain_text(command_db, names, choices):
    conn, operations = command_db
    operations["switch_trip_on"].return_value = None
    operations["trip_names"].return_value = names
    reply = await _command(["on", "Unknown_*"])
    assert "Unknown_*" in reply.args[0]
    assert "Nothing changed" in reply.args[0]
    assert f"Trips: {choices}" in reply.args[0]
    assert reply.kwargs["parse_mode"] is None
    operations["trip_names"].assert_called_once_with(conn)


@pytest.mark.parametrize("trip, status", [(BEACH, "**Beach Trip** is off"), (None, "No trip was on")])
async def test_trip_command_off(command_db, trip, status):
    conn, operations = command_db
    operations["switch_trip_off"].return_value = trip
    reply = await _command(["off"])
    assert status in reply.args[0]
    operations["switch_trip_off"].assert_called_once_with(conn)


@pytest.mark.parametrize("args", [["bad"], ["on"]])
async def test_trip_command_bad_action_shows_usage(command_db, args):
    _, operations = command_db
    reply = await _command(args)
    assert reply.args[0] == bot_core.TRIP_USAGE
    for operation in operations.values():
        operation.assert_not_called()


@pytest.mark.parametrize("args, operation", [([], "load_active_trip"), (["on", "Beach", "Trip"], "switch_trip_on"), (["off"], "switch_trip_off")])
async def test_trip_command_database_error_says_nothing_changed(command_db, args, operation):
    _, operations = command_db
    operations[operation].side_effect = RuntimeError("database unavailable")
    reply = await _command(args)
    assert "Nothing changed" in reply.args[0]


@pytest.mark.parametrize("payment_method, confirm", [(None, "✅ Confirm"), ("Old Card", "⚠️ Save anyway")])
async def test_active_trip_is_on_pending_entry_and_card(monkeypatch, directory, payment_method, confirm):
    monkeypatch.setattr(bot_core, "_load_active_trip", lambda: BEACH)
    context, card, _ = await _show_card(monkeypatch, directory, ALICE, payment_method=payment_method)
    pending = context.user_data["pending_cards"][500]
    assert pending["trip_id"] == 7
    assert pending["trip_offer"] == {"id": 7, "name": "Beach Trip"}
    reply = card.edit_text.await_args
    assert "🧳 **Trip:** Beach Trip" in reply.args[0]
    rows = reply.kwargs["reply_markup"].inline_keyboard
    assert len(rows) == 2
    assert rows[0][0].text == confirm
    assert [b.callback_data for b in rows[0]] == ["confirm_save", "cancel_save"]
    assert [(b.text, b.callback_data) for b in rows[1]] == [("🏠 Not trip", "trip_toggle")]


@pytest.mark.parametrize("fails", [False, True])
async def test_no_trip_or_failed_lookup_has_no_toggle(monkeypatch, directory, fails):
    loader = MagicMock(return_value=None, side_effect=RuntimeError("database unavailable") if fails else None)
    monkeypatch.setattr(bot_core, "_load_active_trip", loader)
    context, card, _ = await _show_card(monkeypatch, directory, ALICE)
    pending = context.user_data["pending_cards"][500]
    assert pending["trip_id"] is None
    assert not pending.get("trip_offer")
    reply = card.edit_text.await_args
    assert "**Trip:**" not in reply.args[0]
    assert (bot_core.TRIP_UNKNOWN_WARNING in reply.args[0]) is fails
    assert len(reply.kwargs["reply_markup"].inline_keyboard) == 1
    loader.assert_called_once_with()


@pytest.mark.parametrize("closed", [False, True])
async def test_trip_toggle_round_trip_uses_only_pending_snapshot(monkeypatch, closed):
    directory_loader, trip_loader, saves = MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr(bot_core, "_load_directory", directory_loader)
    monkeypatch.setattr(bot_core, "_load_active_trip", trip_loader)
    monkeypatch.setattr(bot_core, "add_expense", saves)
    context = _context()
    pending = _pending(trip_id=7, trip_offer={"id": 7, "name": "Beach Trip"}, closed_account=closed)
    bot_core._remember_pending_card(context, 500, pending)
    for trip_id, label, line in [(None, "🧳 Tag to Beach Trip", "not a trip entry"), (7, "🏠 Not trip", "Beach Trip")]:
        update, query = _tap("trip_toggle")
        await bot_core.handle_button_click(update, context)
        query.answer.assert_awaited_once()
        query.edit_message_text.assert_awaited_once()
        assert context.user_data["pending_cards"][500] is pending
        assert pending["trip_id"] == trip_id
        assert pending["trip_offer"] == {"id": 7, "name": "Beach Trip"}
        assert bot_core._card_outcome(context, 500) is None
        reply = query.edit_message_text.await_args
        assert f"🧳 **Trip:** {line}" in reply.args[0]
        rows = reply.kwargs["reply_markup"].inline_keyboard
        assert len(rows) == 2
        assert rows[1][0].text == label
        assert rows[1][0].callback_data == "trip_toggle"
        assert rows[0][0].text == ("⚠️ Save anyway" if closed else "✅ Confirm")
    directory_loader.assert_not_called()
    trip_loader.assert_not_called()
    saves.assert_not_called()


@pytest.mark.parametrize("outcome", ["saved", "cancelled", None, "no_offer"])
async def test_trip_toggle_unavailable_card_only_toasts(outcome):
    context = _context()
    if outcome == "no_offer":
        bot_core._remember_pending_card(context, 500, _pending())
    elif outcome:
        bot_core._mark_card_resolved(context, 500, outcome)
    update, query = _tap("trip_toggle")
    await bot_core.handle_button_click(update, context)
    query.answer.assert_awaited_once()
    assert query.answer.await_args.args[0]
    if outcome in ("saved", "cancelled"):
        assert outcome in query.answer.await_args.args[0]
    query.edit_message_text.assert_not_awaited()
    query.edit_message_reply_markup.assert_not_awaited()


@pytest.mark.parametrize("toggle", [False, True])
@pytest.mark.parametrize("success, header", [(True, "Saved to Ledger"), (False, "Database Error")])
async def test_confirm_saves_selected_trip_and_keeps_trip_line(monkeypatch, directory, toggle, success, header):
    monkeypatch.setattr(bot_core, "_load_active_trip", lambda: BEACH)
    context, _, _ = await _show_card(monkeypatch, directory, ALICE)
    pending = context.user_data["pending_cards"][500]
    key = pending["idempotency_key"]
    saves = MagicMock(return_value=success)
    monkeypatch.setattr(bot_core, "add_expense", saves)
    if toggle:
        update, _ = _tap("trip_toggle")
        await bot_core.handle_button_click(update, context)
    update, query = _tap()
    await bot_core.handle_button_click(update, context)
    saves.assert_called_once()
    assert saves.call_args.kwargs["trip_id"] == (None if toggle else 7)
    reply = query.edit_message_text.await_args
    assert header in reply.args[0]
    line = "not a trip entry" if toggle else "Beach Trip"
    assert f"🧳 **Trip:** {line}" in reply.args[0]
    if success:
        assert not context.user_data["pending_cards"]
        assert bot_core._card_outcome(context, 500) == "saved"
    else:
        assert context.user_data["pending_cards"][500] is pending
        assert pending["idempotency_key"] == key
        assert bot_core._card_outcome(context, 500) is None
        rows = reply.kwargs["reply_markup"].inline_keyboard
        assert rows[1][0].text == ("🧳 Tag to Beach Trip" if toggle else "🏠 Not trip")
