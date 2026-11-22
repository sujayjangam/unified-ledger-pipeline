"""/trip_bals: picking the trip and showing its balances, with fakes and fictional people only.

The maths is tested in tests/test_balances.py. Here: which trip is shown, what the buttons
carry, and that a tap is answered exactly once.
"""
from dataclasses import replace
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.bot_core as bot_core
from app.services import trips
from test_bot_core import ALICE, _context, _update

# Alice (1) and Bob (2) are the Tan family on this trip; Carol (3) settles alone.
BEACH = trips.Trip(7, "Beach_Trip", date(2026, 12, 1), date(2026, 12, 7),
                   member_ids=(1, 2, 3), families=((1, "Tan"), (2, "Tan"), (3, "Lee")))
CITY = trips.Trip(8, "City Trip", date(2026, 6, 1), date(2026, 6, 3))
# Dinner 90.00 paid by Alice, three ways: Carol (Lee family) owes Alice 30.00, and so does
# Bob, though he is in Alice's own family (ADR-0051: debts are per person).
ENTRIES = [{"currency": "AUD", "paid_by": 1, "splits": {1: 3000, 2: 3000, 3: 3000}}]


@pytest.fixture
def bals(monkeypatch, directory):
    """Swaps every read /trip_bals makes for a fake, and returns them to set up and inspect."""
    fakes = {"_load_bals_trip": MagicMock(return_value=None),
             "_load_bals_choices": MagicMock(return_value=([BEACH, CITY], False)),
             "get_trip_settlements": MagicMock(return_value=[]),
             "get_trip_split_entries": MagicMock(return_value=ENTRIES)}
    monkeypatch.setattr(bot_core, "_load_directory", lambda: directory)
    monkeypatch.setattr(bot_core, "get_sgt_now", lambda: MagicMock(date=lambda: date(2026, 12, 9)))
    for name, fake in fakes.items():
        monkeypatch.setattr(bot_core, name, fake)
    return fakes


def _tap(data):
    update = _update(ALICE)
    update.message = None  # Real callback updates have only effective_message.
    query = update.callback_query
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    return update, query


def _buttons(markup):
    return {button.text: button.callback_data for row in markup.inline_keyboard for button in row}


async def test_command_shows_the_active_trip_straight_away(bals):
    bals["_load_bals_trip"].return_value = BEACH
    update = _update(ALICE)
    await bot_core.trip_bals_command(update, _context())
    bals["_load_bals_trip"].assert_called_once_with(1, None)      # None = the trip switched on
    bals["get_trip_split_entries"].assert_called_once_with(7)
    bals["_load_bals_choices"].assert_not_called()
    reply = update.effective_message.reply_text.await_args
    assert reply.args[0] == "\n".join([
        "🧳 Beach_Trip · 1–7 Dec", "AUD", "", "Lee family owes AUD 30.00",
        "Tan family is owed AUD 30.00", "", "2 payments to settle"])
    assert "parse_mode" not in reply.kwargs
    assert _buttons(reply.kwargs["reply_markup"]) == {
        "All payments (2)": "bals:pay:7:AUD", "Other trip": "bals:page:0",
        "💱 All in SGD": "bals:sgd:7"}



async def test_command_with_trip_mode_off_shows_a_picker(bals):
    update = _update(ALICE)
    context = _context()
    context.args = ["typed", "words"]                              # ignored: buttons only
    await bot_core.trip_bals_command(update, context)
    bals["_load_bals_choices"].assert_called_once_with(1, 0)
    bals["get_trip_split_entries"].assert_not_called()
    reply = update.effective_message.reply_text.await_args
    assert reply.args[0] == "Which trip's balances?"
    buttons = _buttons(reply.kwargs["reply_markup"])
    assert buttons == {"Beach_Trip · 1–7 Dec": "bals:show:7", "City Trip · 1–3 Jun": "bals:show:8"}
    assert all(len(data.encode()) < 64 for data in buttons.values())


async def test_command_with_no_trips_has_no_buttons(bals):
    bals["_load_bals_choices"].return_value = ([], False)
    update = _update(ALICE)
    await bot_core.trip_bals_command(update, _context())
    reply = update.effective_message.reply_text.await_args
    assert reply.args[0] == "You're not on any trips yet." and reply.kwargs["reply_markup"] is None


async def test_a_failed_entries_read_is_an_error_never_an_empty_trip(bals):
    bals["_load_bals_trip"].return_value = BEACH
    bals["get_trip_split_entries"].return_value = None
    update = _update(ALICE)
    await bot_core.trip_bals_command(update, _context())
    text = update.effective_message.reply_text.await_args.args[0]
    assert bot_core.BALS_ERROR in text and "settled" not in text and "No split entries" not in text


async def test_failed_settlements_read_is_an_error(bals):
    """A payback read failure must not present the original bills as current debts."""
    bals['_load_bals_trip'].return_value = BEACH
    bals['get_trip_settlements'].return_value = None
    update = _update(ALICE)
    await bot_core.trip_bals_command(update, _context())
    reply = update.effective_message.reply_text.await_args
    assert bot_core.BALS_ERROR in reply.args[0]
    assert _buttons(reply.kwargs['reply_markup']) == {'Other trip': 'bals:page:0'}


async def test_a_trip_with_no_split_entries_says_so(bals):
    bals["_load_bals_trip"].return_value = BEACH
    bals["get_trip_split_entries"].return_value = []
    update = _update(ALICE)
    await bot_core.trip_bals_command(update, _context())
    assert "No split entries on this trip yet." in update.effective_message.reply_text.await_args.args[0]


async def test_command_database_failure_replies_with_the_error(bals):
    bals["_load_bals_trip"].side_effect = RuntimeError("down")
    update = _update(ALICE)
    await bot_core.trip_bals_command(update, _context())
    update.effective_message.reply_text.assert_awaited_once_with(bot_core.BALS_ERROR)


async def test_tapping_a_trip_shows_its_balances_and_answers_once(bals):
    bals["_load_bals_trip"].return_value = replace(BEACH, families=())   # nobody grouped
    update, query = _tap("bals:show:7")
    await bot_core.handle_bals_button(update, _context())
    bals["_load_bals_trip"].assert_called_once_with(1, 7)
    query.answer.assert_awaited_once_with(None)
    text = query.edit_message_text.await_args.args[0]
    # With no families, every person is their own line.
    assert "Alice is owed AUD 60.00" in text and "Bob owes AUD 30.00" in text and "Carol owes AUD 30.00" in text
    assert "2 payments to settle" in text and " pays " not in text


async def test_tapping_a_trip_you_are_not_on_toasts_and_shows_the_picker(bals):
    update, query = _tap("bals:show:99")            # _load_bals_trip returns None: not a member
    await bot_core.handle_bals_button(update, _context())
    query.answer.assert_awaited_once_with(bot_core.BALS_NOT_MEMBER)
    bals["get_trip_split_entries"].assert_not_called()
    assert query.edit_message_text.await_args.args[0] == "Which trip's balances?"


async def test_paging_the_picker(bals):
    bals["_load_bals_choices"].return_value = ([CITY], True)
    update, query = _tap("bals:page:3")
    await bot_core.handle_bals_button(update, _context())
    bals["_load_bals_choices"].assert_called_once_with(1, 3)
    buttons = _buttons(query.edit_message_text.await_args.kwargs["reply_markup"])
    assert buttons["◂ Newer"] == "bals:page:0" and buttons["Older ▸"] == "bals:page:6"
    query.answer.assert_awaited_once_with(None)


@pytest.mark.parametrize("data", ["bals:show", "bals:show:x", "bals:delete:7", "bals:show:-1", "bals:show:7:8"])
async def test_a_malformed_tap_is_answered_once_and_reads_nothing(bals, data):
    update, query = _tap(data)
    await bot_core.handle_bals_button(update, _context())
    query.answer.assert_awaited_once_with(bot_core.BALS_ERROR)
    query.edit_message_text.assert_not_awaited()
    bals["_load_bals_trip"].assert_not_called()
    bals["get_trip_split_entries"].assert_not_called()


async def test_a_failed_redraw_or_answer_is_swallowed(bals):
    bals["_load_bals_trip"].return_value = BEACH
    update, query = _tap("bals:show:7")
    query.answer.side_effect = RuntimeError("stale tap")
    query.edit_message_text.side_effect = RuntimeError("message is not modified")
    await bot_core.handle_bals_button(update, _context())          # must not raise
    query.answer.assert_awaited_once()


async def test_tap_fails_closed_when_the_directory_is_down(bals, monkeypatch):
    monkeypatch.setattr(bot_core, "_load_directory", MagicMock(side_effect=RuntimeError("down")))
    update, query = _tap("bals:show:7")
    await bot_core.handle_bals_button(update, _context())
    query.answer.assert_awaited_once_with()
    query.edit_message_text.assert_not_awaited()
    bals["_load_bals_trip"].assert_not_called()


def test_member_trip_checks_membership_in_the_query():
    conn = MagicMock()
    conn.execute.return_value.first.return_value = (8, "City Trip", date(2026, 6, 1), date(2026, 6, 3))
    assert trips.member_trip(conn, 1, 8) == CITY
    sql, params = conn.execute.call_args.args
    assert params == {"participant_id": 1, "trip_id": 8}
    assert "m.participant_id = :participant_id AND t.id = :trip_id" in " ".join(str(sql).split())
    conn.execute.return_value.first.return_value = None
    assert trips.member_trip(conn, 1, 99) is None


def test_bals_loader_reads_trip_and_families_in_one_connection(monkeypatch):
    manager = MagicMock()
    conn = manager.__enter__.return_value
    monkeypatch.setattr(bot_core, "get_connection", lambda: manager)
    active, member = MagicMock(return_value=CITY), MagicMock(return_value=CITY)
    monkeypatch.setattr(bot_core, "load_active_trip", active)
    monkeypatch.setattr(bot_core, "member_trip", member)
    monkeypatch.setattr(bot_core, "trip_families", MagicMock(return_value=((1, "Tan"), (3, "Lee"))))
    trip = bot_core._load_bals_trip(1, None)
    active.assert_called_once_with(conn, 1)
    member.assert_not_called()
    assert trip.families == ((1, "Tan"), (3, "Lee")) and trip.member_ids == (1, 3)
    bot_core._load_bals_trip(1, 8)
    member.assert_called_once_with(conn, 1, 8)
    member.return_value = None
    assert bot_core._load_bals_trip(1, 99) is None


async def test_a_report_too_long_for_one_message_is_shortened_not_lost(bals, monkeypatch):
    bals["_load_bals_trip"].return_value = BEACH
    monkeypatch.setattr(bot_core, 'TELEGRAM_MESSAGE_LIMIT', 90)
    update = _update(ALICE)
    await bot_core.trip_bals_command(update, _context())
    text = update.effective_message.reply_text.await_args.args[0]
    assert len(text) <= 90 and text.endswith(bot_core.BALS_CUT_SHORT)
    assert text.startswith("🧳 Beach_Trip")


async def test_simplified_report_notes_and_mark_paid_buttons(bals, monkeypatch, directory):
    """The report and its buttons agree on the simplified Tan-to-Alice/Bob payments."""
    from app.services.participants import Participant

    directory = replace(directory, participants=directory.participants[:2] + (
        Participant(3, 'Tom', None, None), Participant(4, 'Tia', None, None)))
    monkeypatch.setattr(bot_core, '_load_directory', lambda: directory)
    bals['_load_bals_trip'].return_value = replace(
        BEACH, member_ids=(1, 2, 3, 4), families=((3, 'Tan'), (4, 'Tan')))
    bals['get_trip_split_entries'].return_value = [
        dict(currency='AUD', paid_by=1, splits={2: 6000, 3: 22500, 4: 22500}),
        dict(currency='AUD', paid_by=2, splits={3: 16500, 4: 16500})]
    update, query = _tap('bals:pay:7:AUD')
    await bot_core.handle_bals_button(update, _context())
    reply = query.edit_message_text.await_args
    assert reply.args[0] == (
        '🧳 Beach_Trip · AUD · to settle\n'
        'Tan family pays Alice AUD 510.00\nTan family pays Bob AUD 270.00\n'
        '💡 Tan family pays Alice the AUD 60.00 Bob owes, and pays Bob AUD 60.00 less.')
    assert 'parse_mode' not in reply.kwargs
    assert _buttons(reply.kwargs['reply_markup']) == {
        '✅ Tan family → Alice · AUD 510.00': 'settle:new:7:1:51000',
        '✅ Tan family → Bob · AUD 270.00': 'settle:new:7:2:27000',
        '⬅ Back': 'bals:cur:7:AUD'}
