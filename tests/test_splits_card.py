"""Split cards and atomic saves, with fake Telegram and database objects."""

from datetime import date
from unittest.mock import MagicMock

import pytest

import app.add_expense as expenses
import app.bot_core as bot_core
from app.services.trips import Trip
from test_add_expense import _fake_connection
from test_bot_core import ALICE, _show_card, _tap

TRIP = Trip(7, "Beach Trip", date(2026, 12, 1), date(2026, 12, 7), (1, 2, 3),
            ((1, "Alice's family"), (2, "Alice's family"), (3, "Carol's family")))
WITH_CAROL = {"kind": "named", "include_sender": True, "people": [{"name": "Carol", "is_family": False}]}


@pytest.mark.parametrize('unit,heading', [('nights', 'nights'), (None, 'count'), ('night_units', r'night\_units')])
async def test_count_card_and_receipt(monkeypatch, directory, unit, heading):
    """Count shares use the same formatting on the live card and saved receipt."""
    reference = {'kind': 'named', 'count_unit': unit,
                 'people': [{'name': 'me', 'count': 3}, {'name': 'Carol', 'count': 2}]}
    context, pending, text = await _card(monkeypatch, directory, reference,
        message='Hotel 600 split by nights, me 3 and Carol 2', amount=600)
    assert f'👥 **Split by {heading} between 2:**' in text
    assert ' • Alice: 3, 360.00' in text and ' • Carol: 2, 240.00' in text
    save, saved = await _confirm(monkeypatch, context)
    assert bot_core._split_lines(pending) in saved
    assert save.call_args.kwargs['splits'] == [(1, 36000), (3, 24000)]
    only_carol = {**pending, 'split': [{**pending['split'][1], 'share_cents': 60000}]}
    assert bot_core._split_lines(only_carol) == '👥 **Split:** Carol owes Alice 600.00\n'


async def _card(monkeypatch, directory, reference=None, trip=True, **fields):
    """Show Alice's card and expose its pending snapshot and text."""
    monkeypatch.setattr(bot_core, "_load_active_trip", lambda participant_id: TRIP if trip else None)
    if reference is not None:
        # A split is kept only when the message itself has a split word (asks_for_split).
        fields.setdefault("message", "dinner 30 split with Carol")
    context, status, _ = await _show_card(monkeypatch, directory, ALICE,
                                         split_reference=reference, **fields)
    return context, context.user_data['pending_cards'][500], status.edit_text.call_args.args[0]


async def _confirm(monkeypatch, context, success=True):
    """Capture the save arguments and the final card without any I/O."""
    save = MagicMock(return_value=success)
    monkeypatch.setattr(bot_core, "add_expense", save)
    update, query = _tap("confirm_save")
    await bot_core.handle_button_click(update, context)
    return save, query.edit_message_text.call_args.args[0]


async def test_split_with_carol_and_confirm(monkeypatch, directory):
    """The cents shown before Confirm are exactly those handed to the writer."""
    context, pending, text = await _card(monkeypatch, directory, WITH_CAROL,
                                        message="dinner 5.55 split with Carol", amount=5.55)
    assert "Split between 2" in text
    assert "Alice: 2.78" in text and "Carol: 2.77" in text
    save, saved = await _confirm(monkeypatch, context)
    assert save.call_args.kwargs['splits'] == [(1, 278), (3, 277)]
    assert bot_core._split_lines(pending) in saved


@pytest.mark.parametrize("reference", [None, {"kind": "none"}])
async def test_no_split_keeps_card_text(monkeypatch, directory, reference):
    """Absent and explicit-none shapes add no card lines or saved shares."""
    context, pending, text = await _card(monkeypatch, directory, reference)
    old = {key: value for key, value in pending.items() if key not in {'split', 'split_warnings'}}
    assert text == bot_core._pending_card_text(old)
    save, _ = await _confirm(monkeypatch, context)
    assert save.call_args.kwargs['splits'] == []


def test_family_grouping_and_escaping():
    """Families follow their lowest id; unlabelled people come last without a header."""
    rows = (
        {"participant_id": 1, "name": "Alice", "family": None, "share_cents": 100},
        {"participant_id": 2, "name": "Bob", "family": "Bob's family", "share_cents": 100},
        {"participant_id": 3, "name": "Carol", "family": "Carol_family", "share_cents": 100},
        {"participant_id": 4, "name": "Dave_name", "family": "Bob's family", "share_cents": 100},
    )
    text = bot_core._split_lines({'split': tuple(reversed(rows))})
    assert text == ("👥 **Split between 4:**\nBob's family\n • Bob: 1.00\n • Dave\\_name: 1.00\n"
                    "Carol\\_family\n • Carol: 1.00\n • Alice: 1.00\n")
    assert bot_core._split_lines({'split': [{**row, 'family': None} for row in rows]}).count('family') == 0


def test_one_person_split_says_who_owes_whom():
    """One sharer who didn't pay owes the payer everything; a split of only the payer is no split."""
    carol = {"participant_id": 3, "name": "Carol_x", "family": None, "share_cents": 555}
    by_alice = {'split': [carol], 'paid_by_participant_id': 1, 'paid_by': "Alice"}
    assert bot_core._split_lines(by_alice) == "👥 **Split:** Carol\\_x owes Alice 5.55\n"
    by_carol = {'split': [carol], 'paid_by_participant_id': 3, 'paid_by': "Carol_x"}
    assert bot_core._split_lines(by_carol) == ""
    assert bot_core._owed_split(by_carol) == ()


async def test_warning_only_on_live_cards(monkeypatch, directory):
    """An unknown name keeps known shares and warns on both pending and retry cards."""
    reference = {**WITH_CAROL, 'people': [{'name': 'Carol'}, {'name': 'Erin_name'}]}
    context, _, text = await _card(monkeypatch, directory, reference)
    assert "Couldn't match 'Erin\\_name'" in text
    _, failed = await _confirm(monkeypatch, context, False)
    assert "Couldn't match 'Erin\\_name'" in failed and "Split between 2" in failed
    _, saved = await _confirm(monkeypatch, context)
    assert "Couldn't match" not in saved and "Split between 2" in saved


@pytest.mark.parametrize("reference,warning", [
    ({'kind': 'everyone'}, 'needs a trip switched on'),
])
async def test_split_refusals(monkeypatch, directory, reference, warning):
    """Refused splits warn while leaving the ordinary entry confirmable."""
    _, pending, text = await _card(monkeypatch, directory, reference, trip=False)
    assert pending['split'] == () and warning in text


async def test_unread_uneven_ticks_names_and_offers_set_amounts(monkeypatch, directory):
    """Uneven shares the bot can't read keep the names ticked, evenly, with the warning and a
    Set amounts button on the card, so fixing it is one tap (ADR-0048)."""
    _, pending, text = await _card(monkeypatch, directory, WITH_CAROL, trip=False,
                                   message="dinner 30 split unevenly with Carol")
    assert len(pending['split']) == 2
    assert 'Shown as an even split' in text
    rows = [[b.callback_data for b in row] for row in bot_core._keyboard_for(pending).inline_keyboard]
    assert ['split:menu', 'split:amounts'] in rows


@pytest.mark.parametrize("fields", [{'category': 'YouTrip top-up'}, {'transaction_type': 'Transfer'}])
async def test_transfers_never_split(monkeypatch, directory, fields):
    """Moving money never creates shares or split warnings."""
    _, pending, text = await _card(monkeypatch, directory, WITH_CAROL,
                                   message="top-up 30 split unevenly with Carol", **fields)
    assert pending['split'] == pending['split_warnings'] == ()
    # The raw message is on the card, so look for the warning line, not the word.
    assert 'Split' not in text and 'Uneven shares' not in text


@pytest.mark.parametrize("message,model_payer,payer,account,warning", [
    ('Carol covered dinner 90', 'Carol', 3, 15, True),
    ('Erin paid dinner 90', 'Carol', 3, 15, True),
    ('Bob paid dinner 90', 'Carol', 2, 12, False),
    ('Dinner 90', 'Alice', 1, 12, False),
    ('Dinner 90', 'Erin', 1, 12, False),
])
async def test_second_reader(monkeypatch, directory, message, model_payer, payer, account, warning):
    """Python wins; only a known other payer read by the model gets the check line."""
    _, pending, text = await _card(monkeypatch, directory, message=message, payer_name=model_payer,
                                  payment_method='Cash')
    assert (pending['paid_by_participant_id'], pending['account_id']) == (payer, account)
    assert ('Read as: Carol paid. Check.' in text) == warning
    if warning:
        assert "Couldn't tell who paid" not in text


async def test_second_reader_keeps_payment_conflict_warning(monkeypatch, directory):
    """An account conflict explains the final payer instead of a generic unclear line."""
    _, pending, text = await _card(monkeypatch, directory, message='Erin paid dinner 90',
                                  payer_name='Bob', payment_method='Card A')
    assert pending['paid_by_participant_id'] == 1
    assert "The message says Bob paid" in text
    assert "Couldn't tell who paid" not in text and 'Read as:' not in text


async def test_no_trip_can_split_with_outside_person(monkeypatch, directory):
    """Split candidates use the whole directory even when payer candidates are household."""
    _, pending, text = await _card(monkeypatch, directory, WITH_CAROL, trip=False)
    assert [row['participant_id'] for row in pending['split']] == [1, 3]
    assert all(row['family'] is None for row in pending['split'])
    assert 'family' not in text


@pytest.mark.parametrize("rowcount", [0, 1])
def test_save_shares_only_after_insert(monkeypatch, rowcount):
    """An idempotent retry cannot insert shares a second time."""
    conn = _fake_connection(monkeypatch, expenses)
    conn.execute.return_value.rowcount = rowcount
    assert expenses.add_expense('2026-11-16', 'Dinner', 5.55, 'Food', account_id=10,
                                splits=iter([(1, 278), (3, 277)]))
    assert conn.execute.call_count == 1 + rowcount
    conn.commit.assert_called_once()
    if rowcount:
        sql, rows = conn.execute.call_args.args
        assert 'INSERT INTO transaction_splits (transaction_id, participant_id, share_cents)' in str(sql)
        transaction_id = conn.execute.call_args_list[0].args[1]['transaction_id']
        assert rows == [{'transaction_id': transaction_id, 'participant_id': 1, 'share_cents': 278},
                        {'transaction_id': transaction_id, 'participant_id': 3, 'share_cents': 277}]


def test_split_mismatch_rolls_back(monkeypatch, capsys):
    """A bad total exits the connection with an exception before anything is committed."""
    manager = MagicMock()
    conn = manager.__enter__.return_value
    conn.execute.return_value.rowcount = 1
    monkeypatch.setattr(expenses, 'get_connection', lambda: manager)
    assert not expenses.add_expense('2026-11-16', 'Dinner', 5.55, 'Food', account_id=10, splits=[(1, 555), (3, 1)])
    conn.commit.assert_not_called()
    conn.execute.assert_called_once()
    assert manager.__exit__.call_args.args[0] is expenses.SplitTotalMismatch
    output = capsys.readouterr().out
    assert 'Split error' in output and 'Invalid input' not in output


async def test_split_taps_keep_the_original_shares(monkeypatch, directory):
    """Trip and payer changes copy the split rather than recalculating its cents."""
    context, pending, _ = await _card(monkeypatch, directory, WITH_CAROL)
    original = pending['split']
    update, _ = _tap('payer:set:2')
    await bot_core.handle_payer_button(update, context)
    assert context.user_data['pending_cards'][500]['paid_by_participant_id'] == 2
    assert context.user_data['pending_cards'][500]['split'] == original
    update, _ = _tap('trip_toggle')
    await bot_core.handle_button_click(update, context)
    changed = context.user_data['pending_cards'][500]
    assert changed['trip_id'] is None
    assert changed['split'] == original


async def test_split_without_a_split_word_is_dropped(monkeypatch, directory):
    """The model read a split, but the message never asked for one: a personal entry."""
    _, pending, text = await _card(monkeypatch, directory, WITH_CAROL, message="dinner with Carol 70")
    assert pending['split'] == () and pending['split_warnings'] == ()
    assert 'Split' not in text


@pytest.mark.parametrize('trip', [True, False])
async def test_paid_for_someone_shows_who_owes_and_saves_it(monkeypatch, directory, trip):
    """"Bob paid 50 for Carol's ticket": Carol owes Bob all of it, with no taps (#154)."""
    context, pending, text = await _card(monkeypatch, directory, message="Bob paid 50 for Carol's ticket",
                                         amount=50, payment_method='Cash', trip=trip)
    assert pending['paid_by_participant_id'] == 2
    assert '👥 **Split:** Carol owes Bob 50.00' in text
    save, _ = await _confirm(monkeypatch, context)
    assert save.call_args.kwargs['splits'] == [(3, 5000)]


async def test_paid_for_yourself_is_no_split(monkeypatch, directory):
    """"For me" with the sender paying names only the payer: nothing is owed."""
    context, _, text = await _card(monkeypatch, directory, message='I paid 50 for me', amount=50)
    assert 'Split' not in text
    save, _ = await _confirm(monkeypatch, context)
    assert save.call_args.kwargs['splits'] == []


async def test_a_transfer_for_someone_is_never_split(monkeypatch, directory):
    _, pending, _ = await _card(monkeypatch, directory, message='sent 50 to Bob for Carol', transaction_type='Transfer')
    assert pending['split'] == ()
