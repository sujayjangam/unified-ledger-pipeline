"""Typed/voice settle integration and all taps, with Telegram, reads and writes stubbed."""
import asyncio
from dataclasses import replace
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.bot_core as bot
from app.services.participants import Directory
from app.services.trips import Trip
from test_bot_core import ALICE, _context, _status_msg, _update
from test_settle import PEOPLE, FAMILIES, bill
from test_settle_card import card
from test_settle_words import family_card
from test_settlements import tap

TODAY = date(2026, 11, 19)
BEACH = Trip(10, 'Beach trip', TODAY, TODAY, tuple(range(1, 7)), tuple(FAMILIES.items()))
DIRECTORY = Directory((replace(PEOPLE[0], telegram_user_id=str(ALICE)), *PEOPLE[1:]), ())


@pytest.fixture
def flow(monkeypatch):
    """Fail loudly if a tap tries to fetch anything or extract an expense."""
    for name in ('_load_directory', '_load_bals_trip', '_load_bals_choices', 'get_trip_split_entries', 'get_trip_settlements'):
        monkeypatch.setattr(bot, name, MagicMock(side_effect=AssertionError('No tap reads')))
    monkeypatch.setattr(bot, 'extract_transactions', AsyncMock(side_effect=AssertionError('No model')))
    monkeypatch.setattr(bot, 'reference_rate', AsyncMock(side_effect=AssertionError('No tap rates')))
    monkeypatch.setattr(bot, 'get_sgt_now', lambda: MagicMock(date=lambda: TODAY))
    monkeypatch.setattr(bot, 'add_settlement', MagicMock(return_value=True))
    context = _context()
    context.bot.edit_message_text = AsyncMock()
    put(context)
    return context


def put(context, value=None):
    value = card() if value is None else value
    value.update(sender_telegram_id=str(ALICE), entered_by_participant_id=1,
                 date=TODAY, idempotency_key='fictional-key', directory=DIRECTORY)
    context.user_data['pending_settle_ups'] = {20: value}
    return value


async def click(context, data):
    update, query = tap(data)
    await bot.handle_settle_up_button(update, context)
    query.answer.assert_awaited_once()
    return query


def pending(context):
    return context.user_data['pending_settle_ups'][20]


def snapshot():
    value = card()
    return {k: value[k] for k in ('bills', 'trip_id', 'scope_labels', 'names', 'people')}, PEOPLE, FAMILIES


async def test_family_toggle_and_new_giver_use_only_snapshot(flow):
    """Every family redraw answers once and replays captured membership, without reads."""
    put(flow, family_card())
    await click(flow, 'su:family')
    assert not pending(flow)['result'].family
    assert tuple(r.for_id for r in pending(flow)['result'].rows) == (3,)
    await click(flow, 'su:family')
    assert tuple(r.for_id for r in pending(flow)['result'].rows) == (3, 4)
    await click(flow, 'su:giver')
    index = pending(flow)['giver_options'].index(5)
    await click(flow, f'su:person:{index}')
    assert tuple(r.for_id for r in pending(flow)['result'].rows) == (5, 6)
    bot._load_directory.assert_not_called()
    bot._load_bals_trip.assert_not_called()


async def test_family_failed_redraw_does_not_mutate(flow):
    """A failed Telegram edit leaves both family mode and allocations untouched."""
    original = put(flow, family_card())
    update, query = tap('su:family')
    query.edit_message_text.side_effect = RuntimeError('offline')
    await bot.handle_settle_up_button(update, flow)
    assert pending(flow) is original and pending(flow)['result'].family
    query.answer.assert_awaited_once()


@pytest.mark.parametrize('family', [False, True])
@pytest.mark.parametrize('already_prefixed', [False, True])
async def test_settle_menu_reply_survives_restart(flow, monkeypatch, family, already_prefixed):
    """Exact bot prompt lines work with empty state; only the prompt is deleted."""
    monkeypatch.setattr(bot, 'authorize', AsyncMock(return_value=DIRECTORY))
    value = family_card()
    snap = {k: value[k] for k in ('bills', 'trip_id', 'scope_labels', 'names', 'people', 'scope_families')}
    monkeypatch.setattr(bot, '_load_settle_snapshot', lambda *_: (snap, PEOPLE, FAMILIES))
    flow.user_data.clear()
    prefix = 'Family settle ' if family else 'Settle '
    update = _update(ALICE, (prefix if already_prefixed else '') + 'Carol paid 500 for white rock')
    prompt = update.message.reply_to_message
    prompt.text = (bot.FAMILY_SETTLE_PROMPT if family else bot.SETTLE_PROMPT) + '\ne.g. Carol paid 381 for dinner'
    prompt.from_user.id = flow.bot.id
    prompt.delete = AsyncMock()
    update.message.delete = AsyncMock()
    update.message.reply_text.return_value = _status_msg()
    await bot.handle_text(update, flow)
    value = flow.user_data['pending_settle_ups'][500]
    assert value['reading'].family is family
    assert tuple(r.for_id for r in value['result'].rows) == ((3, 4) if family else (3,))
    prompt.delete.assert_awaited_once()
    update.message.delete.assert_not_awaited()
    bot.extract_transactions.assert_not_awaited()


@pytest.mark.parametrize('family, said', [(True, 'Settle '), (False, 'Family settle ')])
async def test_the_prompt_decides_the_kind_over_the_replys_own_word(flow, monkeypatch, family, said):
    """A reply that starts with the other kind's word is read as the prompt's kind."""
    monkeypatch.setattr(bot, 'authorize', AsyncMock(return_value=DIRECTORY))
    value = family_card()
    snap = {k: value[k] for k in ('bills', 'trip_id', 'scope_labels', 'names', 'people', 'scope_families')}
    monkeypatch.setattr(bot, '_load_settle_snapshot', lambda *_: (snap, PEOPLE, FAMILIES))
    flow.user_data.clear()
    update = _update(ALICE, said + 'Carol paid 500 for white rock')
    prompt = update.message.reply_to_message
    prompt.text = (bot.FAMILY_SETTLE_PROMPT if family else bot.SETTLE_PROMPT) + '\ne.g. Carol paid 381 for dinner'
    prompt.from_user.id = flow.bot.id
    prompt.delete = AsyncMock()
    update.message.reply_text.return_value = _status_msg()
    await bot.handle_text(update, flow)
    assert flow.user_data['pending_settle_ups'][500]['reading'].family is family


@pytest.mark.parametrize('first_line,own_bot', [('Other prompt', True), (bot.SETTLE_PROMPT, False)])
async def test_other_prompt_is_not_a_settle(flow, monkeypatch, first_line, own_bot):
    """Neither another bot's prompt nor unrelated first lines imply a settle."""
    monkeypatch.setattr(bot, 'authorize', AsyncMock(return_value=DIRECTORY))
    process = AsyncMock()
    monkeypatch.setattr(bot, 'process_expense_text', process)
    update = _update(ALICE, 'Carol paid 381 for dinner')
    update.message.reply_to_message.text = first_line
    update.message.reply_to_message.from_user.id = flow.bot.id if own_bot else 999
    await bot.handle_text(update, flow)
    assert process.await_args.args[2] == update.message.text


async def test_family_typed_message_never_extracts(flow, monkeypatch):
    """The first process-expense check catches family settles, including refusals."""
    monkeypatch.setattr(bot, '_load_settle_snapshot', lambda *_: snapshot())
    status = _status_msg()
    await bot.process_expense_text(_update(ALICE), flow, 'Family settle Carol paid 381', status, DIRECTORY)
    assert flow.user_data['pending_settle_ups'][500]['reading'].family
    await bot.process_expense_text(_update(ALICE), flow, 'Family settle nonsense', status, DIRECTORY)
    assert status.edit_text.await_args.args[0].startswith('Settle needs')
    bot.extract_transactions.assert_not_awaited()


async def test_menu_reply_refusal_and_failed_prompt_delete(flow, monkeypatch, capsys):
    """A refusal stays a settle, and a failed prompt deletion only logs the failure."""
    monkeypatch.setattr(bot, 'authorize', AsyncMock(return_value=DIRECTORY))
    monkeypatch.setattr(bot, '_load_settle_snapshot', lambda *_: snapshot())
    update = _update(ALICE, 'nonsense')
    prompt = update.message.reply_to_message
    prompt.text = bot.FAMILY_SETTLE_PROMPT
    prompt.from_user.id = flow.bot.id
    prompt.delete = AsyncMock(side_effect=RuntimeError('cannot delete'))
    update.message.delete = AsyncMock()
    status = _status_msg()
    update.message.reply_text.return_value = status
    await bot.handle_text(update, flow)
    assert status.edit_text.await_args.args[0].startswith('Settle needs')
    assert 'Could not delete settle menu prompt' in capsys.readouterr().out
    update.message.delete.assert_not_awaited()
    bot.extract_transactions.assert_not_awaited()


def test_snapshot_captures_each_trips_families(monkeypatch):
    """Inactive trip families are copied by scope and restricted to actual members."""
    other = replace(BEACH, id=11, member_ids=(1, 3, 5), families=((3, 'Ng'), (5, 'Ng'), (6, 'Ng')))
    monkeypatch.setattr(bot, '_load_bals_trip', lambda _, tid: {10: BEACH, 11: other}.get(tid))
    monkeypatch.setattr(bot, '_load_bals_choices', lambda *_: ([BEACH, other], False))
    monkeypatch.setattr(bot, '_load_settle_bills', lambda *_: ())
    snap, _, _ = bot._load_settle_snapshot(PEOPLE[0], DIRECTORY)
    assert snap['scope_families'] == {10: FAMILIES, 11: {3: 'Ng', 5: 'Ng'}}


@pytest.mark.parametrize('voice', [False, True])
async def test_text_and_transcribed_voice_never_extract(flow, monkeypatch, voice, tmp_path):
    monkeypatch.setattr(bot, 'authorize', AsyncMock(return_value=DIRECTORY))
    monkeypatch.setattr(bot, '_load_settle_snapshot', lambda *_: snapshot())
    update = _update(ALICE, 'Settle Carol paid 381 for saree pleating and for Erin airbnb')
    update.message.reply_to_message = None
    status = _status_msg()
    update.message.reply_text.return_value = status
    if voice:
        monkeypatch.setattr(bot, 'transcribe_audio', AsyncMock(return_value='Settle, Carol paid 381 for saree pleating and for Erin airbnb'))
        file = MagicMock(download_to_drive=AsyncMock())
        flow.bot.get_file = AsyncMock(return_value=file)
        # The transcript is stubbed and the temporary audio stays inside this repo.
        monkeypatch.setattr(bot.tempfile, 'tempdir', str(tmp_path))
        await bot.handle_voice(update, flow)
    else:
        await bot.handle_text(update, flow)
    bot.extract_transactions.assert_not_awaited()
    assert flow.user_data['pending_settle_ups'][500]['result'].can_confirm
    assert 'parse_mode' not in status.edit_text.await_args.kwargs
    # The words as read are kept on the card, to be saved as the payback's note.
    said = ('Settle, Carol' if voice else 'Settle Carol') + ' paid 381 for saree pleating and for Erin airbnb'
    assert flow.user_data['pending_settle_ups'][500]['raw_text'] == said


@pytest.mark.parametrize('failure', [RuntimeError('offline'), ValueError('bad snapshot')])
async def test_failed_read_and_refusal(flow, monkeypatch, failure, capsys):
    monkeypatch.setattr(bot, '_load_settle_snapshot', MagicMock(side_effect=failure))
    status = _status_msg()
    await bot.process_expense_text(_update(ALICE), flow, 'Settle Carol paid 120', status, DIRECTORY)
    status.edit_text.assert_awaited_once_with(bot.SETTLE_UP_READ_ERROR)
    assert capsys.readouterr().out.count('settle card ') == 1
    monkeypatch.setattr(bot, '_load_settle_snapshot', lambda *_: snapshot())
    await bot.process_expense_text(_update(ALICE), flow, 'Settle nonsense', status, DIRECTORY)
    assert status.edit_text.await_args.args[0].startswith('Settle needs who paid')
    bot.extract_transactions.assert_not_awaited()


async def test_confirm_saving_before_write_and_resolved_toast(flow):
    pending(flow)['raw_text'] = 'Settle Carol paid 381 for saree pleating'
    update, query = tap('su:confirm')
    def save(**kwargs):
        assert 'su:saving' in {b.callback_data for row in query.edit_message_text.await_args.kwargs['reply_markup'].inline_keyboard for b in row}
        assert kwargs['bills'] == [dict(transaction_id='saree', for_id=3, cents=12000),
                                   dict(transaction_id='airbnb', for_id=5, cents=26100)]
        assert kwargs['clears_amount'] == kwargs['paid_amount'] == '381.00'
        assert kwargs['from_id'] == 3 and kwargs['to_id'] == 1 and kwargs['for_id'] is None
        assert kwargs['trip_id'] == 10 and kwargs['date'] == TODAY
        assert not kwargs['needs_statement_check'] and kwargs['fx_rate'] is None
        # The typed message is saved as the payback's note (raw words, like ADR-0008).
        assert kwargs['note'] == 'Settle Carol paid 381 for saree pleating'
        # Answered before the blocking save, so the button stops spinning.
        query.answer.assert_awaited_once_with()
    bot.add_settlement.side_effect = save
    await bot.handle_settle_up_button(update, flow)
    query.answer.assert_awaited_once_with()
    assert query.edit_message_text.await_args.args[0].endswith('✅ Payback saved.')
    assert not flow.user_data['pending_settle_ups']
    query = await click(flow, 'su:confirm')
    query.answer.assert_awaited_once_with('✅ Payback already saved.')
    query.edit_message_text.assert_not_awaited()
    bot.add_settlement.assert_called_once()


async def test_cancel_saving_and_unknown_actions(flow):
    original = pending(flow)
    query = await click(flow, 'su:saving')
    query.answer.assert_awaited_once_with('⏳ Saving...')
    query.edit_message_text.assert_not_awaited()
    for data in ('su:share:-1', 'su:person:999', 'su:bogus'):
        query = await click(flow, data)
        query.answer.assert_awaited_once_with()
        query.edit_message_text.assert_not_awaited()
    assert pending(flow) is original
    query = await click(flow, 'su:cancel')
    assert query.edit_message_text.await_args.args[0].endswith('❌ Cancelled.')
    query = await click(flow, 'su:bills')
    query.answer.assert_awaited_once_with('❌ Cancelled.')
    query.edit_message_text.assert_not_awaited()
    bot.add_settlement.assert_not_called()


async def test_failed_save_preserves_key_and_retry(flow):
    original = pending(flow)
    bot.add_settlement.side_effect = RuntimeError('offline')
    query = await click(flow, 'su:confirm')
    assert pending(flow) is original
    assert query.edit_message_text.await_args.args[0].endswith("Couldn't save. Try Confirm again.")
    bot.add_settlement.side_effect = None
    bot.add_settlement.return_value = False  # Idempotent retry is a success.
    await click(flow, 'su:confirm')
    assert [c.kwargs['idempotency_key'] for c in bot.add_settlement.call_args_list] == ['fictional-key'] * 2
    assert bot._card_outcome(flow, 20) == 'saved'


async def test_bills_toggle_back_and_copy_on_failed_redraw(flow):
    original = pending(flow)
    update, query = tap('su:bills')
    query.edit_message_text.side_effect = RuntimeError('offline edit')
    await bot.handle_settle_up_button(update, flow)
    assert pending(flow) is original and pending(flow)['view'] == 'card'
    query.answer.assert_awaited_once_with()
    await click(flow, 'su:bills')
    assert pending(flow)['view'] == 'bills'
    await click(flow, 'su:share:0')
    assert pending(flow)['choices'].shares == (('airbnb', 5),)
    # Ticked items display first, but indexes stay stable across checkbox redraws.
    await click(flow, 'su:share:0')
    assert set(pending(flow)['choices'].shares) == {('saree', 3), ('airbnb', 5)}
    await click(flow, 'su:back')
    assert pending(flow)['view'] == 'card'


async def test_giver_receiver_and_questions(flow):
    put(flow, card('Settle Carol paid 120 for saree', [bill(shares={3: 12000, 5: 12000})]))
    await click(flow, 'su:giver')
    index = pending(flow)['giver_options'].index(5)
    await click(flow, f'su:person:{index}')
    assert pending(flow)['result'].giver_id == 5
    assert pending(flow)['result'].rows[0].for_id == 5
    put(flow, card('Settle Carol paid Alice 120', [bill(), bill('b', payer=2)]))
    await click(flow, 'su:receivers')
    index = [p for p, _ in pending(flow)['receiver_options']].index(2)
    await click(flow, f'su:receiver:{index}')
    assert pending(flow)['result'].receiver_id == 2
    for value in (card('Settle Carol paid 120', [bill(), bill('b', payer=2)]),
                  card('Settle Carol paid Bob 120', [bill()]),
                  card('Settle Carol paid Bob 120 for saree', [bill()])):
        put(flow, value)
        assert not pending(flow)['result'].can_confirm
        await click(flow, 'su:confirm')
        bot.add_settlement.assert_not_called()
        await click(flow, 'su:receiver:0')
        assert pending(flow)['result'].can_confirm


async def test_close_pick_oldest_and_everyday(flow):
    value = card('Settle Carol paid 120 for saree booking', [bill('a'), bill('b')])
    put(flow, value)
    await click(flow, 'su:pick:1')
    assert pending(flow)['result'].rows[0].transaction_id == 'b'
    await click(flow, 'su:oldest:0')
    assert pending(flow)['result'].rows[0].transaction_id == 'a'
    put(flow, card('Settle Bob paid 330 for bed', [bill('bed', 'Bed', trip=None, shares={2: 33000})]))
    await click(flow, 'su:everyday')
    assert pending(flow)['result'].trip_id is None and pending(flow)['result'].can_confirm


async def test_empty_selection_and_reselected_share_save_in_bill_currency(flow):
    await click(flow, 'su:bills')
    await click(flow, 'su:share:0')
    await click(flow, 'su:share:1')
    assert pending(flow)['choices'].shares == () and not pending(flow)['result'].can_confirm
    await click(flow, 'su:share:0')
    await click(flow, 'su:back')
    await click(flow, 'su:confirm')
    assert bot.add_settlement.call_args.kwargs['clears_currency'] == 'SGD'
    assert bot.add_settlement.call_args.kwargs['bills'] == [dict(transaction_id='saree', for_id=3, cents=12000)]


async def test_foreign_manual_selection_keeps_provenance(flow):
    value = card('Settle Erin paid 100 SGD for diwali deco', [bill(description='diwali deco', currency='INR', shares={5: 726000})],
                 clears_cents=645200, fx_rate=Decimal('64.52'), fx_rate_date=TODAY,
                 fx_source='frankfurter', fx_pair=('SGD', 'INR'))
    put(flow, value)
    await click(flow, 'su:bills')
    await click(flow, 'su:share:0')
    await click(flow, 'su:share:0')
    await click(flow, 'su:back')
    assert pending(flow)['result'].currency == 'INR' and pending(flow)['clears_cents'] == 645200
    await click(flow, 'su:confirm')
    assert bot.add_settlement.call_args.kwargs['fx_rate'] == Decimal('64.52')
    bot.reference_rate.assert_not_awaited()


async def test_failed_final_edit_stays_saved_and_answer_failure_is_once(flow):
    update, query = tap('su:confirm')
    query.edit_message_text.side_effect = [None, RuntimeError('final edit failed')]
    await bot.handle_settle_up_button(update, flow)
    assert bot._card_outcome(flow, 20) == 'saved'
    query.answer.assert_awaited_once_with()
    update, query = tap('su:confirm')
    query.answer.side_effect = RuntimeError('expired answer')
    with pytest.raises(RuntimeError, match='expired answer'):
        await bot.handle_settle_up_button(update, flow)
    query.answer.assert_awaited_once()
    query.edit_message_text.assert_not_awaited()
    bot.add_settlement.assert_called_once()


@pytest.mark.parametrize('rate', [None, (Decimal('64.52'), date(2026, 11, 18))])
async def test_rate_found_or_missing_and_typed_reply(flow, monkeypatch, rate, capsys):
    base = card('Settle Erin paid 100 SGD for diwali deco', [bill(description='diwali deco', currency='INR', shares={5: 726000})])
    snap = {k: base[k] for k in ('bills', 'trip_id', 'scope_labels', 'names', 'people')}
    monkeypatch.setattr(bot, '_load_settle_snapshot', lambda *_: (snap, PEOPLE, FAMILIES))
    bot.reference_rate.side_effect = None
    bot.reference_rate.return_value = rate
    update, status = _update(ALICE), _status_msg()
    status.message_id = 20
    update.message.reply_text.return_value = MagicMock(message_id=30)
    await bot.process_expense_text(update, flow, 'Settle Erin paid 100 SGD for diwali deco', status, DIRECTORY)
    bot.reference_rate.assert_awaited_once_with('SGD', 'INR', TODAY)
    assert capsys.readouterr().out.count('settle card ') == 1
    if rate is not None:
        assert pending(flow)['clears_cents'] == 645200
        query = await click(flow, 'su:amount')
        prompt_id = query.message.reply_text.return_value.message_id
    else:
        assert not pending(flow)['result'].can_confirm
        prompt_id = 30
    reply = _update(ALICE, '6400 INR')
    reply.message.reply_to_message.message_id = prompt_id
    assert await bot._settle_up_reply(reply, flow)
    assert pending(flow)['clears_cents'] == 640000
    assert pending(flow)['fx_rate'] == Decimal(64) and pending(flow)['fx_source'] == 'typed'
    assert pending(flow)['fx_rate_date'] == TODAY
    original = pending(flow)
    for invalid in ('bad', '6400 SGD', '0', '-1'):
        reply.message.text = invalid
        # Each consumed reply drops its old prompt link; restore this target so
        # every invalid amount exercises the same pending card.
        flow.user_data.setdefault('settle_up_prompts', {})[prompt_id] = (20, 'INR')
        assert await bot._settle_up_reply(reply, flow)
        assert pending(flow) is original
    await click(flow, 'su:confirm')
    saved = bot.add_settlement.call_args.kwargs
    assert saved['fx_rate'] == Decimal(64) and saved['needs_statement_check']
    assert saved['paid_amount'] == '100.00' and saved['clears_amount'] == '6400.00'


async def test_orphan_fx_reply_never_extracts(flow, monkeypatch):
    monkeypatch.setattr(bot, 'authorize', AsyncMock(return_value=DIRECTORY))
    update = _update(ALICE, '6400')
    update.message.reply_to_message.text = 'Reply with how much INR this clears, e.g. 6452'
    update.message.reply_to_message.from_user.id = flow.bot.id
    await bot.handle_text(update, flow)
    update.message.reply_text.assert_awaited_once_with(bot.SETTLE_UP_GONE)
    bot.extract_transactions.assert_not_awaited()


async def test_lock_serializes_redraw_then_confirm(flow):
    entered, release = asyncio.Event(), asyncio.Event()
    update, query = tap('su:bills')
    async def edit(*args, **kwargs):
        entered.set()
        await release.wait()
    query.edit_message_text.side_effect = edit
    first = asyncio.create_task(bot.handle_settle_up_button(update, flow))
    await entered.wait()
    second = asyncio.create_task(click(flow, 'su:confirm'))
    await asyncio.sleep(0)
    bot.add_settlement.assert_not_called()
    release.set()
    await asyncio.gather(first, second)
    # Confirm is no longer displayed in the bills menu, so this stale tap does nothing.
    assert pending(flow)['view'] == 'bills'
    bot.add_settlement.assert_not_called()


def test_snapshot_pages_trips_and_folds_each_separately(monkeypatch):
    other = replace(BEACH, id=11, name='City trip', families=((1, 'Tan'), (3, 'Lee')))
    trip_read = MagicMock(side_effect=lambda pid, tid: None if tid is None else {10: BEACH, 11: other}[tid])
    monkeypatch.setattr(bot, '_load_bals_trip', trip_read)
    pages = MagicMock(side_effect=[([BEACH], True), ([other], False)])
    monkeypatch.setattr(bot, '_load_bals_choices', pages)
    reads = MagicMock(return_value=())
    monkeypatch.setattr(bot, '_load_settle_bills', reads)
    value, people, families = bot._load_settle_snapshot(DIRECTORY.participants[0], DIRECTORY)
    assert value['trip_id'] is None and people == DIRECTORY.participants and families == {}
    assert [c.args[0] for c in reads.call_args_list] == [None, BEACH, other]
    assert pages.call_args_list[1].args == (1, bot.TRIP_PAGE_SIZE)
    trip_read.side_effect = lambda *_: BEACH
    reads.reset_mock()
    value, people, families = bot._load_settle_snapshot(DIRECTORY.participants[0], DIRECTORY)
    assert value['trip_id'] == 10 and families == FAMILIES
    assert [c.args[0] for c in reads.call_args_list] == [None, BEACH]


def test_bill_snapshot_skips_missing_payer_and_failed_reads(monkeypatch):
    # transactions.date is ISO text: a str here once crashed the close-match card.
    entries = [dict(transaction_id='a', date=TODAY.isoformat(), description='dinner', amount='12.50',
                    currency='SGD', paid_by=1, splits={3: 1250}),
               dict(transaction_id='b', paid_by=None, currency='SGD', splits={3: 1250})]
    monkeypatch.setattr(bot, 'get_trip_split_entries', lambda _: entries)
    monkeypatch.setattr(bot, 'get_trip_settlements', lambda _: [])
    bills = bot._load_settle_bills(BEACH)
    assert len(bills) == 1 and bills[0].amount_cents == 1250 and bills[0].open == {3: 1250}
    assert bills[0].date == TODAY
    monkeypatch.setattr(bot, 'get_trip_settlements', lambda _: None)
    with pytest.raises(RuntimeError):
        bot._load_settle_bills(BEACH)


async def test_tap_on_a_card_this_person_has_no_state_for_is_a_toast_only(flow):
    """Never wipe the card: pending cards live in the tapper's own user_data."""
    flow.user_data['pending_settle_ups'] = {}
    query = await click(flow, 'su:confirm')
    query.answer.assert_awaited_once_with(bot.SETTLE_UP_GONE)
    query.edit_message_text.assert_not_awaited()


async def test_a_refused_menu_choice_is_a_toast_and_keeps_the_card(flow, monkeypatch):
    """A giver who owes nothing here is refused by the reader; the card must survive it."""
    before = pending(flow)
    def refuse(_card):
        raise bot.SettleRefused('No match.')
    monkeypatch.setattr(bot, '_su_state', refuse)
    query = await click(flow, 'su:giver')
    query.answer.assert_awaited_once_with('No match.', show_alert=True)
    query.edit_message_text.assert_not_awaited()
    assert pending(flow) is before


async def test_a_refused_menu_choice_stays_on_screen_as_an_alert(flow, monkeypatch):
    """A toast vanished in a second on the test bot, so the tap looked dead."""
    def refuse(_card):
        raise bot.SettleRefused('No match.')
    monkeypatch.setattr(bot, '_su_state', refuse)
    query = await click(flow, 'su:giver')
    assert query.answer.await_args.kwargs == {'show_alert': True}


async def test_a_crash_drawing_the_card_says_so_instead_of_hanging(flow, monkeypatch, capsys):
    """Found on the test bot: a crash left "Extracting data..." up with no answer."""
    monkeypatch.setattr(bot, '_load_settle_snapshot', lambda *_: snapshot())
    monkeypatch.setattr(bot, 'build_settle_card', MagicMock(side_effect=AttributeError('day')))
    status = _status_msg()
    await bot.process_expense_text(_update(ALICE), flow, 'Settle Carol paid 120', status, DIRECTORY)
    status.edit_text.assert_awaited_once_with(bot.SETTLE_UP_FAILED)
    assert 'Settle card failed: AttributeError' in capsys.readouterr().out


# ---- amount prompt and reply cleanup (copies the split-prompt cleanup) ----

def _deleted(context):
    """Message ids passed to delete_message, in order."""
    return [c.kwargs['message_id'] for c in context.bot.delete_message.await_args_list]


async def _open_prompt(context, prompt_id=30):
    """Tap 'Change amount cleared' and return the prompt's (fictional) message id."""
    # The amount button only shows on a card paid in another currency, so use one
    # (replacing the card also on a second call is harmless: same shape each time).
    base = card('Settle Erin paid 100 SGD for diwali deco',
                [bill(description='diwali deco', currency='INR', shares={5: 726000})],
                clears_cents=645200, fx_rate=Decimal(64.52), fx_rate_date=TODAY,
                fx_source='frankfurter', fx_pair=('SGD', 'INR'))
    context.user_data['pending_settle_ups'][20] = put(context, base)
    update, query = tap('su:amount')
    query.message.reply_text.return_value = MagicMock(message_id=prompt_id)
    await bot.handle_settle_up_button(update, context)
    return prompt_id


def _reply(prompt_id, text, message_id=77):
    reply = _update(ALICE, text)
    reply.message.message_id = message_id
    reply.message.reply_to_message.message_id = prompt_id
    return reply


async def test_read_reply_deletes_reply_and_prompt_and_drops_links(flow):
    flow.bot.delete_message = AsyncMock()
    prompt_id = await _open_prompt(flow)
    assert flow.user_data['settle_up_prompts'][prompt_id] == (20, 'INR') or prompt_id in flow.user_data['settle_up_prompts']
    assert flow.user_data['settle_up_prompt_of_card'] == {20: prompt_id}
    assert await bot._settle_up_reply(_reply(prompt_id, '381'), flow)
    assert sorted(_deleted(flow)) == [30, 77]
    assert flow.user_data['settle_up_prompts'] == {}
    assert flow.user_data['settle_up_prompt_of_card'] == {}


async def test_refused_reply_replaces_prompt_with_error(flow):
    """Refused amounts get one replacement prompt instead of a separate error message."""
    flow.bot.delete_message = AsyncMock()
    prompt_id = await _open_prompt(flow)
    reply = _reply(prompt_id, 'bad')
    reply.message.reply_text.return_value.message_id = 31
    before = pending(flow)
    assert await bot._settle_up_reply(reply, flow)
    reply.message.reply_text.assert_awaited_once()
    assert reply.message.reply_text.await_args.args[0].startswith(
        'Reply with how much INR this clears, e.g. 6452\n⚠️ ')
    assert reply.message.reply_text.await_args.kwargs['reply_markup'].force_reply
    assert _deleted(flow) == [30, 77]
    assert flow.user_data['settle_up_prompts'] == {31: (20, 'INR')}
    assert flow.user_data['settle_up_prompt_of_card'] == {20: 31}
    assert pending(flow) is before


async def test_second_change_amount_deletes_the_first_prompt(flow):
    flow.bot.delete_message = AsyncMock()
    first = await _open_prompt(flow, 30)
    second = await _open_prompt(flow, 31)
    assert _deleted(flow) == [first]
    assert list(flow.user_data['settle_up_prompts']) == [second]
    assert flow.user_data['settle_up_prompt_of_card'] == {20: second}


@pytest.mark.parametrize('action', ['su:confirm', 'su:cancel'])
async def test_confirm_and_cancel_delete_the_open_prompt(flow, action):
    flow.bot.delete_message = AsyncMock()
    prompt_id = await _open_prompt(flow)
    await click(flow, action)
    assert _deleted(flow) == [prompt_id]
    assert flow.user_data['settle_up_prompts'] == {}
    assert flow.user_data['settle_up_prompt_of_card'] == {}


async def test_failed_save_keeps_the_prompt(flow):
    flow.bot.delete_message = AsyncMock()
    prompt_id = await _open_prompt(flow)
    bot.add_settlement.side_effect = RuntimeError('offline')
    await click(flow, 'su:confirm')
    flow.bot.delete_message.assert_not_awaited()
    assert flow.user_data['settle_up_prompt_of_card'] == {20: prompt_id}


async def test_expired_prompt_reply_deletes_both_and_says_gone(flow):
    flow.bot.delete_message = AsyncMock()
    reply = _reply(55, '381', message_id=78)
    reply.message.reply_to_message.text = 'Reply with how much INR this clears, e.g. 6452'
    reply.message.reply_to_message.from_user.id = flow.bot.id
    assert await bot._settle_up_reply(reply, flow)
    assert sorted(_deleted(flow)) == [55, 78]
    reply.message.reply_text.assert_awaited_once_with(bot.SETTLE_UP_GONE)


async def test_failing_delete_does_not_stop_the_redraw(flow):
    flow.bot.delete_message = AsyncMock(side_effect=RuntimeError('too old'))
    prompt_id = await _open_prompt(flow)
    assert await bot._settle_up_reply(_reply(prompt_id, '381'), flow)
    assert pending(flow)['clears_cents'] == 38100
    flow.bot.edit_message_text.assert_awaited()


async def test_failed_redraw_after_a_reply_replaces_prompt_and_keeps_card(flow, capsys):
    """The unchanged card gets a fresh prompt explaining why the redraw failed."""
    # The reply is already deleted by then, so silence would look like the bot ignored it.
    flow.bot.delete_message = AsyncMock()
    prompt_id = await _open_prompt(flow)
    flow.bot.edit_message_text = AsyncMock(side_effect=RuntimeError('telegram down'))
    reply = _reply(prompt_id, '381')
    reply.message.reply_text.return_value.message_id = 31
    assert await bot._settle_up_reply(reply, flow)
    reply.message.reply_text.assert_awaited_once()
    assert reply.message.reply_text.await_args.args[0] == (
        "Reply with how much INR this clears, e.g. 6452\n⚠️ Couldn't change the card. Reply again.")
    assert reply.message.reply_text.await_args.kwargs['reply_markup'].force_reply
    assert pending(flow)['clears_cents'] == 645200
    assert flow.user_data['settle_up_prompt_of_card'] == {20: 31}
    assert flow.user_data['settle_up_prompts'] == {31: (20, 'INR')}
    assert _deleted(flow) == [30, 77]
    assert 'Settle amount reply failed: RuntimeError' in capsys.readouterr().out


async def test_tap_that_leaves_no_other_currency_deletes_the_open_prompt(flow):
    # Say a new receiver's bills are in the paid currency: the amount prompt is stale.
    flow.bot.delete_message = AsyncMock()
    prompt_id = await _open_prompt(flow)
    put(flow)  # same-currency card, prompt still linked to it
    await click(flow, 'su:bills')
    assert _deleted(flow) == [prompt_id]
    assert flow.user_data['settle_up_prompts'] == {}
    assert flow.user_data['settle_up_prompt_of_card'] == {}


@pytest.mark.parametrize('case', ['refused', 'gone', 'restart'])
async def test_answer_is_sent_before_the_reply_is_deleted(flow, case):
    """In a group chat the answer quotes the reply, and Telegram refuses to quote a deleted one."""
    flow.bot.delete_message = AsyncMock()
    prompt_id = await _open_prompt(flow)
    reply = _reply(prompt_id, 'bad' if case == 'refused' else '381')
    if case == 'gone':
        flow.user_data['pending_settle_ups'].clear()
    if case == 'restart':
        flow.user_data['settle_up_prompts'].clear()
        reply.message.reply_to_message.text = 'Reply with how much INR this clears, e.g. 6452'
        reply.message.reply_to_message.from_user.id = flow.bot.id

    async def answer(*args, **kwargs):
        assert 77 not in _deleted(flow), 'answered a reply that was already deleted'
        return MagicMock(message_id=31)
    reply.message.reply_text.side_effect = answer
    assert await bot._settle_up_reply(reply, flow)
    reply.message.reply_text.assert_awaited_once()
    assert 77 in _deleted(flow)


# ---- a fresh /trip_bals after a saved typed payback (#151) ----

def _report_reads(monkeypatch):
    """Allow the one read a saved trip payback makes: that trip's balances, for a new report."""
    load = MagicMock(return_value=BEACH)
    screen = MagicMock(return_value=('fresh balances', None))
    monkeypatch.setattr(bot, '_load_bals_trip', load)
    monkeypatch.setattr(bot, '_bals_screen', screen)
    return load, screen


async def test_saved_trip_payback_sends_a_new_report_from_the_card(flow, monkeypatch):
    """The report goes out below the saved card, with the names the card was read with."""
    load, screen = _report_reads(monkeypatch)
    update, query = tap('su:confirm')

    async def send(**kwargs):
        assert query.edit_message_text.await_args.args[0].endswith('✅ Payback saved.')
        return MagicMock(message_id=40)
    flow.bot.send_message = AsyncMock(side_effect=send)
    await bot.handle_settle_up_button(update, flow)
    load.assert_called_once_with(1, 10)  # the sender's membership-checked trip read
    screen.assert_called_once_with(BEACH, DIRECTORY, TODAY, 'SGD')
    kwargs = flow.bot.send_message.await_args.kwargs
    assert kwargs['chat_id'] == query.message.chat_id and kwargs['text'] == 'fresh balances'
    assert 'parse_mode' not in kwargs
    # Registered like a /trip_bals reply, so its "Mark paid" buttons work.
    assert flow.user_data['bals_reports'][40] is DIRECTORY
    bot._load_directory.assert_not_called()


async def test_everyday_payback_sends_no_report(flow, monkeypatch):
    """No trip, no /trip_bals: an everyday payback only shows its saved card."""
    load, _ = _report_reads(monkeypatch)
    put(flow, card('Settle Bob paid 330 for bed', [bill('bed', 'Bed', trip=None, shares={2: 33000})]))
    await click(flow, 'su:everyday')
    flow.bot.send_message = AsyncMock()
    query = await click(flow, 'su:confirm')
    assert query.edit_message_text.await_args.args[0].endswith('✅ Payback saved.')
    load.assert_not_called()
    flow.bot.send_message.assert_not_awaited()


@pytest.mark.parametrize('case', ['cancel', 'failed_save'])
async def test_no_report_without_a_save(flow, monkeypatch, case):
    load, _ = _report_reads(monkeypatch)
    flow.bot.send_message = AsyncMock()
    if case == 'failed_save':
        bot.add_settlement.side_effect = RuntimeError('offline')
    await click(flow, 'su:cancel' if case == 'cancel' else 'su:confirm')
    load.assert_not_called()
    flow.bot.send_message.assert_not_awaited()


@pytest.mark.parametrize('failure', ['send', 'read', 'not_member'])
async def test_report_failure_leaves_the_saved_card(flow, monkeypatch, capsys, failure):
    """The payback is saved either way; a failed report is only printed."""
    load, _ = _report_reads(monkeypatch)
    flow.bot.send_message = AsyncMock(side_effect=RuntimeError('offline') if failure == 'send' else None)
    if failure == 'read':
        load.side_effect = RuntimeError('offline')
    if failure == 'not_member':
        load.return_value = None
    query = await click(flow, 'su:confirm')
    assert query.edit_message_text.await_args.args[0].endswith('✅ Payback saved.')
    assert bot._card_outcome(flow, 20) == 'saved'
    assert 'bals_reports' not in flow.user_data or not flow.user_data['bals_reports']
    if failure != 'send':
        flow.bot.send_message.assert_not_awaited()
    else:
        assert 'Could not send fresh balances' in capsys.readouterr().out
