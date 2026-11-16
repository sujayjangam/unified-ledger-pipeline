"""Split menus use saved snapshots and fake Telegram objects, never network or database I/O."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from telegram.error import BadRequest

import app.bot_core as bot_core
from app.services.participants import Participant
from app.services.splits import (
    UNEVEN_EVEN_SPLIT_WARNING, UNEVEN_NOT_BUILT_WARNING, open_split,
    remove_split, split_groups, split_rows, toggle_split_family, toggle_split_person,
)
from test_bot_core import _context, _tap
from test_paid_by_button import _data, _labels, _press as _payer_press
from test_splits_card import TRIP, WITH_CAROL, _card, _confirm


async def test_count_failure_warning_survives_menu_and_confirm(monkeypatch, directory):
    from app.services.splits import COUNT_EVEN_SPLIT_WARNING, COUNT_UNREAD_WARNING
    context, pending, _ = await _card(monkeypatch, directory,
        {'kind': 'named', 'include_sender': True, 'people': [{'name': 'Carol', 'count': 2}]},
        message='Hotel 600 split with Carol 2 nights', amount=600)
    assert not pending['split'] and pending['split_warnings'] == (COUNT_UNREAD_WARNING,)
    before = deepcopy(pending)
    changed = open_split(pending)
    assert pending == before
    assert changed['split_warnings'] == (COUNT_EVEN_SPLIT_WARNING,)
    assert all('count' not in row for row in changed['split'])
    removed = remove_split(changed)
    assert removed['split_warnings'] == (COUNT_UNREAD_WARNING,)
    changed = toggle_split_person(removed, 3)
    assert changed['split_warnings'] == (COUNT_EVEN_SPLIT_WARNING,)
    pending.update(changed)
    await _press(context, 'split:done')
    assert pending['split_warnings'] == (COUNT_EVEN_SPLIT_WARNING,)
    _, failed = await _confirm(monkeypatch, context, success=False)
    assert 'Shown as an even split' in failed
    _, saved = await _confirm(monkeypatch, context)
    assert "Couldn't read a count" not in saved


@pytest.mark.parametrize('default,unit,expected', [(3, 'nights', 5), (None, None, 5)])
async def test_count_menu_retains_read_counts_and_warns_for_additions(monkeypatch, directory, default, unit, expected):
    """Only newly guessed counts warn; warnings last through Done and disappear on receipt."""
    reference = {'kind': 'named', 'include_sender': True, 'default_count': default,
                 'count_unit': unit, 'people': [{'name': 'me', 'count': 4}, {'name': 'Carol', 'count': 5}]}
    context, pending, _ = await _card(monkeypatch, directory, reference,
        message='Hotel 600 split by nights, me 4 Carol 5, default 3', amount=600)
    original = deepcopy(pending)
    unticked = toggle_split_person(pending, 3)
    assert pending == original  # Nested count maps must not be mutated by a redraw attempt.
    restored = toggle_split_person(unticked, 3)
    assert restored['split'] == pending['split'] and not restored['split_warnings']
    changed = toggle_split_family(restored, 0)
    assert changed['split_menu']['counts'] == {1: 4, 3: 5, 2: expected}
    suffix = ' nights' if unit else ''
    warning = f'⚠️ Bob added at {expected}{suffix}. Check.'
    assert changed['split_warnings'] == (warning,)
    assert toggle_split_person(changed, 3)['split_warnings'] == (warning,)
    removed = remove_split(changed)
    assert removed['split_menu']['counts'] == changed['split_menu']['counts']
    assert not removed['split_warnings'] and not open_split(removed)['split']
    assert toggle_split_person(removed, 2)['split_warnings'] == (warning,)
    pending.update(changed)
    await _press(context, 'split:done')
    assert pending['split_warnings'] == (warning,)
    _, failed = await _confirm(monkeypatch, context, success=False)
    assert 'Bob added at' in failed
    save, saved = await _confirm(monkeypatch, context)
    assert 'added at' not in saved
    assert sum(cents for _, cents in save.call_args.kwargs['splits']) == 60000


def test_count_fallback_uses_mode_of_read_counts(directory):
    """The modal count beats the largest count, and guessed counts never vote."""
    people = (*directory.participants, Participant(4, 'Dave', None, None))
    pending = {'split_menu': {'amount_cents': 101, 'everyone': people, 'groups': (),
                             'families': {}, 'counts': {1: 2, 2: 2, 3: 5}},
               'split': (), 'split_warnings': ("⚠️ Couldn't match 'Zed'",)}
    before = deepcopy(pending)
    changed = toggle_split_person(pending, 4)
    assert pending == before
    assert changed['split'][0]['count'] == 2
    assert changed['split_warnings'] == ('⚠️ Dave added at 2. Check.',)


async def _press(context, data, error=None):
    """Tap through the real handler and check its answer-once contract on every path."""
    update, query = _tap(data)
    if error is not None:
        query.edit_message_text.side_effect = error
    await bot_core.handle_split_button(update, context)
    query.answer.assert_awaited_once()
    return query


def _ids(pending):
    """Read ticked ids from saved rows so tests compare choices rather than UI decoration."""
    return [row['participant_id'] for row in pending['split']]


@pytest.mark.parametrize('fields,offered', [
    ({}, True), ({'category': 'YouTrip top-up'}, False),
    ({'transaction_type': 'Transfer'}, False), ({'amount': 0}, False),
])
async def test_button_only_on_splittable_cards(monkeypatch, directory, fields, offered):
    """Invalid amounts and money movements cannot acquire a split through a button."""
    _, pending, _ = await _card(monkeypatch, directory, **fields)
    buttons = [b for row in bot_core._keyboard_for(pending).inline_keyboard for b in row]
    assert ('split_menu' in pending) is offered
    assert [b.text for b in buttons if b.callback_data == 'split:menu'] == (['👥 Add split'] if offered else [])


async def test_change_label_for_read_split(monkeypatch, directory):
    """A split read from text offers Change instead of suggesting no split exists."""
    _, pending, _ = await _card(monkeypatch, directory, WITH_CAROL)
    assert ['👥 Change split'] in _labels(bot_core._keyboard_for(pending))


async def test_first_trip_open_and_remove_reopen(monkeypatch, directory):
    """Trip defaults apply only once; Remove and Done restore the ordinary card buttons."""
    context, pending, _ = await _card(monkeypatch, directory, amount=5.55)
    query = await _press(context, 'split:menu')
    assert _ids(pending) == [1, 2, 3]
    assert pending['split_menu']['opened'] is True
    assert sum(r['share_cents'] for r in pending['split']) == 555
    assert _labels(query.edit_message_text.await_args.kwargs['reply_markup']) == [
        ["✅ Alice's family"], ['✅ Alice', '✅ Bob'], ["✅ Carol's family"], ['✅ Carol'],
        ['Remove split', 'Done'],
    ]
    query = await _press(context, 'split:none')
    assert pending['split'] == () and bot_core._split_lines(pending) == ''
    assert _data(query.edit_message_text.await_args.kwargs['reply_markup']) == _data(bot_core._keyboard_for(pending))
    await _press(context, 'split:menu')
    assert pending['split'] == ()
    query = await _press(context, 'split:done')
    assert _data(query.edit_message_text.await_args.kwargs['reply_markup']) == _data(bot_core._keyboard_for(pending))


async def test_one_person_split_follows_a_payer_change(monkeypatch, directory):
    """The one-person line names whoever pays now, on the live card and on the saved receipt."""
    context, pending, _ = await _card(monkeypatch, directory, amount=5.55)
    await _press(context, 'split:menu')
    await _press(context, 'split:p:1')
    await _press(context, 'split:p:2')
    assert _ids(pending) == [3]
    # Carol paid for herself: no split line, and nothing would be saved as a split.
    query = await _payer_press(context, 'payer:set:3')
    assert 'Split' not in query.edit_message_text.await_args.args[0]
    # The tick survived, so moving the payer to Bob brings the debt back.
    query = await _payer_press(context, 'payer:set:2')
    assert 'Carol owes Bob 5.55' in query.edit_message_text.await_args.args[0]
    save, saved = await _confirm(monkeypatch, context)
    assert 'Carol owes Bob 5.55' in saved
    assert save.call_args.kwargs['splits'] == [(3, 555)]


async def test_split_of_only_the_payer_is_not_saved(monkeypatch, directory):
    """Ticking only the person who paid saves the entry as theirs alone, with no split rows."""
    context, pending, _ = await _card(monkeypatch, directory, amount=5.55)
    await _press(context, 'split:menu')
    await _press(context, 'split:p:2')
    query = await _press(context, 'split:p:3')
    assert _ids(pending) == [1] and pending['paid_by_participant_id'] == 1
    assert 'Split' not in query.edit_message_text.await_args.args[0]
    save, _ = await _confirm(monkeypatch, context)
    assert save.call_args.kwargs['splits'] == []


async def test_no_trip_open_and_person_shares(monkeypatch, directory):
    """An everyday entry starts personal and hands leftover cents to the lowest id."""
    context, pending, _ = await _card(monkeypatch, directory, trip=False, amount=5.55)
    await _press(context, 'split:menu')
    assert pending['split'] == ()
    await _press(context, 'split:p:3')
    query = await _press(context, 'split:p:1')
    assert [(r['participant_id'], r['share_cents']) for r in pending['split']] == [(1, 278), (3, 277)]
    assert 'Alice: 2.78' in query.edit_message_text.await_args.args[0]
    assert 'Carol: 2.77' in query.edit_message_text.await_args.args[0]
    assert ['👥 Change split'] in _labels(bot_core._keyboard_for(pending))
    await _press(context, 'split:p:1')
    assert 'Carol owes Alice 5.55' in bot_core._split_lines(pending)
    await _press(context, 'split:p:3')
    assert bot_core._split_lines(pending) == ''


async def test_partial_family_ticks_all_then_unticks_all(monkeypatch, directory):
    """A partial family's toggle fills it before a second tap removes the whole family."""
    context, pending, _ = await _card(monkeypatch, directory, WITH_CAROL, amount=5.55)
    await _press(context, 'split:menu')
    await _press(context, 'split:f:0')
    assert _ids(pending) == [1, 2, 3]
    assert [r['share_cents'] for r in pending['split']] == [185, 185, 185]
    query = await _press(context, 'split:f:0')
    assert _ids(pending) == [3] and pending['split'][0]['share_cents'] == 555
    assert 'Carol owes Alice 5.55' in query.edit_message_text.await_args.args[0]


async def test_not_trip_order_and_retained_tick(monkeypatch, directory):
    """Both menus widen after Not trip; even a tick missing from everyone remains removable."""
    directory = replace(directory, participants=(Participant(5, 'Zoe', None, None),
                        *directory.participants, Participant(4, 'amy', None, None)))
    context, pending, _ = await _card(monkeypatch, directory)
    await _press(context, 'split:menu')
    update, query = _tap('trip_toggle')
    await bot_core.handle_button_click(update, context)
    query.answer.assert_awaited_once()
    assert [p.display_name for p in bot_core._payer_people(pending)] == ['Alice', 'Bob', 'amy', 'Carol', 'Zoe']
    assert _labels(bot_core._split_keyboard(pending))[:-1] == [['✅ Alice', '✅ Bob'], ['amy', '✅ Carol'], ['Zoe']]
    pending['split_menu'] = {**pending['split_menu'], 'everyone': tuple(
        p for p in pending['split_menu']['everyone'] if p.id != 3)}
    assert _data(bot_core._split_keyboard(pending))[:-1] == [
        ['split:p:1', 'split:p:2'], ['split:p:4', 'split:p:5'], ['split:p:3'],
    ]
    await _press(context, 'split:p:3')
    assert _ids(pending) == [1, 2]


@pytest.mark.parametrize('data,trip', [
    ('split:p:99', True), ('split:p:abc', True), ('split:f:99', True),
    ('split:f:-1', True), ('split:f:0', False), ('split:bogus', True), ('split:menu:bad', True),
])
async def test_stale_callbacks_only_toast(monkeypatch, directory, data, trip):
    """Callbacks must refer to what this card would currently draw, without changing state."""
    context, pending, _ = await _card(monkeypatch, directory, trip=trip)
    before = deepcopy(pending)
    query = await _press(context, data)
    assert 'no longer supported' in query.answer.await_args.args[0]
    query.edit_message_text.assert_not_awaited()
    assert pending == before


async def test_stale_people_from_everyone_rejected_on_trip(monkeypatch, directory):
    """A person offered off-trip cannot be selected by an old callback once the tag returns."""
    directory = replace(directory, participants=(*directory.participants, Participant(4, 'Dave', None, None)))
    context, pending, _ = await _card(monkeypatch, directory)
    before = deepcopy(pending)
    query = await _press(context, 'split:p:4')
    assert 'no longer supported' in query.answer.await_args.args[0]
    query = await _payer_press(context, 'payer:set:4')
    assert 'no longer supported' in query.answer.await_args.args[0]
    assert pending == before
    update, _ = _tap('trip_toggle')
    await bot_core.handle_button_click(update, context)
    assert _data(bot_core._payer_keyboard(pending)) == [
        ['payer:set:1', 'payer:set:2'], ['payer:set:3', 'payer:set:4'], ['payer:back'],
    ]


@pytest.mark.parametrize('uneven', [False, True])
async def test_reading_warnings_clear_only_when_ticks_change(monkeypatch, directory, uneven):
    """Opening an everyday menu keeps readings; a changed choice retains only uneven advice."""
    reference = {**WITH_CAROL, 'people': [{'name': 'Zed'}], 'include_sender': False, 'uneven': uneven}
    context, pending, _ = await _card(monkeypatch, directory, reference, trip=False,
                                     message='dinner 30 split between Zed')
    warnings = pending['split_warnings']
    assert warnings
    await _press(context, 'split:menu')
    assert pending['split_warnings'] == warnings
    query = await _press(context, 'split:p:1')
    assert pending['split_warnings'] == ((UNEVEN_EVEN_SPLIT_WARNING,) if uneven else ())
    assert 'No split recorded' not in query.edit_message_text.await_args.args[0]
    await _press(context, 'split:none')
    assert pending['split_warnings'] == ((UNEVEN_NOT_BUILT_WARNING,) if uneven else ())


async def test_auto_tick_changes_uneven_warning(monkeypatch, directory):
    """Trip auto-selection is a real change and must not leave a no-split warning above shares."""
    context, pending, _ = await _card(monkeypatch, directory, {**WITH_CAROL, 'uneven': True})
    await _press(context, 'split:menu')
    assert pending['split_warnings'] == (UNEVEN_EVEN_SPLIT_WARNING,)


@pytest.mark.parametrize('data', ['split:menu', 'split:p:1', 'split:none', 'split:done'])
@pytest.mark.parametrize('state', ['saved', 'cancelled', 'gone', 'no_menu'])
async def test_unavailable_card_only_toasts(data, state):
    """Finished cards are never edited, and missing state cannot accidentally create a split."""
    context = _context()
    if state == 'no_menu':
        bot_core._remember_pending_card(context, 500, {'split': ()})
    elif state != 'gone':
        bot_core._mark_card_resolved(context, 500, state)
    query = await _press(context, data)
    assert query.answer.await_args.args[0]
    query.edit_message_text.assert_not_awaited()
    query.edit_message_reply_markup.assert_not_awaited()


@pytest.mark.parametrize('data', ['split:menu', 'split:p:1', 'split:f:0', 'split:none', 'split:done'])
@pytest.mark.parametrize('error', [RuntimeError('offline'), BadRequest('invalid text')])
async def test_failed_redraw_preserves_nested_state(monkeypatch, directory, data, error):
    """A failed redraw keeps both the shares and first-open flag as they were on the card."""
    context, pending, _ = await _card(monkeypatch, directory)
    before = deepcopy(pending)
    query = await _press(context, data, error)
    assert pending == before
    assert "Couldn't change" in query.answer.await_args.args[0]


async def test_not_modified_counts_as_success(monkeypatch, directory):
    """Telegram's identical-message error still lets the successfully shown state be retained."""
    context, pending, _ = await _card(monkeypatch, directory)
    await _press(context, 'split:menu', BadRequest('Message is not modified'))
    assert pending['split_menu']['opened'] and _ids(pending) == [1, 2, 3]


async def test_confirm_takes_entry_during_redraw(monkeypatch, directory):
    """The identity guard protects an entry removed or replaced while Telegram was awaited."""
    context, pending, _ = await _card(monkeypatch, directory)
    before = deepcopy(pending)

    async def take_entry(*args, **kwargs):
        """Simulate another owner taking the pending entry during the redraw await."""
        bot_core._take_pending_card(context, 500)

    query = await _press(context, 'split:menu', take_entry)
    assert pending == before
    assert 'already being saved' in query.answer.await_args.args[0]


async def test_confirm_saves_the_shown_shares(monkeypatch, directory):
    """Menu edits flow through the normal Confirm writer with exactly the displayed cents."""
    context, pending, _ = await _card(monkeypatch, directory, amount=5.55)
    await _press(context, 'split:menu')
    await _press(context, 'split:p:2')
    await _press(context, 'split:done')
    save, saved = await _confirm(monkeypatch, context)
    assert save.call_args.kwargs['splits'] == [(1, 278), (3, 277)]
    assert bot_core._split_lines(pending) in saved


async def test_confirm_waits_for_split_redraw(monkeypatch, directory):
    """The card lock delays a concurrent Confirm until the new shares are actually shown."""
    context, _, _ = await _card(monkeypatch, directory, trip=False, amount=5.55)
    save = MagicMock(return_value=True)
    monkeypatch.setattr(bot_core, 'add_expense', save)
    started, finish = asyncio.Event(), asyncio.Event()

    async def slow_redraw(*args, **kwargs):
        """Hold the Telegram await open so Confirm gets a chance to race the change."""
        started.set()
        await finish.wait()

    changing = asyncio.create_task(_press(context, 'split:p:3', slow_redraw))
    await started.wait()
    update, query = _tap('confirm_save')
    confirming = asyncio.create_task(bot_core.handle_button_click(update, context))
    await asyncio.sleep(0)
    save.assert_not_called()
    finish.set()
    await asyncio.gather(changing, confirming)
    query.answer.assert_awaited_once()
    assert save.call_args.kwargs['splits'] == [(3, 555)]


async def test_pure_functions_leave_input_untouched(monkeypatch, directory):
    """Nested menu state stays isolated so a handler can safely discard an unsuccessful copy."""
    _, pending, _ = await _card(monkeypatch, directory, WITH_CAROL)
    before = deepcopy(pending)
    for change in (open_split, remove_split, lambda p: toggle_split_person(p, 2),
                   lambda p: toggle_split_family(p, 0)):
        assert change(pending) is not pending
        assert pending == before
    assert split_rows(pending, {1, 3}) == pending['split']
    assert split_rows(pending, ()) == ()


async def test_unlabelled_group_last_without_family_button(monkeypatch, directory):
    """Family indexes follow lowest member id, with no toggle for the trailing unlabelled group."""
    context, pending, _ = await _card(monkeypatch, directory)
    # Build a fresh snapshot with Alice unlabelled and the other two separate families.
    trip = replace(TRIP, families=((1, None), (2, 'Z family'), (3, 'A family')))
    pending['split_menu']['groups'] = split_groups(directory.participants, dict(trip.families))
    assert _data(bot_core._split_keyboard(pending)) == [
        ['split:f:0'], ['split:p:2'], ['split:f:1'], ['split:p:3'], ['split:p:1'], ['split:none', 'split:done'],
    ]
    query = await _press(context, 'split:f:2')
    assert 'no longer supported' in query.answer.await_args.args[0]


def test_split_handler_precedes_catch_all(monkeypatch):
    """Split callbacks must reach the menu handler before ordinary Confirm/Cancel routing."""
    monkeypatch.setattr(bot_core, 'TOKEN', '123:test-token-not-real')
    handlers = bot_core.get_application().handlers[0]
    match = next(h for h in handlers
                 if getattr(h, 'pattern', None) is not None and h.pattern.match('split:menu')
                 or h.callback is bot_core.handle_button_click)
    assert match.callback is bot_core.handle_split_button


@pytest.mark.parametrize('trip,expected', [(True, [1, 2, 3]), (False, [1, 2])])
async def test_wider_menus_do_not_widen_payer_readers(monkeypatch, directory, trip, expected):
    """Both readers retain their old candidates even though menu snapshots offer more people."""
    directory = replace(directory, participants=(*directory.participants, Participant(4, 'Dave', None, None)))
    reader = MagicMock(wraps=bot_core.read_payer)
    second = MagicMock(wraps=bot_core.second_reader_payer)
    monkeypatch.setattr(bot_core, 'read_payer', reader)
    monkeypatch.setattr(bot_core, 'second_reader_payer', second)
    await _card(monkeypatch, directory, trip=trip, message='dinner 30')
    assert [p.id for p in reader.call_args.args[1]] == expected
    assert [p.id for p in second.call_args.args[1]] == expected


async def test_redraw_uses_copy_and_answers_last(monkeypatch, directory):
    """The callback cannot acknowledge or mutate the pending entry before Telegram redraws."""
    context, pending, _ = await _card(monkeypatch, directory)
    before = deepcopy(pending)
    update, query = _tap('split:menu')

    async def inspect_redraw(text, **kwargs):
        """Inspect the awaited boundary where the shown copy differs from pending state."""
        assert 'Split between 3' in text
        assert pending == before
        query.answer.assert_not_awaited()

    query.edit_message_text.side_effect = inspect_redraw
    await bot_core.handle_split_button(update, context)
    query.answer.assert_awaited_once()
    assert _ids(pending) == [1, 2, 3]
