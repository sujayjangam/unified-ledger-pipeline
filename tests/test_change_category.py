"""Category-menu rules and callbacks using fictional snapshots and stubbed Telegram calls."""
from copy import deepcopy
from unittest.mock import MagicMock

import pytest

import app.bot_core as bot_core
from test_bot_core import _context, _tap
from test_paid_by_button import _card, _data, _labels


async def _press(context, data):
    """Dispatch a stub callback and require one final answer on every tested path."""
    update, query = _tap(data)
    await bot_core.handle_category_change(update, context)
    query.answer.assert_awaited_once()
    assert query.mock_calls[-1][0] == 'answer'
    return query


async def test_card_has_the_button_above_the_payer_row(monkeypatch, directory):
    _, pending = await _card(monkeypatch, directory, category='Other')
    data = _data(bot_core._keyboard_for(pending))
    assert data[-2:] == [['categ:menu'], ['payer:menu', 'card:menu']]


async def test_menu_back_and_pick(monkeypatch, directory):
    """The menu offers every category but the top-up, marks the current one, and a pick only
    changes the category, without loading the directory."""
    context, pending = await _card(monkeypatch, directory, message='Spent 48.7 aud for bunch', category='Other')
    before = deepcopy(pending)
    loader = MagicMock(side_effect=AssertionError("Directory load on tap"))
    monkeypatch.setattr(bot_core, '_load_directory', loader)

    query = await _press(context, 'categ:menu')
    markup = query.edit_message_reply_markup.await_args.kwargs['reply_markup']
    assert _labels(markup) == [['Food', 'Transport'], ['Accommodation', 'Entertainment'],
                               ['Utilities', '✓ Other'], ['⬅ Back']]
    assert all(len(d.encode()) < 64 for row in _data(markup) for d in row)
    assert pending == before

    query = await _press(context, 'categ:back')
    assert query.edit_message_reply_markup.await_args.kwargs['reply_markup'] == bot_core._keyboard_for(pending)
    assert pending == before

    query = await _press(context, 'categ:set:Food')
    assert '🏷️ Category: Food' in query.edit_message_text.await_args.args[0]
    assert query.edit_message_text.await_args.kwargs['parse_mode'] == 'HTML'
    assert query.answer.await_args.args == ('🏷️ Food',)
    assert pending == {**before, 'category': 'Food'}
    loader.assert_not_called()


@pytest.mark.parametrize('data', ['categ:set:YouTrip top-up', 'categ:set:food', 'categ:set:Groceries',
                                  'categ:unknown', 'categ:menu:extra'])
async def test_stale_buttons_change_nothing(monkeypatch, directory, data):
    """Only exact callbacks for categories offered on this card may change it; never a top-up."""
    context, pending = await _card(monkeypatch, directory, category='Other')
    before = deepcopy(pending)
    query = await _press(context, data)
    assert query.answer.await_args.args == ('⚠️ This button is no longer supported. Please send the entry again.',)
    query.edit_message_text.assert_not_awaited()
    query.edit_message_reply_markup.assert_not_awaited()
    assert pending == before


@pytest.mark.parametrize('state', ['saved', 'cancelled', 'gone', 'top_up'])
@pytest.mark.parametrize('data', ['categ:menu', 'categ:back', 'categ:set:Food'])
async def test_unavailable_card_toast_only(state, data):
    """Late taps preserve resolved receipts, and a top-up's category is never changed."""
    context = _context()
    if state == 'top_up':
        bot_core._remember_pending_card(context, 500, {'category': 'YouTrip top-up'})
    elif state != 'gone':
        bot_core._mark_card_resolved(context, 500, state)
    query = await _press(context, data)
    query.edit_message_text.assert_not_awaited()
    query.edit_message_reply_markup.assert_not_awaited()
    if state == 'top_up':
        assert context.user_data['pending_cards'][500] == {'category': 'YouTrip top-up'}


@pytest.mark.parametrize('data', ['categ:menu', 'categ:back', 'categ:set:Food'])
async def test_failed_redraw_keeps_entry(monkeypatch, directory, data):
    context, pending = await _card(monkeypatch, directory, category='Other')
    before = deepcopy(pending)
    update, query = _tap(data)
    query.edit_message_text.side_effect = RuntimeError('Offline')
    query.edit_message_reply_markup.side_effect = RuntimeError('Offline')
    await bot_core.handle_category_change(update, context)
    assert pending == before
    query.answer.assert_awaited_once()


async def test_replaced_pending_entry_is_not_updated(monkeypatch, directory):
    context, pending = await _card(monkeypatch, directory, category='Other')
    before = deepcopy(pending)
    update, query = _tap('categ:set:Food')

    async def replace_pending(*args, **kwargs):
        context.user_data['pending_cards'][500] = dict(before)

    query.edit_message_text.side_effect = replace_pending
    await bot_core.handle_category_change(update, context)
    assert pending == before
    assert 'already being saved' in query.answer.await_args.args[0]


async def test_topup_has_no_category_button(monkeypatch, directory):
    _, pending = await _card(monkeypatch, directory, message='top up youtrip 100', category='YouTrip top-up')
    assert 'categ:menu' not in sum(_data(bot_core._keyboard_for(pending)), [])


def test_handler_is_separate_from_category_summary(monkeypatch):
    """"categ:" taps reach this handler, and the "cat:" summary buttons still reach theirs."""
    monkeypatch.setattr(bot_core, 'TOKEN', '123:test-token-not-real')
    handlers = [h for h in bot_core.get_application().handlers[0] if getattr(h, 'pattern', None) is not None]
    first = lambda data: next(h for h in handlers if h.pattern.match(data)).callback  # noqa: E731
    assert first('categ:menu') is bot_core.handle_category_change
    assert first('cat:today') is bot_core.handle_category_button
