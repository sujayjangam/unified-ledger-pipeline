"""Account-menu rules and callbacks using fictional snapshots and stubbed Telegram calls."""
from copy import deepcopy
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

import app.bot_core as bot_core
from app.services.participants import CLOSED_CARD_WARNING, change_account, change_payer
from test_bot_core import _context, _tap
from test_paid_by_button import _card, _data, _labels, _logged


@pytest.mark.parametrize("payer,account,expected", [(1, 11, 2), (2, 12, 2), (3, 12, 1), (3, 13, 1)])
def test_change_account_rules_and_copy(directory, payer, account, expected):
    """Explicit choices replace the payment fields without mutating the original or its ticks."""
    before = _logged(directory, "Refund old card 40", "Old Card")
    before.update(entered_by_participant_id=1, paid_by_participant_id=payer, payer_warning="Check",
                  split=[{"participant_id": 2, "name": "Bob", "share_cents": 4000}])
    snapshot = deepcopy(before)
    changed = change_account(before, directory, directory.account(account))
    assert before == snapshot
    assert changed is not before
    assert (changed['account_id'], changed['base_account_id'], changed['paid_by_participant_id']) == (
        account, account, expected)
    assert changed['payment_method'] == directory.account(account).name
    assert changed['paid_by'] == changed['account_owner'] == directory.participant(expected).display_name
    assert changed['closed_account'] is False
    assert changed['payer_warning'] is None
    assert changed['split'] == before['split']


def test_payer_change_starts_from_new_card(directory):
    """Choosing Cash then changing the payer must keep Cash, not restore the original card."""
    before = _logged(directory, "Lunch 40 card a", "Card A")
    changed = change_account(before, directory, directory.account(12))
    changed = change_payer(changed, directory, directory.participant(2))
    assert (changed['account_id'], changed['paid_by_participant_id']) == (12, 2)


async def _press(context, data):
    """Dispatch a stub callback and require one final answer on every tested path."""
    update, query = _tap(data)
    await bot_core.handle_card_button(update, context)
    query.answer.assert_awaited_once()
    assert query.mock_calls[-1][0] == 'answer'
    return query


async def test_menu_and_back_use_only_snapshot(monkeypatch, directory):
    """Menus sort names, exclude closed/outside accounts and restore the original controls."""
    directory = replace(directory, accounts=tuple(reversed(directory.accounts)))
    context, pending = await _card(monkeypatch, directory)
    before = deepcopy(pending)
    loader = MagicMock(side_effect=AssertionError("Directory load on tap"))
    monkeypatch.setattr(bot_core, '_load_directory', loader)
    for data in ('card:menu', 'card:back', 'card:set:11'):
        query = await _press(context, data)
        if data == 'card:menu':
            markup = query.edit_message_reply_markup.await_args.kwargs['reply_markup']
            assert _labels(markup) == [['✓ Card A', 'Card B'], ['Cash', 'YouTrip'], ['⬅ Back']]
            assert _data(markup) == [['card:set:10', 'card:set:11'], ['card:set:12', 'card:set:13'], ['card:back']]
            assert all(len(d.encode()) < 64 for row in _data(markup) for d in row)
        elif data == 'card:back':
            assert query.edit_message_reply_markup.await_args.kwargs['reply_markup'] == bot_core._keyboard_for(pending)
        if data != 'card:set:11':
            query.edit_message_text.assert_not_awaited()
            assert pending == before
    loader.assert_not_called()
    assert pending['account_id'] == 11
    assert query.answer.await_args.args == ('💳 Card B',)
    assert query.edit_message_text.await_args.kwargs['parse_mode'] == 'Markdown'


async def test_current_account_only_closes_menu(monkeypatch, directory):
    """The marked choice must not reset the base account or existing payer warning."""
    context, pending = await _card(monkeypatch, directory)
    pending['payer_warning'] = 'Check'
    before = deepcopy(pending)
    query = await _press(context, 'card:set:10')
    assert pending == before
    assert query.edit_message_text.await_args.kwargs['reply_markup'] == bot_core._keyboard_for(pending)


@pytest.mark.parametrize('data', ['card:set:999', 'card:set:14', 'card:set:15', 'card:set:Cash',
                                'card:unknown', 'card:menu:extra'])
async def test_stale_buttons_change_nothing(monkeypatch, directory, data):
    """Only exact callbacks for accounts offered on this card may change payment."""
    context, pending = await _card(monkeypatch, directory)
    before = deepcopy(pending)
    query = await _press(context, data)
    assert query.answer.await_args.args == ('⚠️ This button is no longer supported. Please send the entry again.',)
    query.edit_message_text.assert_not_awaited()
    query.edit_message_reply_markup.assert_not_awaited()
    assert pending == before


@pytest.mark.parametrize('state', ['saved', 'cancelled', 'gone', 'no_menu'])
@pytest.mark.parametrize('data', ['card:menu', 'card:back', 'card:set:11'])
async def test_unavailable_card_toast_only(state, data):
    """Late taps preserve resolved receipts, and missing menus cannot change an entry."""
    context = _context()
    if state == 'no_menu':
        bot_core._remember_pending_card(context, 500, {'account_id': 10})
    elif state != 'gone':
        bot_core._mark_card_resolved(context, 500, state)
    query = await _press(context, data)
    query.edit_message_text.assert_not_awaited()
    query.edit_message_reply_markup.assert_not_awaited()


@pytest.mark.parametrize('data', ['card:menu', 'card:back', 'card:set:11'])
async def test_failed_redraw_keeps_entry(monkeypatch, directory, data):
    """An unsuccessful Telegram redraw leaves the payment that the visible card shows."""
    context, pending = await _card(monkeypatch, directory)
    before = deepcopy(pending)
    update, query = _tap(data)
    query.edit_message_text.side_effect = RuntimeError('Offline')
    query.edit_message_reply_markup.side_effect = RuntimeError('Offline')
    await bot_core.handle_card_button(update, context)
    assert pending == before
    query.answer.assert_awaited_once()
    assert query.mock_calls[-1][0] == 'answer'


async def test_replaced_pending_entry_is_not_updated(monkeypatch, directory):
    """Even after a successful redraw, a different pending object must not be overwritten."""
    context, pending = await _card(monkeypatch, directory)
    before = deepcopy(pending)
    update, query = _tap('card:set:11')

    async def replace_pending(*args, **kwargs):
        """Simulate ownership changing during the awaited redraw."""
        context.user_data['pending_cards'][500] = dict(before)

    query.edit_message_text.side_effect = replace_pending
    await bot_core.handle_card_button(update, context)
    assert pending == before
    assert context.user_data['pending_cards'][500] == before
    query.answer.assert_awaited_once()
    assert 'already being saved' in query.answer.await_args.args[0]


async def test_closed_warning_and_payer_own_share_follow_new_account(monkeypatch, directory):
    """Ticks persist when the new payer's sole share disappears from the rendered debt."""
    context, pending = await _card(monkeypatch, directory, message='Refund old card 40', payment_method='Old Card')
    pending['split'] = [{'participant_id': 2, 'name': 'Bob', 'share_cents': 4000}]
    ticks = deepcopy(pending['split'])
    assert CLOSED_CARD_WARNING in bot_core._pending_card_text(pending)
    query = await _press(context, 'card:set:11')
    assert CLOSED_CARD_WARNING not in query.edit_message_text.await_args.args[0]
    assert 'owes' not in query.edit_message_text.await_args.args[0]
    assert pending['split'] == ticks
    query = await _press(context, 'card:set:10')
    assert 'Bob owes Alice' in query.edit_message_text.await_args.args[0]
    assert pending['split'] == ticks


async def test_topup_has_no_card_button(monkeypatch, directory):
    """Top-ups keep their funding account and expose neither payment menu."""
    _, pending = await _card(monkeypatch, directory, message='top up youtrip 100', category='YouTrip top-up')
    assert 'card:menu' not in sum(_data(bot_core._keyboard_for(pending)), [])


def test_card_handler_precedes_catchall(monkeypatch):
    """All card callbacks must reach the dedicated handler before the general card handler."""
    monkeypatch.setattr(bot_core, 'TOKEN', '123:test-token-not-real')
    handlers = bot_core.get_application().handlers[0]
    first = next(h for h in handlers
                 if getattr(h, 'pattern', None) is not None and h.pattern.match('card:menu')
                 or h.callback is bot_core.handle_button_click)
    assert first.callback is bot_core.handle_card_button
