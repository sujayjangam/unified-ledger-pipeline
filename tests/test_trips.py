"""Trip queries, menus and card snapshots, using fakes without database or network I/O."""
from dataclasses import astuple, replace
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.bot_core as bot_core
from app.services import trips
from test_bot_core import ALICE, BOB, _context, _pending, _show_card, _tap, _update

BEACH = trips.Trip(7, "Beach Trip", date(2026, 12, 1), date(2026, 12, 7))
TODAY = date(2026, 11, 14)


@pytest.mark.parametrize("start,end,expected", [
    ("2026-03-03", "2026-03-09", "3–9 Mar"),
    ("2026-04-27", "2026-05-04", "27 Apr–4 May"),
    ("2027-04-27", "2027-05-04", "27 Apr–4 May 2027"),
    ("2025-12-26", "2026-01-08", "26 Dec 2025–8 Jan 2026"),
])
def test_format_trip_dates(start, end, expected):
    trip = replace(BEACH, start_date=date.fromisoformat(start), end_date=date.fromisoformat(end))
    assert trips.format_trip_dates(trip, TODAY) == expected


@pytest.mark.parametrize("row", [None, astuple(BEACH)])
def test_load_active_trip_is_personal(row):
    conn = MagicMock()
    conn.execute.return_value.first.return_value = row
    assert trips.load_active_trip(conn, 2) == (BEACH if row else None)
    sql, params = conn.execute.call_args.args
    assert "p.id = :participant_id" in str(sql)
    assert "t.id = p.active_trip_id" in str(sql)
    assert params == {"participant_id": 2}
    conn.commit.assert_not_called()


@pytest.mark.parametrize("count,more", [(0, False), (3, False), (4, True)])
def test_member_trips_fetches_one_extra(count, more):
    conn = MagicMock()
    conn.execute.return_value.all.return_value = [astuple(replace(BEACH, id=i)) for i in range(count)]
    choices, actual_more = trips.member_trips(conn, 2, 3, 3)
    assert [t.id for t in choices] == list(range(min(count, 3)))
    assert actual_more is more
    sql, params = conn.execute.call_args.args
    assert "ORDER BY t.start_date DESC, t.id DESC" in str(sql)
    assert "m.participant_id = :participant_id" in str(sql)
    assert params == {"participant_id": 2, "offset": 3, "limit": 4}


@pytest.mark.parametrize("row", [None, astuple(BEACH)])
@pytest.mark.parametrize("action", ["on", "off"])
def test_switches_scope_update_to_one_person_and_commit(action, row):
    conn = MagicMock()
    conn.execute.return_value.first.return_value = row
    result = trips.switch_trip_on(conn, 2, 7) if action == "on" else trips.switch_trip_off(conn, 2)
    assert result == (BEACH if row else None)
    sql, params = conn.execute.call_args.args
    assert "UPDATE participants p" in str(sql)
    assert "p.id = :participant_id" in str(sql)
    assert params["participant_id"] == 2
    if action == "on":
        assert "m.participant_id = p.id" in str(sql)
        assert params["trip_id"] == 7
    else:
        assert "SET active_trip_id = NULL" in str(sql)
    conn.execute.assert_called_once()
    conn.commit.assert_called_once()


def test_banner_id_is_personal_and_persisted():
    conn = MagicMock()
    conn.execute.return_value.scalar.return_value = 123
    assert trips.banner_message_id(conn, 2) == 123
    assert conn.execute.call_args.args[1] == {"participant_id": 2}
    for value in (456, None):
        trips.set_banner_message_id(conn, 2, value)
        assert conn.execute.call_args.args[1] == {"participant_id": 2, "message_id": value}
    assert conn.commit.call_count == 2


@pytest.mark.parametrize("offset,more", [(0, True), (3, True), (6, False)])
def test_keyboard_pages_marker_and_short_payloads(offset, more):
    choices = [replace(BEACH, id=i, name="Long_name" * 20) for i in (7, 8, 9)]
    keyboard = bot_core._trip_keyboard(choices, BEACH, offset, more, TODAY)
    rows = keyboard.inline_keyboard
    assert [r[0].callback_data for r in rows[:3]] == ["trip:on:7", "trip:on:8", "trip:on:9"]
    assert rows[0][0].text.startswith("✅ ")
    assert not rows[1][0].text.startswith("✅ ")
    assert " · 1–7 Dec" in rows[0][0].text
    buttons = {b.text: b.callback_data for row in rows for b in row}
    assert ("◂ Newer" in buttons) is (offset > 0)
    if offset:
        assert buttons["◂ Newer"] == f"trip:page:{offset - 3}"
    assert ("Older ▸" in buttons) is more
    if more:
        assert buttons["Older ▸"] == f"trip:page:{offset + 3}"
    assert buttons["🏠 Turn off trip mode"] == "trip:off"
    assert all(len(data.encode()) < 64 for data in buttons.values())
    assert bot_core._trip_keyboard([], None, 0, False, TODAY) is None


@pytest.fixture
def command_db(monkeypatch, directory):
    monkeypatch.setattr(bot_core, "_load_directory", lambda: directory)
    manager = MagicMock()
    monkeypatch.setattr(bot_core, "get_connection", lambda: manager)
    operations = {}
    defaults = {"load_active_trip": None, "member_trips": ([BEACH], False),
                "switch_trip_on": BEACH, "switch_trip_off": BEACH,
                "banner_message_id": 99, "set_banner_message_id": None}
    for name, value in defaults.items():
        operations[name] = MagicMock(return_value=value)
        monkeypatch.setattr(bot_core, name, operations[name])
    return manager.__enter__.return_value, operations


def _trip_tap(data, user_id=ALICE):
    update = _update(user_id)
    update.message = None  # Real callback updates have only effective_message.
    update.effective_chat.id = user_id
    query = update.callback_query
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    context = _context()
    context.bot.send_message = AsyncMock(return_value=MagicMock(message_id=123))
    context.bot.pin_chat_message = AsyncMock()
    context.bot.unpin_chat_message = AsyncMock()
    context.bot.delete_message = AsyncMock()
    context.bot.edit_message_text = AsyncMock()
    return update, context, query


@pytest.mark.parametrize("active,choices", [(BEACH, [BEACH]), (None, [BEACH]), (None, [])])
async def test_command_shows_personal_menu_and_ignores_arguments(command_db, active, choices):
    conn, operations = command_db
    operations["load_active_trip"].return_value = active
    operations["member_trips"].return_value = (choices, False)
    update, context = _update(BOB), _context()
    context.args = ["on", "ignored"]
    await bot_core.trip_command(update, context)
    operations["load_active_trip"].assert_called_once_with(conn, 2)
    operations["member_trips"].assert_called_once_with(conn, 2, 0, 3)
    operations["switch_trip_on"].assert_not_called()
    operations["switch_trip_off"].assert_not_called()
    reply = update.message.reply_text.await_args
    assert ("Trip mode is on" if active else "Trip mode is off") in reply.args[0]
    if not choices:
        assert "You're not on any trips yet." in reply.args[0]
        assert reply.kwargs["reply_markup"] is None


@pytest.mark.parametrize("action,active,selected,toast,changed", [
    ("trip:on:7", None, BEACH, "Trip mode is on.", True),
    ("trip:on:7", replace(BEACH, id=8), BEACH, "Trip mode is on.", True),
    ("trip:on:7", BEACH, BEACH, "Already on.", False),
    ("trip:on:7", None, None, "You're not on that trip.", False),
    ("trip:off", BEACH, BEACH, "Trip mode is off.", True),
    ("trip:off", None, None, "Trip mode was already off.", False),
    ("trip:page:3", BEACH, BEACH, None, False),
])
async def test_trip_callback_paths_answer_once(command_db, action, active, selected, toast, changed):
    conn, operations = command_db
    operations["load_active_trip"].return_value = active
    operations["switch_trip_on"].return_value = selected
    operations["switch_trip_off"].return_value = selected
    update, context, query = _trip_tap(action, BOB)
    await bot_core.handle_trip_button(update, context)
    query.answer.assert_awaited_once_with(toast)
    operations["load_active_trip"].assert_called_once_with(conn, 2)
    if action == "trip:page:3":
        operations["member_trips"].assert_called_once_with(conn, 2, 3, 3)
        query.edit_message_reply_markup.assert_awaited_once()
        query.edit_message_text.assert_not_awaited()
    elif toast == "Already on.":
        query.edit_message_text.assert_not_awaited()
        operations["switch_trip_on"].assert_not_called()
    else:
        query.edit_message_text.assert_awaited_once()
        operation = "switch_trip_on" if action.startswith("trip:on") else "switch_trip_off"
        assert operations[operation].call_args.args[:2] == (conn, 2)
    if changed:
        # The old banner is deleted outright, which also unpins it.
        context.bot.delete_message.assert_awaited_once_with(chat_id=BOB, message_id=99)
        context.bot.unpin_chat_message.assert_not_awaited()
        expected_id = None if action == "trip:off" else 123
        operations["set_banner_message_id"].assert_called_once_with(conn, 2, expected_id)
        if expected_id:
            context.bot.pin_chat_message.assert_awaited_once_with(
                chat_id=BOB, message_id=123, disable_notification=True)
        else:
            context.bot.send_message.assert_not_awaited()
    else:
        context.bot.delete_message.assert_not_awaited()
        context.bot.unpin_chat_message.assert_not_awaited()
        context.bot.send_message.assert_not_awaited()


@pytest.mark.parametrize("failure", ["load_active_trip", "member_trips", "switch_trip_on", "switch_trip_off"])
async def test_callback_database_failure_answers_once(command_db, failure):
    _, operations = command_db
    operations[failure].side_effect = RuntimeError("DB unavailable")
    update, context, query = _trip_tap("trip:off" if failure == "switch_trip_off" else "trip:on:7")
    await bot_core.handle_trip_button(update, context)
    query.answer.assert_awaited_once_with(bot_core.TRIP_CHANGE_ERROR)
    query.edit_message_text.assert_not_awaited()
    context.bot.send_message.assert_not_awaited()
    if failure in ("load_active_trip", "member_trips"):
        operations["switch_trip_on"].assert_not_called()


@pytest.mark.parametrize("failure", ["send_message", "pin_chat_message", "set_banner_message_id"])
async def test_banner_failure_does_not_undo_switch(command_db, failure):
    conn, operations = command_db
    update, context, query = _trip_tap("trip:on:7")
    target = operations[failure] if failure in operations else getattr(context.bot, failure)
    target.side_effect = RuntimeError("unavailable")
    await bot_core.handle_trip_button(update, context)
    operations["switch_trip_on"].assert_called_once_with(conn, 1, 7)
    operations["switch_trip_off"].assert_not_called()
    query.answer.assert_awaited_once_with("Trip mode is on.")
    assert bot_core.TRIP_BANNER_WARNING in query.edit_message_text.await_args.args[0]


@pytest.mark.parametrize("fails", [True, False])
async def test_callback_authorization_uses_effective_message(command_db, fails, monkeypatch):
    if fails:
        monkeypatch.setattr(bot_core, "_load_directory", MagicMock(side_effect=RuntimeError("down")))
    update, context, query = _trip_tap("trip:on:7", 999999)
    await bot_core.handle_trip_button(update, context)
    update.effective_message.reply_text.assert_awaited_once()
    query.answer.assert_awaited_once()
    context.bot.send_message.assert_not_awaited()


@pytest.mark.parametrize("user_id,participant_id", [(ALICE, 1), (BOB, 2)])
async def test_process_expense_reads_senders_trip(monkeypatch, directory, user_id, participant_id):
    loader = MagicMock(return_value=BEACH)
    monkeypatch.setattr(bot_core, "_load_active_trip", loader)
    await _show_card(monkeypatch, directory, user_id)
    loader.assert_called_once_with(participant_id)


@pytest.mark.parametrize("fails", [False, True])
async def test_register_commands_is_best_effort(fails):
    bot = MagicMock()
    bot.set_my_commands = AsyncMock(side_effect=RuntimeError("offline") if fails else None)
    await bot_core.register_commands(bot)
    bot.set_my_commands.assert_awaited_once_with(bot_core.BOT_COMMANDS)
    assert [c.command for c in bot_core.BOT_COMMANDS] == [
        "start", "recent", "today", "week", "month", "cat_today", "cat_week", "cat_month", "trip"]


@pytest.mark.parametrize("payment_method, confirm", [(None, "✅ Confirm"), ("Old Card", "⚠️ Save anyway")])
async def test_active_trip_is_on_pending_entry_and_card(monkeypatch, directory, payment_method, confirm):
    monkeypatch.setattr(bot_core, "_load_active_trip", lambda participant_id: BEACH)
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
    loader.assert_called_once_with(1)


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
    monkeypatch.setattr(bot_core, "_load_active_trip", lambda participant_id: BEACH)
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


# --- found by the Codex review ---

async def test_a_failed_toggle_redraw_leaves_the_entry_as_the_card_shows_it():
    # The card still shows the old trip line after a failed redraw, so Confirm must save that.
    context = _context()
    pending = _pending(trip_id=7, trip_offer={"id": 7, "name": "Beach Trip"})
    bot_core._remember_pending_card(context, 500, pending)
    update, query = _tap("trip_toggle")
    query.edit_message_text.side_effect = RuntimeError("Telegram unavailable")

    await bot_core.handle_button_click(update, context)

    assert pending["trip_id"] == 7
    query.answer.assert_awaited_once()
    assert "Couldn't change" in query.answer.await_args.args[0]


async def test_a_trip_name_with_markdown_characters_is_escaped_on_the_card(monkeypatch, directory):
    # An unescaped _ opens italics that never close, and Telegram rejects the whole card.
    monkeypatch.setattr(bot_core, "_load_active_trip", lambda participant_id: replace(BEACH, name="Beach_2026"))
    context, card, _ = await _show_card(monkeypatch, directory, ALICE)
    assert r"🧳 **Trip:** Beach\_2026" in card.edit_text.await_args.args[0]
    # The stored name and the button label stay as typed.
    assert context.user_data["pending_cards"][500]["trip_offer"]["name"] == "Beach_2026"


async def test_menu_and_banner_escape_names_but_not_labels(command_db):
    _, operations = command_db
    named = replace(BEACH, name="Beach_2026")
    operations["member_trips"].return_value = ([named], False)
    operations["switch_trip_on"].return_value = named
    update, context, query = _trip_tap("trip:on:7")
    await bot_core.handle_trip_button(update, context)
    reply = query.edit_message_text.await_args
    assert r"**Beach\_2026**" in reply.args[0]
    assert "Beach_2026" in reply.kwargs["reply_markup"].inline_keyboard[0][0].text
    assert r"**Beach\_2026**" in context.bot.send_message.await_args.kwargs["text"]


async def test_failed_menu_redraw_still_updates_banner_and_answers_once(command_db):
    _, operations = command_db
    update, context, query = _trip_tap("trip:on:7")
    query.edit_message_text.side_effect = RuntimeError("Telegram unavailable")
    await bot_core.handle_trip_button(update, context)
    operations["switch_trip_on"].assert_called_once()
    context.bot.pin_chat_message.assert_awaited_once()
    query.answer.assert_awaited_once_with("Trip mode is on.")


async def test_old_banner_too_old_to_delete_is_unpinned_and_marked_ended(command_db):
    # Telegram refuses to delete a bot message older than 48 hours: the banner is unpinned and
    # edited instead, and that counts as success - no warning in the menu.
    conn, operations = command_db
    operations["load_active_trip"].return_value = BEACH
    update, context, query = _trip_tap("trip:off")
    context.bot.delete_message.side_effect = RuntimeError("message can't be deleted")
    await bot_core.handle_trip_button(update, context)
    context.bot.unpin_chat_message.assert_awaited_once_with(chat_id=ALICE, message_id=99)
    edit = context.bot.edit_message_text.await_args.kwargs
    assert edit["message_id"] == 99 and "Beach Trip" in edit["text"] and "ended" in edit["text"]
    operations["set_banner_message_id"].assert_called_once_with(conn, 1, None)
    query.answer.assert_awaited_once_with("Trip mode is off.")
    assert bot_core.TRIP_BANNER_WARNING not in query.edit_message_text.await_args.args[0]


async def test_switching_trips_names_the_old_trip_on_a_retired_banner(command_db):
    _, operations = command_db
    operations["load_active_trip"].return_value = replace(BEACH, id=8, name="Old Trip")
    update, context, query = _trip_tap("trip:on:7")
    context.bot.delete_message.side_effect = RuntimeError("message can't be deleted")
    await bot_core.handle_trip_button(update, context)
    assert "Old Trip" in context.bot.edit_message_text.await_args.kwargs["text"]
    context.bot.pin_chat_message.assert_awaited_once()


async def test_old_banner_that_cannot_be_removed_at_all_still_clears_banner(command_db):
    conn, operations = command_db
    operations["load_active_trip"].return_value = BEACH
    update, context, query = _trip_tap("trip:off")
    context.bot.delete_message.side_effect = RuntimeError("message gone")
    context.bot.unpin_chat_message.side_effect = RuntimeError("message gone")
    await bot_core.handle_trip_button(update, context)
    operations["switch_trip_off"].assert_called_once_with(conn, 1)
    operations["set_banner_message_id"].assert_called_once_with(conn, 1, None)
    query.answer.assert_awaited_once_with("Trip mode is off.")
    assert bot_core.TRIP_BANNER_WARNING in query.edit_message_text.await_args.args[0]


@pytest.mark.parametrize("data", ["trip:", "trip:on:bad", "trip:page:-3", "trip:off:7"])
async def test_malformed_trip_callback_answers_once_without_writing(command_db, data):
    _, operations = command_db
    update, context, query = _trip_tap(data)
    await bot_core.handle_trip_button(update, context)
    query.answer.assert_awaited_once_with(bot_core.TRIP_CHANGE_ERROR)
    operations["switch_trip_on"].assert_not_called()
    operations["switch_trip_off"].assert_not_called()


@pytest.mark.parametrize("from_bot", [True, False])
async def test_pin_notice_deleted_only_for_the_bots_own_pin(from_bot):
    update, context = MagicMock(), MagicMock()
    context.bot.id = 42
    update.effective_message.from_user.id = 42 if from_bot else 7
    update.effective_message.delete = AsyncMock()
    await bot_core.delete_pin_notice(update, context)
    assert update.effective_message.delete.await_count == (1 if from_bot else 0)


async def test_pin_notice_delete_failure_is_swallowed():
    update, context = MagicMock(), MagicMock()
    context.bot.id = 42
    update.effective_message.from_user.id = 42
    update.effective_message.delete = AsyncMock(side_effect=RuntimeError("too old"))
    await bot_core.delete_pin_notice(update, context)


async def test_toggle_leaves_an_entry_that_confirm_took_during_the_redraw():
    # Under the webhook, updates run concurrently: Confirm can take the entry while the toggle
    # waits on its redraw. The toggle must then not change it - Confirm owns it now.
    context = _context()
    pending = _pending(trip_id=7, trip_offer={"id": 7, "name": "Beach Trip"})
    bot_core._remember_pending_card(context, 500, pending)
    update, query = _tap("trip_toggle")

    async def confirm_takes_it_meanwhile(*args, **kwargs):
        bot_core._take_pending_card(context, 500)
    query.edit_message_text.side_effect = confirm_takes_it_meanwhile

    await bot_core.handle_button_click(update, context)

    assert pending["trip_id"] == 7
    query.answer.assert_awaited_once()
    assert "already being saved" in query.answer.await_args.args[0]


async def test_banner_goes_to_the_senders_private_chat_even_from_a_group(command_db):
    # Telegram message ids are per chat, so the banner must always live in one place: the
    # sender's private chat with the bot (chat id = their Telegram user id).
    update, context, query = _trip_tap("trip:on:7")
    update.effective_chat.id = -100123
    await bot_core.handle_trip_button(update, context)
    assert context.bot.send_message.await_args.kwargs["chat_id"] == ALICE
    context.bot.delete_message.assert_awaited_once_with(chat_id=ALICE, message_id=99)
