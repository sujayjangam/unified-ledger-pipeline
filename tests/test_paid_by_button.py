"""The "Change who paid" button on a pending card, and the rule a tapped payer follows.

Two layers, tested apart:
  - change_payer (app/services/participants.py) is the pure rule: given an entry, the directory
    and the tapped person, which account and payer result. No Telegram, no database.
  - handle_payer_button (app/bot_core.py) is the Telegram side: open the menu, pick, go back,
    using stubbed Telegram objects as in tests/test_button_callback.py.

The `directory` fixture (tests/conftest.py) is fictional: Alice and Bob are household, Carol is
outside it.
"""
import asyncio
from dataclasses import replace
from datetime import date
from unittest.mock import MagicMock

import pytest

import app.bot_core as bot_core
from app.services import trips
from app.services.participants import (
    CLOSED_CARD_WARNING,
    OutsideAccountNotSetUp,
    Participant,
    PayerHasNoAccount,
    change_payer,
    read_payer,
    resolve_payment,
)
from test_bot_core import ALICE, _context, _show_card, _tap

BEACH = trips.Trip(7, "Beach Trip", date(2026, 12, 1), date(2026, 12, 7), member_ids=(1, 2, 3))


def _logged(directory, message, method=None, currency="SGD"):
    """An entry as Alice's message would leave it: the payer read from the text, then the
    payment rules. The same two calls process_expense_text makes."""
    sender = directory.participant(1)
    reading = read_payer(message, directory.participants, sender, directory.account_names())
    return resolve_payment({"description": message, "payment_method": method, "currency": currency,
                            "category": "Food"}, directory, sender, reading.payer)


# --- the rule: change_payer ---------------------------------------------------------------

@pytest.mark.parametrize("message,method,currency,tapped,account,warned_before", [
    # An outside payer always moves to "Paid by someone else", whatever the message named.
    pytest.param("Lunch 40", None, "SGD", 3, 15, False, id="outside-from-default"),
    pytest.param("Lunch 40 cash", "Cash", "SGD", 3, 15, False, id="outside-from-cash"),
    pytest.param("Lunch 40 on my Visa", "Card A", "SGD", 3, 15, False, id="outside-from-owned-card"),
    pytest.param("Lunch 40 AUD", None, "AUD", 3, 15, False, id="outside-foreign-currency"),
    # A household payer keeps an unowned account...
    pytest.param("Lunch 40 cash", "Cash", "SGD", 2, 12, False, id="household-keeps-cash"),
    pytest.param("Lunch 40 AUD", None, "AUD", 2, 13, False, id="household-keeps-wallet"),
    # ...or one they own...
    pytest.param("Bob paid 40 on Alice's Visa", "Card A", "SGD", 1, 10, True, id="household-keeps-own-card"),
    # ...and otherwise moves to their default.
    pytest.param("Lunch 40", None, "SGD", 2, 11, False, id="household-default-from-default"),
    pytest.param("Lunch 40 on my Visa", "Card A", "SGD", 2, 11, False, id="household-default-from-other-card"),
    # Coming back from the outside account, the message's own account is used again.
    pytest.param("Carol paid 40 cash", "Cash", "SGD", 2, 12, False, id="back-from-outside-to-cash"),
    pytest.param("Carol paid 40 on my Visa", "Card A", "SGD", 1, 10, True, id="back-from-outside-to-own-card"),
    pytest.param("Carol paid 40", None, "SGD", 2, 11, False, id="back-from-outside-to-default"),
    pytest.param("Carol paid 40 AUD", None, "AUD", 1, 13, False, id="back-from-outside-to-wallet"),
])
def test_tapped_payer_rules(directory, message, method, currency, tapped, account, warned_before):
    before = _logged(directory, message, method, currency)
    assert bool(before["payer_warning"]) == warned_before
    snapshot = dict(before)

    after = change_payer(before, directory, directory.participant(tapped))

    assert (after["account_id"], after["paid_by_participant_id"]) == (account, tapped)
    assert after["payment_method"] == directory.account(account).name
    assert after["paid_by"] == after["account_owner"] == directory.participant(tapped).display_name
    assert after["payer_warning"] is None  # a tap is a deliberate choice
    assert before == snapshot  # a copy comes back; the entry passed in is untouched


def test_taps_do_not_pile_up(directory):
    # Alice's card -> tap Bob (his default) -> tap Alice: back on the card the message named,
    # not on Alice's default. The rule starts from the message's account, not the last tap's.
    start = _logged(directory, "refund 20 old card", "Old Card")
    assert (start["account_id"], start["closed_account"]) == (14, True)

    as_bob = change_payer(start, directory, directory.participant(2))
    assert (as_bob["account_id"], as_bob["closed_account"]) == (11, False)

    as_alice_again = change_payer(as_bob, directory, directory.participant(1))
    assert as_alice_again == start | {"payer_warning": None}


def test_someone_elses_card_in_a_foreign_currency_moves_to_the_wallet(directory):
    # Bob can't stay on Alice's card, so the entry is treated as if no card was named: a
    # foreign-currency entry goes on the wallet before anyone's default.
    start = _logged(directory, "Lunch 40 AUD on my Visa", "Card A", "AUD")
    after = change_payer(start, directory, directory.participant(2))
    assert (after["account_id"], after["paid_by_participant_id"]) == (13, 2)


def test_a_wallet_someone_else_owns_is_not_used_for_the_tapped_payer(directory):
    # If the wallet had an owner (Alice), Bob's foreign-currency entry must not go on it: an
    # owned account decides the payer. It falls through to Bob's default instead.
    accounts = tuple(replace(a, owner_participant_id=1) if a.id == 13 else a for a in directory.accounts)
    owned_wallet = replace(directory, accounts=accounts)
    after = change_payer(_logged(owned_wallet, "Lunch 40 AUD", None, "AUD"), owned_wallet, owned_wallet.participant(2))
    assert (after["account_id"], after["paid_by_participant_id"]) == (11, 2)


@pytest.mark.parametrize("default", [None, 10], ids=["no-default", "default-is-someone-elses-card"])
def test_household_payer_with_no_account_of_their_own_is_refused(directory, default):
    # Dave is household but has no usable default. Never guess a card for him.
    dave = Participant(4, "Dave", None, default, True)
    with pytest.raises(PayerHasNoAccount):
        change_payer(_logged(directory, "Lunch 40"), directory, dave)
    # An unowned account the message named is still fine: nothing needs guessing.
    after = change_payer(_logged(directory, "Lunch 40 cash", "Cash"), directory, dave)
    assert (after["account_id"], after["paid_by_participant_id"]) == (12, 4)


def test_outside_payer_needs_the_outside_account(directory):
    without = replace(directory, accounts=tuple(a for a in directory.accounts if a.id != 15))
    with pytest.raises(OutsideAccountNotSetUp):
        change_payer(_logged(without, "Lunch 40"), without, without.participant(3))


def test_resolve_payment_records_the_account_the_message_named(directory):
    assert _logged(directory, "Lunch 40")["base_account_id"] is None
    assert _logged(directory, "Lunch 40 cash", "Cash")["base_account_id"] == 12
    # The outside rule moves the entry, but what the message named is still remembered.
    carol = _logged(directory, "Carol paid 40 on my Visa", "Card A")
    assert (carol["account_id"], carol["base_account_id"]) == (15, 10)


# --- the card: the button and its menu ------------------------------------------------------

def _labels(markup):
    return [[b.text for b in row] for row in markup.inline_keyboard]


def _data(markup):
    return [[b.callback_data for b in row] for row in markup.inline_keyboard]


async def _card(monkeypatch, directory, message="Lunch 40", **extracted):
    """Shows a card for Alice's message. Returns (context, the pending entry on card 500)."""
    context, _, _ = await _show_card(monkeypatch, directory, ALICE, message=message, **extracted)
    return context, context.user_data["pending_cards"][500]


async def _press(context, data):
    update, query = _tap(data)
    await bot_core.handle_payer_button(update, context)
    query.answer.assert_awaited_once()  # every path answers exactly once
    return query


async def test_card_has_the_button_and_offers_everyone(monkeypatch, directory):
    """Everyday menus include outside people without widening the text reader."""
    _, pending = await _card(monkeypatch, directory)
    assert _data(bot_core._keyboard_for(pending))[-1] == ["payer:menu", "card:menu"]
    assert _labels(bot_core._payer_keyboard(pending)) == [["✅ Alice", "Bob"], ["Carol"], ["◂ Back"]]


async def test_trip_card_offers_the_trip_members(monkeypatch, directory):
    monkeypatch.setattr(bot_core, "_load_active_trip", lambda participant_id: BEACH)
    _, pending = await _card(monkeypatch, directory)
    assert _data(bot_core._payer_keyboard(pending)) == [["payer:set:1", "payer:set:2"], ["payer:set:3"], ["payer:back"]]


async def test_current_payer_is_offered_even_when_not_a_candidate(monkeypatch, directory):
    # A trip with only Alice on it, and the entry is on Bob's card: Bob paid, so he is listed.
    monkeypatch.setattr(bot_core, "_load_active_trip",
                        lambda participant_id: replace(BEACH, member_ids=(1,)))
    _, pending = await _card(monkeypatch, directory, message="Lunch 40 card b", payment_method="Card B")
    assert _data(bot_core._payer_keyboard(pending)) == [["payer:set:1", "payer:set:2"], ["payer:back"]]


async def test_pick_is_checked_against_the_list_the_card_shows_now(monkeypatch, directory):
    # A trip with only Alice and Bob on it. While the card is tagged to it, Carol is not offered,
    # so a "payer:set:3" left over from the everyone list is a stale button and changes nothing.
    monkeypatch.setattr(bot_core, "_load_active_trip",
                        lambda participant_id: replace(BEACH, member_ids=(1, 2)))
    context, pending = await _card(monkeypatch, directory)
    query = await _press(context, "payer:set:3")
    assert "no longer supported" in query.answer.await_args.args[0]
    query.edit_message_text.assert_not_awaited()
    assert pending["paid_by_participant_id"] == 1

    # After "Not trip" the menu offers everyone, so the same tap now picks Carol (ADR-0041).
    pending["trip_id"] = None
    query = await _press(context, "payer:set:3")
    assert pending["paid_by_participant_id"] == 3


async def test_topup_card_has_no_payer_button(monkeypatch, directory):
    _, pending = await _card(monkeypatch, directory, message="top up youtrip 100", category="YouTrip top-up")
    assert "payer_menu" not in pending
    assert "payer:menu" not in sum(_data(bot_core._keyboard_for(pending)), [])


async def test_menu_lists_people_two_to_a_row_and_marks_the_payer(monkeypatch, directory):
    monkeypatch.setattr(bot_core, "_load_active_trip", lambda participant_id: BEACH)
    context, pending = await _card(monkeypatch, directory)

    query = await _press(context, "payer:menu")

    # Only the buttons change; the card text and the entry stay as they were.
    query.edit_message_text.assert_not_awaited()
    markup = query.edit_message_reply_markup.await_args.kwargs["reply_markup"]
    assert _labels(markup) == [["✅ Alice", "Bob"], ["Carol"], ["◂ Back"]]
    assert _data(markup) == [["payer:set:1", "payer:set:2"], ["payer:set:3"], ["payer:back"]]
    assert all(len(d.encode()) <= 64 for row in _data(markup) for d in row)  # Telegram's limit
    assert pending["paid_by_participant_id"] == 1


async def test_back_restores_the_card_buttons_unchanged(monkeypatch, directory):
    context, pending = await _card(monkeypatch, directory)
    query = await _press(context, "payer:back")
    markup = query.edit_message_reply_markup.await_args.kwargs["reply_markup"]
    assert _data(markup) == [["confirm_save", "cancel_save"], ["split:menu"], ["payer:menu", "card:menu"]]
    assert pending["paid_by_participant_id"] == 1


async def test_picking_a_person_redraws_the_card_and_changes_the_entry(monkeypatch, directory):
    monkeypatch.setattr(bot_core, "_load_active_trip", lambda participant_id: BEACH)
    context, pending = await _card(monkeypatch, directory)
    # From here on, nothing may read the database or call the model.
    for name in ("_load_directory", "_load_active_trip", "extract_transactions", "add_expense"):
        monkeypatch.setattr(bot_core, name, MagicMock(side_effect=AssertionError(f"{name} was called")))

    query = await _press(context, "payer:set:3")

    assert "Paid by Carol" in query.answer.await_args.args[0]
    reply = query.edit_message_text.await_args
    assert "**Account:** Paid by someone else" in reply.args[0]
    assert "**Paid by:** Carol" in reply.args[0]
    # The card's own buttons are back, trip toggle included.
    assert _data(reply.kwargs["reply_markup"]) == [["confirm_save", "cancel_save"], ["trip_toggle"], ["split:menu"], ["payer:menu", "card:menu"]]
    # The same dict the cache holds was changed in place, and the trip tag was not touched.
    assert context.user_data["pending_cards"][500] is pending
    assert (pending["account_id"], pending["paid_by_participant_id"], pending["trip_id"]) == (15, 3, 7)


async def test_confirm_saves_the_tapped_payer_and_account(monkeypatch, directory):
    context, _ = await _card(monkeypatch, directory)
    await _press(context, "payer:set:2")
    saves = MagicMock(return_value=True)
    monkeypatch.setattr(bot_core, "add_expense", saves)

    update, query = _tap("confirm_save")
    await bot_core.handle_button_click(update, context)

    assert saves.call_args.kwargs["paid_by_participant_id"] == 2
    assert saves.call_args.kwargs["account_id"] == 11  # Bob's default, Card B
    assert saves.call_args.kwargs["entered_by_participant_id"] == 1  # Alice still logged it
    saved_card = query.edit_message_text.await_args.args[0]
    assert "**Account:** Card B" in saved_card and "**Paid by:** Bob" in saved_card


async def test_closed_card_warning_follows_the_account(monkeypatch, directory):
    # Alice's closed card: flagged. Tap Bob: his open default, so the warning and "Save anyway"
    # go. Tap Alice: back on the closed card, and both return.
    context, pending = await _card(monkeypatch, directory, message="refund 20 old card", payment_method="Old Card")
    assert pending["closed_account"]

    query = await _press(context, "payer:set:2")
    reply = query.edit_message_text.await_args
    assert CLOSED_CARD_WARNING not in reply.args[0]
    assert _labels(reply.kwargs["reply_markup"])[0][0] == "✅ Confirm"

    query = await _press(context, "payer:set:1")
    reply = query.edit_message_text.await_args
    assert CLOSED_CARD_WARNING in reply.args[0]
    assert _labels(reply.kwargs["reply_markup"])[0][0] == "⚠️ Save anyway"
    assert pending["account_id"] == 14


async def test_tapping_the_marked_person_closes_the_menu_and_clears_the_warning(monkeypatch, directory):
    # "Zed paid" matches nobody, so the card warns and shows Alice. Tapping Alice says "yes, her".
    context, pending = await _card(monkeypatch, directory, message="Zed paid 40")
    assert pending["payer_warning"]

    query = await _press(context, "payer:set:1")

    reply = query.edit_message_text.await_args
    assert "Couldn't tell who paid" not in reply.args[0]
    assert _data(reply.kwargs["reply_markup"])[-1] == ["payer:menu", "card:menu"]
    assert (pending["account_id"], pending["paid_by_participant_id"], pending["payer_warning"]) == (10, 1, None)


@pytest.mark.parametrize("problem,words", [
    ("no_default", "Dave has no default account"),
    ("no_outside_account", "Paid by someone else"),
])
async def test_a_payer_who_cannot_be_applied_changes_nothing(monkeypatch, directory, problem, words):
    if problem == "no_default":
        directory = replace(directory, participants=directory.participants + (Participant(4, "Dave", None, None, True),))
        tapped = "payer:set:4"
    else:
        monkeypatch.setattr(bot_core, "_load_active_trip", lambda participant_id: BEACH)
        tapped = "payer:set:3"
    context, pending = await _card(monkeypatch, directory)
    if problem == "no_outside_account":
        # The account vanished from this card's snapshot: the tap must refuse, not guess.
        accounts = tuple(a for a in directory.accounts if a.id != 15)
        pending["payer_menu"] = {**pending["payer_menu"], "directory": replace(directory, accounts=accounts)}
    before = dict(pending)

    query = await _press(context, tapped)

    assert words in query.answer.await_args.args[0]
    query.edit_message_text.assert_not_awaited()
    query.edit_message_reply_markup.assert_not_awaited()  # the menu stays open to pick someone else
    assert pending == before


@pytest.mark.parametrize("data", ["payer:set:99", "payer:set:", "payer:set:abc", "payer:nonsense", "payer:"])
async def test_unknown_person_or_action_only_toasts(monkeypatch, directory, data):
    context, pending = await _card(monkeypatch, directory)
    before = dict(pending)
    query = await _press(context, data)
    assert "no longer supported" in query.answer.await_args.args[0]
    query.edit_message_text.assert_not_awaited()
    query.edit_message_reply_markup.assert_not_awaited()
    assert pending == before


@pytest.mark.parametrize("state", ["saved", "cancelled", "gone", "no_menu"])
@pytest.mark.parametrize("data", ["payer:menu", "payer:set:2", "payer:back"])
async def test_a_card_that_cannot_change_gets_a_toast_only(state, data):
    # A finished card must never be edited (#29). "gone" is a restart or a save in flight;
    # "no_menu" is a top-up, or a card stashed before the button shipped.
    context = _context()
    if state == "no_menu":
        bot_core._remember_pending_card(context, 500, {"paid_by_participant_id": 1})
    elif state != "gone":
        bot_core._mark_card_resolved(context, 500, state)
    query = await _press(context, data)
    assert query.answer.await_args.args[0]
    query.edit_message_text.assert_not_awaited()
    query.edit_message_reply_markup.assert_not_awaited()


async def test_a_failed_redraw_leaves_the_entry_as_the_card_shows_it(monkeypatch, directory):
    # The card still shows Alice after a failed redraw, so Confirm must save Alice.
    context, pending = await _card(monkeypatch, directory)
    before = dict(pending)
    update, query = _tap("payer:set:2")
    query.edit_message_text.side_effect = RuntimeError("Telegram unavailable")

    await bot_core.handle_payer_button(update, context)

    assert pending == before
    query.answer.assert_awaited_once()
    assert "Couldn't change" in query.answer.await_args.args[0]


@pytest.mark.parametrize("data", ["payer:menu", "payer:back"])
async def test_a_failed_menu_redraw_answers_once(monkeypatch, directory, data):
    context, _ = await _card(monkeypatch, directory)
    update, query = _tap(data)
    query.edit_message_reply_markup.side_effect = RuntimeError("Telegram unavailable")
    await bot_core.handle_payer_button(update, context)
    query.answer.assert_awaited_once()
    assert "Couldn't update" in query.answer.await_args.args[0]


async def test_pick_leaves_an_entry_that_confirm_took_during_the_redraw(monkeypatch, directory):
    # Under the webhook, updates run concurrently: Confirm can take the entry while the pick
    # waits on its redraw. The pick must then not change it - Confirm owns it now.
    context, pending = await _card(monkeypatch, directory)
    before = dict(pending)
    update, query = _tap("payer:set:2")

    async def confirm_takes_it_meanwhile(*args, **kwargs):
        bot_core._take_pending_card(context, 500)
    query.edit_message_text.side_effect = confirm_takes_it_meanwhile

    await bot_core.handle_payer_button(update, context)

    assert pending == before
    query.answer.assert_awaited_once()
    assert "already being saved" in query.answer.await_args.args[0]


@pytest.mark.parametrize("change", ["payer:set:2", "card:set:11", "trip_toggle"])
async def test_confirm_waits_for_a_change_still_redrawing_the_card(monkeypatch, directory, change):
    # The webhook runs taps concurrently. A Confirm that arrives while a payer change (or trip
    # toggle) is still redrawing the card must wait for it, then save what the card now shows.
    monkeypatch.setattr(bot_core, "_load_active_trip", lambda participant_id: BEACH)
    context, pending = await _card(monkeypatch, directory)
    saves = MagicMock(return_value=True)
    monkeypatch.setattr(bot_core, "add_expense", saves)

    redraw_started, let_redraw_finish = asyncio.Event(), asyncio.Event()

    async def slow_redraw(*args, **kwargs):
        redraw_started.set()
        await let_redraw_finish.wait()

    change_update, change_query = _tap(change)
    change_query.edit_message_text.side_effect = slow_redraw
    handler = (bot_core.handle_payer_button if change.startswith("payer:") else
               bot_core.handle_card_button if change.startswith("card:") else bot_core.handle_button_click)
    changing = asyncio.create_task(handler(change_update, context))
    await redraw_started.wait()

    confirm_update, _ = _tap("confirm_save")
    confirming = asyncio.create_task(bot_core.handle_button_click(confirm_update, context))
    await asyncio.sleep(0)  # give Confirm every chance to run early
    await asyncio.sleep(0)
    saves.assert_not_called()  # it is waiting on the card's lock

    let_redraw_finish.set()
    await asyncio.gather(changing, confirming)

    saves.assert_called_once()
    if change == "trip_toggle":
        assert saves.call_args.kwargs["trip_id"] is None
    else:
        assert (saves.call_args.kwargs["paid_by_participant_id"], saves.call_args.kwargs["account_id"]) == (2, 11)


async def test_taps_on_different_cards_do_not_wait_for_each_other():
    context = _context()
    assert bot_core._card_lock(context, 500) is bot_core._card_lock(context, 500)
    assert bot_core._card_lock(context, 500) is not bot_core._card_lock(context, 501)


def test_payer_taps_are_routed_to_their_own_handler(monkeypatch):
    # "payer:..." must reach handle_payer_button, not fall into the catch-all card handler.
    monkeypatch.setattr(bot_core, "TOKEN", "123:test-token-not-real")
    handlers = bot_core.get_application().handlers[0]
    first_match = next(h for h in handlers
                       if getattr(h, "pattern", None) is None and h.callback is bot_core.handle_button_click
                       or getattr(h, "pattern", None) is not None and h.pattern.match("payer:menu"))
    assert first_match.callback is bot_core.handle_payer_button
