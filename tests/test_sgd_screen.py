"""SGD reports and cards with fictional members and fully stubbed I/O."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.bot_core as bot
from app.services.trips import Trip
from test_bot_core import ALICE, _context, _update
from test_trip_bals import _buttons, _tap

DAY = date(2026, 6, 5)
TRIP = Trip(7, 'Island trip', DAY, DAY, (1, 2, 3), ((1, 'Tan'), (2, 'Tan'), (3, 'Lee')))
ROW = dict(rate=Decimal(100), rate_date=DAY, source='frankfurter', set_by_participant_id=None)


@pytest.fixture
def sgd(monkeypatch, directory):
    entries = [dict(transaction_id='a', date=DAY, currency='SGD', paid_by=3, splits={1: 1000}),
               dict(transaction_id='b', date=DAY, currency='IDR', paid_by=3, splits={1: 20000}),
               dict(transaction_id='c', date=DAY, currency='USD', paid_by=1, splits={3: 20})]
    rates = {'IDR': dict(ROW), 'USD': dict(ROW, rate=Decimal('.5'))}
    monkeypatch.setattr(bot, 'authorize', AsyncMock(return_value=directory))
    monkeypatch.setattr(bot, '_load_directory', MagicMock(side_effect=AssertionError('No directory on taps')))
    monkeypatch.setattr(bot, '_load_bals_trip', MagicMock(return_value=TRIP))
    monkeypatch.setattr(bot, 'get_trip_split_entries', MagicMock(return_value=entries))
    monkeypatch.setattr(bot, 'get_trip_settlements', MagicMock(return_value=[]))
    monkeypatch.setattr(bot, 'get_trip_rates', MagicMock(side_effect=lambda _: rates.copy()))
    monkeypatch.setattr(bot, 'get_sgt_now', lambda: SimpleNamespace(date=lambda: DAY))
    monkeypatch.setattr(bot, 'reference_rate', AsyncMock(return_value=None))
    monkeypatch.setattr(bot, 'upsert_trip_rate', MagicMock(return_value=True))
    monkeypatch.setattr(bot, 'add_settlement', MagicMock(return_value=True))
    monkeypatch.setattr(bot, '_refresh_saved_trip_sheet', AsyncMock())
    monkeypatch.setattr(bot, 'extract_transactions', AsyncMock(side_effect=AssertionError('No model')))
    return entries, rates


def context_for(directory):
    context = _context()
    context.user_data['bals_reports'] = {10: directory}
    context.bot.id = 900
    context.bot.delete_message = AsyncMock()
    context.bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=50))
    return context


def tap(data):
    update, query = _tap(data)
    query.message.message_id = 10
    query.message.chat_id = 100
    query.message.reply_text = AsyncMock(return_value=SimpleNamespace(message_id=20))
    query.edit_message_reply_markup = AsyncMock()
    return update, query


def rate_reply(context, text, currency='IDR', trip=TRIP):
    update = _update(ALICE, text)
    update.message.message_id = 30
    update.message.chat_id = 100
    row = ROW if currency == 'IDR' else dict(ROW, rate=Decimal('.5'))
    update.message.reply_to_message = SimpleNamespace(
        from_user=SimpleNamespace(id=context.bot.id), message_id=25,
        text=bot._sgd_prompt(trip, currency, row, {1: 'Alice'}))
    return update


def test_screen_rates_lines_and_buttons(sgd, directory):
    text, markup = bot._sgd_screen(TRIP, directory)
    assert text == ('🧳 Island trip · all in SGD\n1 SGD = 100 IDR (ECB, 5 Jun)\n'
                    '1 USD = 2.0000 SGD (ECB, 5 Jun)\n\nTan family pays Carol SGD 11.60')
    assert _buttons(markup) == {'✏️ IDR rate': 'sgd:rate:7:IDR', '✏️ USD rate': 'sgd:rate:7:USD',
                                '✅ Tan family → Carol · SGD 11.60': 'sgd:new:7:1:1160',
                                '⬅ Back': 'bals:cur:7:IDR'}


def test_missing_rates_have_no_confirm_and_failed_read_is_error(sgd, directory, monkeypatch):
    sgd[1].clear()
    text, markup = bot._sgd_screen(TRIP, directory)
    assert '⚠️ No IDR rate yet. Tap ✏️ IDR rate.' in text
    assert '⚠️ No USD rate yet. Tap ✏️ USD rate.' in text
    assert not any(key.startswith('✅') for key in _buttons(markup))
    assert len(_buttons(markup)) == 3
    monkeypatch.setattr(bot, 'get_trip_rates', lambda _: None)
    with pytest.raises(bot._SettlementUnavailable, match="Couldn't work out"):
        bot._sgd_screen(TRIP, directory)


def test_main_button_only_for_non_sgd_payments(sgd, directory):
    assert '💱 All in SGD' in _buttons(bot._bals_screen(TRIP, directory, DAY)[1])
    sgd[0][:] = sgd[0][:1]
    assert '💱 All in SGD' not in _buttons(bot._bals_screen(TRIP, directory, DAY)[1])
    sgd[0].clear()
    text, markup = bot._sgd_screen(TRIP, directory)
    assert 'Everyone is settled.' in text
    assert not any(key.startswith('✅') for key in _buttons(markup))


def test_truncation_only_enables_kept_lines(sgd, directory, monkeypatch):
    text, _ = bot._sgd_screen(TRIP, directory)
    monkeypatch.setattr(bot, 'TELEGRAM_MESSAGE_LIMIT', len(text) - 5)
    text, markup = bot._sgd_screen(TRIP, directory)
    assert bot.BALS_CUT_SHORT in text
    assert not any(key.startswith('✅') for key in _buttons(markup))


async def test_open_only_looks_up_missing_rates(sgd, directory):
    context = context_for(directory)
    update, query = tap('bals:sgd:7')
    await bot.handle_bals_button(update, context)
    bot.reference_rate.assert_not_awaited()
    query.answer.assert_awaited_once_with()
    del sgd[1]['IDR']
    bot.reference_rate.return_value = Decimal(101), DAY

    def store(trip_id, currency, rate, rated_on, source):
        sgd[1][currency] = dict(ROW, rate=rate, rate_date=rated_on)
        return True

    bot.upsert_trip_rate.side_effect = store
    update, query = tap('bals:sgd:7')
    await bot.handle_bals_button(update, context)
    bot.reference_rate.assert_awaited_once_with('SGD', 'IDR', DAY)
    assert '1 SGD = 101 IDR' in query.edit_message_text.await_args.args[0]
    assert 'parse_mode' not in query.edit_message_text.await_args.kwargs


async def test_open_failed_rate_read_does_not_lookup(sgd, directory):
    bot.get_trip_rates.side_effect = lambda _: None
    update, query = tap('bals:sgd:7')
    await bot.handle_bals_button(update, context_for(directory))
    query.answer.assert_awaited_once_with(bot.BALS_ERROR)
    bot.reference_rate.assert_not_awaited()


async def test_rate_prompt_uses_snapshot(sgd, directory):
    update, query = tap('sgd:rate:7:USD')
    await bot.handle_sgd_button(update, context_for(directory))
    assert 'Reply with how many SGD 1 USD cost' in query.message.reply_text.await_args.args[0]
    query.answer.assert_awaited_once_with()
    bot._load_directory.assert_not_called()


@pytest.mark.parametrize(('currency', 'value', 'expected'), [
    ('IDR', '110', Decimal(110)), ('USD', '2.5', Decimal('.4'))])
async def test_stateless_reply_upserts_canonical_rate_and_registers_report(sgd, directory, currency, value, expected):
    context = context_for(directory)
    context.user_data = {}  # No prompt or report state survives the restart.
    update = rate_reply(context, value, currency)
    await bot.handle_text(update, context)
    bot.upsert_trip_rate.assert_called_once_with(7, currency, expected, DAY, 'typed', 1)
    bot.extract_transactions.assert_not_awaited()
    assert context.user_data['bals_reports'][50] is directory
    assert context.bot.delete_message.await_args.kwargs['message_id'] == 30


async def test_bad_reply_replaces_prompt_and_deletes_reply_last(sgd, directory):
    context = context_for(directory)
    update = rate_reply(context, 'not a number')
    events = []
    update.message.reply_text.side_effect = lambda *a, **kw: events.append('prompt')
    context.bot.delete_message.side_effect = lambda **kw: events.append(kw['message_id'])
    await bot.handle_text(update, context)
    text = update.message.reply_text.await_args.args[0]
    assert text.startswith(update.message.reply_to_message.text)
    assert '\n⚠️ ' in text
    assert events == ['prompt', 25, 30]
    bot.upsert_trip_rate.assert_not_called()
    bot.extract_transactions.assert_not_awaited()


async def test_reply_wrong_member_refused_and_not_extracted(sgd, directory):
    context = context_for(directory)
    update = rate_reply(context, '110')
    bot._load_bals_trip.return_value = None
    await bot.handle_text(update, context)
    update.message.reply_text.assert_awaited_once_with(bot.BALS_NOT_MEMBER)
    bot.upsert_trip_rate.assert_not_called()
    bot.extract_transactions.assert_not_awaited()


async def open_card(directory):
    context = context_for(directory)
    update, query = tap('sgd:new:7:1:1160')
    await bot.handle_sgd_button(update, context)
    query.answer.assert_awaited_once_with()
    card = context.user_data['pending_settlements'][20]
    return context, card


async def test_confirm_saves_each_part_with_own_fx_and_reverse_giver(sgd, directory):
    context, card = await open_card(directory)
    assert card['from_id'] == 1
    update, query = tap('sgd:confirm')
    query.message.message_id = 20
    await bot.handle_sgd_button(update, context)
    calls = [call.kwargs for call in bot.add_settlement.call_args_list]
    assert [(c['from_id'], c['to_id'], c['clears_amount'], c['paid_amount']) for c in calls] == [
        (1, 3, '200.00', '2.00'), (1, 3, '10.00', '10.00'), (3, 1, '0.20', '0.40')]
    assert calls[0]['fx_rate'] == Decimal(100)
    assert 'fx_rate' not in calls[1]
    assert calls[2]['fx_rate'] == Decimal('.5')
    assert all(c['paid_currency'] == 'SGD' and c['needs_statement_check'] is False for c in calls)
    assert all(' · all in SGD' in c['note'] for c in calls)
    assert all(c['for_id'] is None for c in calls)
    assert len({c['idempotency_key'] for c in calls}) == 3
    query.edit_message_reply_markup.assert_awaited_once()
    query.answer.assert_awaited_once_with()
    assert '✅ Paid back.' in query.edit_message_text.await_args.args[0]
    assert context.user_data['bals_reports'][50] is directory
    bot._refresh_saved_trip_sheet.assert_awaited_once_with(7)
    bot._load_directory.assert_not_called()
    # Resolved cards are toast-only, even for a late Who paid tap.
    update, query = tap('sgd:who')
    query.message.message_id = 20
    await bot.handle_sgd_button(update, context)
    query.edit_message_text.assert_not_awaited()
    query.answer.assert_awaited_once_with('✅ Paid back.')


async def test_second_part_failure_preserves_keys_and_retry(sgd, directory):
    context, card = await open_card(directory)
    keys = [p['idempotency_key'] for p in card['parts']]
    bot.add_settlement.side_effect = [True, RuntimeError('offline')]
    update, query = tap('sgd:confirm')
    query.message.message_id = 20
    await bot.handle_sgd_button(update, context)
    assert context.user_data['pending_settlements'][20]['parts'] == card['parts']
    assert [p['idempotency_key'] for p in card['parts']] == keys
    assert "Couldn't save. Try Confirm again." in query.edit_message_text.await_args.args[0]
    bot._refresh_saved_trip_sheet.assert_not_awaited()
    bot.add_settlement.side_effect = [False, True, True]
    await bot.handle_sgd_button(update, context)
    assert [c.kwargs['idempotency_key'] for c in bot.add_settlement.call_args_list] == keys[:2] + keys
    bot._refresh_saved_trip_sheet.assert_awaited_once_with(7)


@pytest.mark.parametrize('data', ['sgd:new:7:1:1161', 'sgd:new:7:0:1160', 'sgd:new:7:99:1160'])
async def test_stale_card_amount_or_index(sgd, directory, data):
    update, query = tap(data)
    await bot.handle_sgd_button(update, context_for(directory))
    query.answer.assert_awaited_once_with(bot.SETTLE_STALE)
    query.message.reply_text.assert_not_awaited()


async def test_no_report_snapshot_is_stale(sgd):
    update, query = tap('sgd:new:7:1:1160')
    await bot.handle_sgd_button(update, _context())
    query.answer.assert_awaited_once_with(bot.SETTLE_STALE)
    bot._load_bals_trip.assert_not_called()


async def test_zero_net_line_has_button_and_saves_gross_parts(sgd, directory):
    sgd[0][:] = [dict(currency='SGD', paid_by=3, splits={1: 100}),
                dict(currency='USD', paid_by=1, splits={3: 50})]
    text, markup = bot._sgd_screen(TRIP, directory)
    assert 'Tan family and Carol are even' in text
    assert 'sgd:new:7:1:0' in _buttons(markup).values()
    context = context_for(directory)
    update, query = tap('sgd:new:7:1:0')
    await bot.handle_sgd_button(update, context)
    card = context.user_data['pending_settlements'][20]
    bot._save_sgd_parts(card)
    assert [c.kwargs['paid_amount'] for c in bot.add_settlement.call_args_list] == ['1.00', '1.00']


async def test_payer_redraw_copy_and_on_behalf(sgd, directory):
    # Carol pays a Tan debt owed to Bob: she is outside the owing unit.
    sgd[0][:] = [dict(currency='IDR', paid_by=2, splits={1: 20000})]
    context = context_for(directory)
    update, query = tap('sgd:new:7:1:200')
    await bot.handle_sgd_button(update, context)
    original = context.user_data['pending_settlements'][20]
    update, query = tap('sgd:person:3')
    query.message.message_id = 20
    query.edit_message_text.side_effect = RuntimeError('Telegram unavailable')
    with pytest.raises(RuntimeError):
        await bot.handle_sgd_button(update, context)
    assert context.user_data['pending_settlements'][20] is original
    assert original['from_id'] == 1
    query.edit_message_text.side_effect = None
    await bot.handle_sgd_button(update, context)
    changed = context.user_data['pending_settlements'][20]
    assert changed['from_id'] == 3 and changed['for_id'] == 1
    bot._save_sgd_parts(changed)
    saved = bot.add_settlement.call_args.kwargs
    assert saved['from_id'] == 3 and saved['to_id'] == 2 and saved['for_id'] == 1
    assert saved['note'].startswith('Carol (for Alice) · all in SGD')


async def test_partial_save_cannot_change_payer(sgd, directory):
    context, _ = await open_card(directory)
    bot.add_settlement.side_effect = [True, RuntimeError('offline')]
    update, query = tap('sgd:confirm')
    query.message.message_id = 20
    await bot.handle_sgd_button(update, context)
    update, query = tap('sgd:person:2')
    query.message.message_id = 20
    await bot.handle_sgd_button(update, context)
    query.answer.assert_awaited_once_with('Saving has started. Retry Confirm with the same payer.')
    assert context.user_data['pending_settlements'][20]['from_id'] == 1


async def test_partial_save_cannot_be_cancelled(sgd, directory):
    """Codex review of #171: Cancel after a half-saved Confirm would drop the retry keys."""
    context, card = await open_card(directory)
    bot.add_settlement.side_effect = [True, RuntimeError('offline')]
    update, query = tap('sgd:confirm')
    query.message.message_id = 20
    await bot.handle_sgd_button(update, context)
    # The redrawn card offers only Confirm.
    assert list(_buttons(query.edit_message_text.await_args.kwargs['reply_markup']).values()) == ['sgd:confirm']
    update, query = tap('sgd:cancel')
    query.message.message_id = 20
    await bot.handle_sgd_button(update, context)
    query.answer.assert_awaited_once_with('Some of this is already saved. Tap Confirm to finish it.')
    query.edit_message_text.assert_not_awaited()
    assert context.user_data['pending_settlements'][20]['parts'] == card['parts']


async def test_card_keeps_shortcut_rows_for_confirm(sgd, directory, monkeypatch):
    from app.services.balances import LinePart

    paths = (LinePart(1, 20000, ((('old', 1, 2, 20000),), (('new', 2, 3, 20000),))),)
    monkeypatch.setattr(bot, 'line_parts', lambda e, f, s, c, u, p: paths if c == 'IDR' else None)
    _, card = await open_card(directory)
    bot._save_sgd_parts(card)
    assert bot.add_settlement.call_args_list[0].kwargs['bills'] == [
        dict(transaction_id='old', for_id=1, payer_id=2, cents=20000),
        dict(transaction_id='new', for_id=2, payer_id=3, cents=20000)]


async def test_lookup_failure_leaves_missing_rate(sgd, directory):
    del sgd[1]['IDR']
    update, query = tap('bals:sgd:7')
    await bot.handle_bals_button(update, context_for(directory))
    bot.upsert_trip_rate.assert_not_called()
    assert 'No IDR rate yet' in query.edit_message_text.await_args.args[0]
    assert not any(k.startswith('✅') for k in _buttons(query.edit_message_text.await_args.kwargs['reply_markup']))


async def test_rate_reply_storage_failure_is_error_not_success(sgd, directory):
    context = context_for(directory)
    update = rate_reply(context, '110')
    bot.upsert_trip_rate.return_value = False
    await bot.handle_text(update, context)
    update.message.reply_text.assert_awaited_once_with(bot.BALS_ERROR)
    context.bot.send_message.assert_not_awaited()
    bot.extract_transactions.assert_not_awaited()
