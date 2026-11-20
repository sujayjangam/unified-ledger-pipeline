"""Paybacks use fictional people and fake connections; no database or Telegram traffic."""
import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace

import pytest

import app.bot_core as bot
from app.services import settlements, ledger_queries
from test_bot_core import ALICE, _context, _update
from test_trip_bals import BEACH, ENTRIES, _tap, _buttons


def card(**changes):
    """A pending payback from Carol to Alice, with all names invented."""
    result = dict(trip_id=7, trip_name="Beach", from_id=3, to_id=1,
                  clears_cents=3000, clears_currency="AUD", paid_cents=3000, paid_currency="AUD",
                  suggested_cents=3000, debtor_member_ids=(3,), for_id=None, idempotency_key="fictional-key",
                  date=date(2026, 11, 17), entered_by_participant_id=1, family="Lee",
                  names={1: "Alice", 2: "Bob", 3: "Carol"}, people=tuple(SimpleNamespace(id=i) for i in (1, 2, 3)), currencies={'AUD', 'SGD', 'IDR'})
    result.update(changes)
    return result


@pytest.fixture
def flow(monkeypatch, directory):
    """Give the handler a report snapshot; any directory reload would fail this test."""
    monkeypatch.setattr(bot, '_load_directory', MagicMock(side_effect=AssertionError("Do not reload")))
    monkeypatch.setattr(bot, '_load_bals_trip', MagicMock(return_value=BEACH))
    monkeypatch.setattr(bot, 'get_trip_split_entries', MagicMock(return_value=ENTRIES))
    monkeypatch.setattr(bot, 'get_trip_settlements', MagicMock(return_value=[]))
    monkeypatch.setattr(bot, 'get_sgt_now', lambda: MagicMock(date=lambda: date(2026, 11, 17)))
    context = _context()
    context.bot.edit_message_text = AsyncMock()
    context.bot.delete_message = AsyncMock()
    context.bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=40))
    context.user_data['bals_reports'] = {10: directory}
    return context


@pytest.fixture
def authorized(monkeypatch, directory):
    """Typed replies go through authorize() first, like every message; let it pass here."""
    monkeypatch.setattr(bot, 'authorize', AsyncMock(return_value=directory))


def tap(data, message_id=20):
    """Make the awaited Telegram methods behave like a real callback's methods."""
    update, query = _tap(data)
    query.message.message_id = message_id
    query.message.chat_id = 100
    query.edit_message_reply_markup = AsyncMock()
    query.message.reply_text = AsyncMock(return_value=MagicMock(message_id=20))
    return update, query


async def test_new_card_requires_family_choice_and_checks_membership(flow):
    """A sender outside the owing family must explicitly pick a payer from the trip."""
    update, query = tap('settle:new:7:1:3000', 10)
    await bot.handle_settle_button(update, flow)
    bot._load_bals_trip.assert_called_once_with(1, 7)
    pending = flow.user_data['pending_settlements'][20]
    assert pending['directory'] is flow.user_data['bals_reports'][10]
    assert pending['report_message_id'] == 10
    assert pending['from_id'] is None and pending['debtor_member_ids'] == (3,)
    buttons = _buttons(query.message.reply_text.await_args.kwargs['reply_markup'])
    assert buttons['Carol'] == 'settle:person:3' and 'Confirm' not in buttons
    # Alice is owed, so she can't pay herself. Bob, her relative, may pay on Lee's behalf.
    assert 'Alice' not in buttons and buttons['Bob'] == 'settle:person:2'
    query.answer.assert_awaited_once()

    update, query = tap('settle:person:1')
    await bot.handle_settle_button(update, flow)
    assert flow.user_data['pending_settlements'][20]['from_id'] is None
    update, query = tap('settle:person:999')
    await bot.handle_settle_button(update, flow)
    assert flow.user_data['pending_settlements'][20]['from_id'] is None
    query.edit_message_text.assert_not_awaited()
    update, query = tap('settle:person:3')
    await bot.handle_settle_button(update, flow)
    assert flow.user_data['pending_settlements'][20]['from_id'] == 3
    assert 'Confirm' in _buttons(query.edit_message_text.await_args.kwargs['reply_markup'])


async def test_new_card_defaults_to_sender_in_family(flow, monkeypatch):
    """Alice can pay for Tan; a person without a family defaults to that person."""
    from dataclasses import replace
    monkeypatch.setattr(bot, 'get_trip_split_entries', lambda _: [{'currency': 'AUD', 'paid_by': 3, 'splits': {1: 3000}}])
    for trip in (BEACH, replace(BEACH, families=())):
        bot._load_bals_trip.return_value = trip
        update, query = tap('settle:new:7:1:3000', 10)
        await bot.handle_settle_button(update, flow)
        assert flow.user_data['pending_settlements'][20]['from_id'] == 1
        assert 'Confirm' in _buttons(query.message.reply_text.await_args.kwargs['reply_markup'])


def three_families(monkeypatch):
    """Bob settles alone here (family "Ng"), so he can pay Carol's debt to Alice as a gift.

    Anyone but the receiver may be the giver, even the receiver's own relative (ADR-0051).
    """
    from dataclasses import replace
    bot._load_bals_trip.return_value = replace(BEACH, families=((1, "Tan"), (2, "Ng"), (3, "Lee")))
    monkeypatch.setattr(bot, 'get_trip_split_entries',
                        MagicMock(return_value=[{'currency': 'AUD', 'paid_by': 1, 'splits': {3: 3000}}]))


async def test_gift_menu_snapshot_switch_and_confirm(flow, monkeypatch):
    """Trip people outside the receiver's family can pay, debtors first, beneficiary kept."""
    three_families(monkeypatch)
    update, query = tap('settle:new:7:1:3000', 10)
    await bot.handle_settle_button(update, flow)
    pending = flow.user_data['pending_settlements'][20]
    assert [p.id for p in pending['people']] == [3, 2]
    update, query = tap('settle:person:2')
    await bot.handle_settle_button(update, flow)
    gift = flow.user_data['pending_settlements'][20]
    assert gift['for_id'] == 3 and gift['debtor_member_ids'] == (3,)
    assert 'From: Bob, on behalf of Lee family' in query.edit_message_text.await_args.args[0]
    update, query = tap('settle:person:3')
    await bot.handle_settle_button(update, flow)
    assert flow.user_data['pending_settlements'][20]['for_id'] is None
    update, query = tap('settle:person:2')
    await bot.handle_settle_button(update, flow)
    writer = MagicMock(return_value=True)
    monkeypatch.setattr(bot, 'add_settlement', writer)
    update, query = tap('settle:confirm')
    await bot.handle_settle_button(update, flow)
    assert writer.call_args.kwargs['for_id'] == 3
    assert writer.call_args.kwargs['from_id'] == 2


async def test_failed_gift_redraw_keeps_original_card(flow, monkeypatch):
    """A failed Telegram edit must not silently change the payer or beneficiary."""
    three_families(monkeypatch)
    update, query = tap('settle:new:7:1:3000', 10)
    await bot.handle_settle_button(update, flow)
    original = flow.user_data['pending_settlements'][20]
    update, query = tap('settle:person:2')
    query.edit_message_text.side_effect = RuntimeError('offline')
    with pytest.raises(RuntimeError):
        await bot.handle_settle_button(update, flow)
    assert flow.user_data['pending_settlements'][20] is original


async def test_person_in_directory_but_not_trip_is_not_offered(flow):
    """The directory snapshot supplies names, but only trip members can be payers."""
    from dataclasses import replace

    bot._load_bals_trip.return_value = replace(BEACH, member_ids=(1, 3))
    update, query = tap('settle:new:7:1:3000', 10)
    await bot.handle_settle_button(update, flow)
    pending = flow.user_data['pending_settlements'][20]
    assert [p.id for p in pending['people']] == [3]   # Bob isn't on the trip; Alice is owed
    update, query = tap('settle:person:2')
    await bot.handle_settle_button(update, flow)
    query.edit_message_text.assert_not_awaited()
    assert flow.user_data['pending_settlements'][20] is pending


def test_overpaid_gift_names_giver_and_handles_lone_debtor():
    """Gift labels match report labels and the excess is owed to the giver."""
    for family, label in [('Lee', 'Lee family'), ('Cedar Grove', 'Cedar Grove'), (None, 'Carol')]:
        text = settlements.settlement_card_text(card(from_id=2, for_id=3, family=family,
                                                    clears_cents=4000, paid_cents=4000))
        assert f'From: Bob, on behalf of {label}' in text
        assert 'owed back to Bob.' in text


@pytest.mark.parametrize('data', ['settle:new:7:1:2999', 'settle:new:7:3:3000', 'settle:new:7:0:3000'])
async def test_stale_or_invalid_buttons_change_nothing(flow, data):
    """Old suggestions cannot silently create a card for a different amount."""
    update, query = tap(data, 10)
    await bot.handle_settle_button(update, flow)
    query.answer.assert_awaited_once()
    if ':0:' not in data:
        assert query.answer.await_args.args == (bot.SETTLE_STALE,)
    query.message.reply_text.assert_not_awaited()
    assert 'pending_settlements' not in flow.user_data


@pytest.mark.parametrize('missing', ['get_trip_split_entries', 'get_trip_settlements'])
async def test_new_card_read_failure_is_not_a_settled_trip(flow, missing):
    """Both reads must succeed before a card can be made."""
    getattr(bot, missing).return_value = None
    update, query = tap('settle:new:7:1:3000', 10)
    await bot.handle_settle_button(update, flow)
    query.answer.assert_awaited_once_with(bot.BALS_ERROR)
    query.message.reply_text.assert_not_awaited()


@pytest.mark.parametrize('inserted', [True, False])
async def test_confirm_and_double_tap_save_once(flow, monkeypatch, inserted):
    """Even an already-inserted key is success; a resolved card only gets a toast."""
    pending = card()
    flow.user_data['pending_settlements'] = {20: pending}
    update, query = tap('settle:confirm')
    def save(**kwargs):
        """Check that progress reached Telegram before the blocking write."""
        query.edit_message_reply_markup.assert_awaited_once()
        assert kwargs['source'] == 'telegram_bot'
        assert kwargs['entered_by_participant_id'] == 1 and kwargs['date'] == date(2026, 11, 17)
        assert kwargs['clears_amount'] == '30.00' and kwargs['idempotency_key'] == 'fictional-key'
        return inserted
    writer = MagicMock(side_effect=save)
    monkeypatch.setattr(bot, 'add_settlement', writer)
    other, other_query = tap('settle:confirm')
    await asyncio.gather(bot.handle_settle_button(update, flow), bot.handle_settle_button(other, flow))
    writer.assert_called_once()
    query.answer.assert_awaited_once()
    other_query.answer.assert_awaited_once()
    other_query.edit_message_text.assert_not_awaited()
    assert query.edit_message_text.await_args.args[0].endswith('✅ Payback saved.')
    assert query.edit_message_text.await_args.kwargs['reply_markup'] is None


async def test_failed_save_restores_same_key_and_buttons(flow, monkeypatch):
    """A retry keeps the original key even if the failed call reached Postgres."""
    pending = card(clears_cents=4000, paid_cents=4000,
                   directory=flow.user_data['bals_reports'][10], report_message_id=10)
    flow.user_data['pending_settlements'] = {20: pending}
    monkeypatch.setattr(bot, 'add_settlement', MagicMock(side_effect=RuntimeError('offline')))
    update, query = tap('settle:confirm')
    await bot.handle_settle_button(update, flow)
    assert flow.user_data['pending_settlements'][20] is pending
    assert bot._card_outcome(flow, 20) is None
    assert '⚠️ Confirm overpayment' in _buttons(query.edit_message_text.await_args.kwargs['reply_markup'])
    query.answer.assert_awaited_once()
    flow.bot.send_message.assert_not_awaited()


async def test_cancel_and_resolved_menu_are_toast_only(flow):
    """Cancelling removes the buttons and no later tap redraws the card."""
    flow.user_data['pending_settlements'] = {
        20: card(directory=flow.user_data['bals_reports'][10], report_message_id=10)}
    update, query = tap('settle:cancel')
    await bot.handle_settle_button(update, flow)
    assert query.edit_message_text.await_args.args[0].endswith('❌ Cancelled.')
    update, query = tap('settle:who')
    await bot.handle_settle_button(update, flow)
    query.answer.assert_awaited_once()
    query.edit_message_text.assert_not_awaited()
    flow.bot.send_message.assert_not_awaited()


@pytest.mark.parametrize('value,side,expected', [('40', 'clears', (4000, 'AUD', 4000)),
                                                ('50 SGD', 'paid', (3000, 'SGD', 5000)),
                                                ('20 AUD', 'paid', (2000, 'AUD', 2000))])
async def test_prompt_reply_routes_before_expenses(flow, authorized, monkeypatch, value, side, expected):
    """An amount reply changes this card, never creates an expense."""
    flow.user_data['pending_settlements'] = {20: card()}
    update, query = tap('settle:' + side)
    query.message.reply_text.return_value.message_id = 30
    await bot.handle_settle_button(update, flow)
    assert query.message.reply_text.await_args.kwargs['reply_markup'].force_reply
    reply = _update(ALICE, value)
    reply.message.reply_to_message.message_id = 30
    extract = AsyncMock()
    monkeypatch.setattr(bot, 'process_expense_text', extract)
    await bot.handle_text(reply, flow)
    updated = flow.user_data['pending_settlements'][20]
    assert (updated['clears_cents'], updated['paid_currency'], updated['paid_cents']) == expected
    extract.assert_not_awaited()
    assert flow.bot.edit_message_text.await_args.kwargs['message_id'] == 20
    assert [c.kwargs['message_id'] for c in flow.bot.delete_message.await_args_list] == [30, reply.message.message_id]
    assert flow.user_data['settlement_prompts'] == {}
    assert flow.user_data['settlement_prompt_of_card'] == {}


@pytest.mark.parametrize('value', ['banana', '-5', '0', 'NaN', '50 XYZ', '50 SGF', '50 SGD extra'])
async def test_bad_reply_does_not_change_card_or_reach_model(flow, authorized, monkeypatch, value):
    """Bad input stays in the payback flow and explains the expected format."""
    pending = card()
    flow.user_data.update(pending_settlements={20: pending}, settlement_prompts={30: (20, 'paid')})
    extract = AsyncMock()
    monkeypatch.setattr(bot, 'process_expense_text', extract)
    update = _update(ALICE, value)
    update.message.reply_to_message.message_id = 30
    update.message.reply_text.return_value.message_id = 31
    await bot.handle_text(update, flow)
    assert flow.user_data['pending_settlements'][20] is pending
    update.message.reply_text.assert_awaited_once()
    assert update.message.reply_text.await_args.args[0].startswith(bot.SETTLE_PROMPTS['paid'] + '\n⚠️ ')
    assert update.message.reply_text.await_args.kwargs['reply_markup'].force_reply
    assert [c.kwargs['message_id'] for c in flow.bot.delete_message.await_args_list] == [30, update.message.message_id]
    assert flow.user_data['settlement_prompts'] == {31: (20, 'paid')}
    assert flow.user_data['settlement_prompt_of_card'] == {20: 31}
    flow.bot.edit_message_text.assert_not_awaited()
    extract.assert_not_awaited()


async def test_reply_to_resolved_prompt_never_becomes_expense(flow, authorized, monkeypatch):
    """Old prompt links remain recognisable after the card is gone."""
    flow.user_data['settlement_prompts'] = {30: (20, 'paid')}
    extract = AsyncMock()
    monkeypatch.setattr(bot, 'process_expense_text', extract)
    update = _update(ALICE, '50 SGD')
    update.message.reply_to_message.message_id = 30
    await bot.handle_text(update, flow)
    extract.assert_not_awaited()
    update.message.reply_text.assert_awaited_once_with(bot.SETTLE_GONE)
    assert [c.kwargs['message_id'] for c in flow.bot.delete_message.await_args_list] == [30, update.message.message_id]
    assert flow.user_data['settlement_prompts'] == {}


@pytest.mark.parametrize('suffix', ['', '\n⚠️ Use a positive amount.'])
async def test_reply_after_restart_never_becomes_expense(flow, authorized, monkeypatch, suffix):
    """The bot's own prompt text protects replies even when its memory has been lost."""
    update = _update(ALICE, '50 SGD')
    update.message.reply_to_message.text = bot.SETTLE_PROMPTS['paid'] + suffix
    update.message.reply_to_message.from_user.id = flow.bot.id
    extract = AsyncMock()
    monkeypatch.setattr(bot, 'process_expense_text', extract)
    await bot.handle_text(update, flow)
    extract.assert_not_awaited()
    update.message.reply_text.assert_awaited_once_with(bot.SETTLE_GONE)
    assert [c.kwargs['message_id'] for c in flow.bot.delete_message.await_args_list] == [
        update.message.message_id, update.message.reply_to_message.message_id]


@pytest.mark.parametrize('side', ['clears', 'paid'])
async def test_second_prompt_sends_before_deleting_first(flow, side):
    """Switching either amount prompt leaves only the newly sent prompt linked."""
    flow.user_data.update(pending_settlements={20: card()}, settlement_prompts={30: (20, 'clears')},
                          settlement_prompt_of_card={20: 30})
    update, query = tap('settle:' + side)
    async def send(*args, **kwargs):
        flow.bot.delete_message.assert_not_awaited()
        return SimpleNamespace(message_id=31)
    query.message.reply_text.side_effect = send
    await bot.handle_settle_button(update, flow)
    flow.bot.delete_message.assert_awaited_once_with(chat_id=query.message.chat_id, message_id=30)
    assert flow.user_data['settlement_prompts'] == {31: (20, side)}
    assert flow.user_data['settlement_prompt_of_card'] == {20: 31}
    query.answer.assert_awaited_once()

async def test_failed_prompt_send_keeps_old_links(flow):
    """A failed replacement must leave the old prompt usable."""
    flow.user_data.update(pending_settlements={20: card()}, settlement_prompts={30: (20, 'clears')},
                          settlement_prompt_of_card={20: 30})
    update, query = tap('settle:paid')
    query.message.reply_text.side_effect = RuntimeError('offline')
    with pytest.raises(RuntimeError):
        await bot.handle_settle_button(update, flow)
    flow.bot.delete_message.assert_not_awaited()
    assert flow.user_data['settlement_prompts'] == {30: (20, 'clears')}
    assert flow.user_data['settlement_prompt_of_card'] == {20: 30}
    query.answer.assert_awaited_once()


async def test_failed_amount_redraw_replaces_prompt_without_changing_card(flow):
    """A redraw error belongs in the next ForceReply, preserving the previous amount."""
    original = card()
    flow.user_data.update(pending_settlements={20: original}, settlement_prompts={30: (20, 'clears')},
                          settlement_prompt_of_card={20: 30})
    flow.bot.edit_message_text.side_effect = RuntimeError('offline')
    update = _update(ALICE, '40')
    update.message.reply_to_message.message_id = 30
    update.message.reply_text.return_value.message_id = 31
    assert await bot._settlement_reply(update, flow)
    update.message.reply_text.assert_awaited_once()
    assert update.message.reply_text.await_args.args[0] == (
        bot.SETTLE_PROMPTS['clears'] + "\n⚠️ Couldn't change the card. Reply again.")
    assert flow.user_data['pending_settlements'][20] is original
    assert flow.user_data['settlement_prompt_of_card'] == {20: 31}
    assert [c.kwargs['message_id'] for c in flow.bot.delete_message.await_args_list] == [30, update.message.message_id]


@pytest.mark.parametrize('action', ['confirm', 'cancel'])
@pytest.mark.parametrize('delete_fails', [False, True])
async def test_resolving_card_clears_prompt_despite_delete_failure(flow, monkeypatch, action, delete_fails):
    """Cleanup failures cannot prevent saving or cancelling."""
    flow.user_data.update(pending_settlements={20: card()}, settlement_prompts={30: (20, 'clears')},
                          settlement_prompt_of_card={20: 30})
    writer = MagicMock(return_value=True)
    monkeypatch.setattr(bot, 'add_settlement', writer)
    if delete_fails:
        flow.bot.delete_message.side_effect = RuntimeError('too old')
    update, query = tap('settle:' + action)
    await bot.handle_settle_button(update, flow)
    assert writer.call_count == (1 if action == 'confirm' else 0)
    flow.bot.delete_message.assert_awaited_once_with(chat_id=query.message.chat_id, message_id=30)
    assert flow.user_data['settlement_prompts'] == {}
    assert flow.user_data['settlement_prompt_of_card'] == {}
    assert bot._card_outcome(flow, 20) == ('saved' if action == 'confirm' else 'cancelled')
    query.answer.assert_awaited_once()


async def test_saved_payback_redraw_uses_snapshot_and_retires_both_reports(flow, monkeypatch):
    """Two cards from one source leave only the latest report, using captured names."""
    directory = flow.user_data['bals_reports'][10]
    flow.user_data['pending_settlements'] = {
        i: card(directory=directory, report_message_id=10) for i in (20, 21)}
    monkeypatch.setattr(bot, 'add_settlement', MagicMock(return_value=True))
    screen = MagicMock(return_value=('fresh balances', None))
    monkeypatch.setattr(bot, '_bals_screen', screen)
    for card_id, report_id in ((20, 40), (21, 41)):
        update, query = tap('settle:confirm', card_id)
        async def send(**kwargs):
            assert query.edit_message_text.await_args.args[0].endswith('✅ Payback saved.')
            assert flow.bot.delete_message.await_count == (0 if card_id == 20 else 1)
            assert report_id not in flow.user_data['bals_reports']
            assert 'parse_mode' not in kwargs
            return SimpleNamespace(message_id=report_id)
        flow.bot.send_message.side_effect = send
        await bot.handle_settle_button(update, flow)
        screen.assert_called_with(BEACH, directory, date(2026, 11, 17))
        assert flow.user_data['bals_reports'][report_id] is directory
    assert [c.kwargs['message_id'] for c in flow.bot.delete_message.await_args_list] == [10, 10, 40]
    bot._load_bals_trip.assert_called_with(1, 7)
    bot._load_directory.assert_not_called()
    assert flow.user_data['bals_last_redraw'] == {(query.message.chat_id, 7): 41}


@pytest.mark.parametrize('failure', ['send', 'read', 'bills', 'not_member'])
async def test_report_failure_leaves_saved_card_and_old_report(flow, monkeypatch, failure):
    """A refresh is optional after saving; membership loss sends nothing.

    'bills' is a failed read of the bills behind the report: the old report stays rather
    than being swapped for an error message.
    """
    directory = flow.user_data['bals_reports'][10]
    flow.user_data['pending_settlements'] = {20: card(directory=directory, report_message_id=10)}
    monkeypatch.setattr(bot, 'add_settlement', MagicMock(return_value=True))
    if failure == 'send':
        flow.bot.send_message.side_effect = RuntimeError('offline')
    elif failure == 'read':
        bot._load_bals_trip.side_effect = RuntimeError('offline')
    elif failure == 'bills':
        monkeypatch.setattr(bot, 'get_trip_split_entries', lambda trip_id: None)
    else:
        bot._load_bals_trip.return_value = None
    update, query = tap('settle:confirm')
    await bot.handle_settle_button(update, flow)
    assert bot._card_outcome(flow, 20) == 'saved'
    assert 20 not in flow.user_data['pending_settlements']
    assert query.edit_message_text.await_args.args[0].endswith('✅ Payback saved.')
    flow.bot.delete_message.assert_not_awaited()
    assert flow.user_data['bals_reports'] == {10: directory}
    if failure != 'send':
        flow.bot.send_message.assert_not_awaited()


async def test_unauthorized_reply_never_touches_a_card(flow, monkeypatch):
    """authorize() runs before the payback check, so a refused sender changes nothing."""
    pending = card()
    flow.user_data.update(pending_settlements={20: pending}, settlement_prompts={30: (20, 'paid')})
    monkeypatch.setattr(bot, 'authorize', AsyncMock(return_value=None))
    update = _update(ALICE, '50 SGD')
    update.message.reply_to_message.message_id = 30
    await bot.handle_text(update, flow)
    assert flow.user_data['pending_settlements'][20] is pending
    flow.bot.edit_message_text.assert_not_awaited()


@pytest.mark.parametrize('value,expected', [('5000 INR', (500000, 'INR')), ('20 usd', (2000, 'USD')),
                                            ('600,000 IDR', (60000000, 'IDR'))])
def test_any_real_currency_is_accepted(value, expected):
    """A payback can be handed over in any real currency, even one no bill on the trip used."""
    assert settlements.parse_settlement_amount(value, "SGD", set()) == expected


def test_amount_with_thousands_commas_is_read():
    """Rupiah is typed with commas; they are separators, never a decimal point."""
    assert settlements.parse_settlement_amount("600,000 IDR", "SGD", {"IDR"}) == (60000000, "IDR")


async def test_failed_answer_is_never_answered_twice(flow):
    """An expired callback answer does not enter the read-error handler."""
    bot._load_bals_trip.return_value = None
    update, query = tap('settle:new:7:1:3000', 10)
    query.answer.side_effect = RuntimeError('expired')
    with pytest.raises(RuntimeError):
        await bot.handle_settle_button(update, flow)
    query.answer.assert_awaited_once_with(bot.BALS_NOT_MEMBER)


def test_card_text_and_overpayment():
    """The warning names the actual payer, and is about clears rather than paid."""
    text = settlements.settlement_card_text(card(paid_cents=5000, paid_currency='SGD'))
    assert 'From: Carol (Lee family)' in text and 'To: Alice' in text
    assert 'Clears: 30.00 AUD\nPaid: 50.00 SGD\nDate: 17 Nov' in text
    assert '⚠️' not in text
    text = settlements.settlement_card_text(card(clears_cents=4000, paid_cents=4000))
    assert 'more than the 30.00 AUD owed' in text and 'owed back to Carol.' in text


def test_amount_changes_keep_cross_currency_paid_side():
    """Changing debt does not invent a new exchange rate for money already specified."""
    original = card(paid_currency='SGD', paid_cents=5000)
    changed = settlements.change_settlement_amount(original, '40', 'clears')
    assert changed['paid_cents'] == 5000 and original['clears_cents'] == 3000
    with pytest.raises(ValueError):
        settlements.change_settlement_amount(original, '40 SGD', 'clears')
    assert settlements.parse_settlement_amount('1.005 sgd', 'AUD', {'SGD'}) == (101, 'SGD')


def write_args(**changes):
    """Fictional writer inputs use decimal text, like a sheet loader would."""
    result = dict(from_id=1, to_id=2, clears_amount='10', clears_currency='AUD',
                  paid_amount='10', paid_currency='AUD', date=date(2026, 11, 17),
                  source='test', idempotency_key='fictional-key')
    result.update(changes)
    return result


@pytest.mark.parametrize('fx_values', [dict(fx_rate=1), dict(fx_rate_date=date(2026, 11, 19)),
                                    dict(fx_source='typed'), dict(fx_rate=1, fx_source='typed')])
def test_fx_provenance_must_be_complete_before_connection(monkeypatch, fx_values):
    connect = MagicMock()
    monkeypatch.setattr(settlements, 'get_connection', connect)
    with pytest.raises(ValueError, match='all three'):
        settlements.add_settlement(**write_args(**fx_values))
    connect.assert_not_called()


def test_fx_provenance_is_written_with_payment(monkeypatch):
    from decimal import Decimal
    manager = MagicMock()
    conn = manager.__enter__.return_value
    conn.execute.return_value.rowcount = 1
    monkeypatch.setattr(settlements, 'get_connection', lambda: manager)
    values = dict(fx_rate=Decimal('64.52'), fx_rate_date=date(2026, 11, 18), fx_source='frankfurter',
                  needs_statement_check=True)
    assert settlements.add_settlement(**write_args(paid_currency='SGD', clears_currency='INR', **values))
    sql, params = conn.execute.call_args.args
    for key, value in values.items():
        assert f':{key}' in str(sql) and params[key] == value
    conn.commit.assert_called_once()


@pytest.mark.parametrize('trip_id', [None, 10])
def test_everyday_reads_use_null_safe_scope(monkeypatch, trip_id):
    manager = MagicMock()
    conn = manager.__enter__.return_value
    conn.execute.return_value.mappings.return_value.all.return_value = []
    conn.execute.return_value.fetchall.return_value = []
    monkeypatch.setattr(ledger_queries, 'get_connection', lambda: manager)
    assert ledger_queries.get_trip_split_entries(trip_id) == []
    assert ledger_queries.get_trip_settlements(trip_id) == []
    for call in conn.execute.call_args_list:
        sql, params = call.args
        assert 'trip_id IS NOT DISTINCT FROM :trip_id' in str(sql)
        assert params == {'trip_id': trip_id}
    conn.execute.side_effect = RuntimeError('offline')
    assert ledger_queries.get_trip_split_entries(trip_id) is None
    assert ledger_queries.get_trip_settlements(trip_id) is None


@pytest.mark.parametrize('bills', [
    [dict(transaction_id='bill', for_id=3, cents=cents)] for cents in (0, -1, True, 1.5, '100', None)
] + [
    [dict(transaction_id='bill', for_id=3, cents=100)] * 2,
    [dict(transaction_id='bill', for_id=3, cents=1001)],
    [dict(transaction_id='bill', for_id=3, cents=600), dict(transaction_id='bill', for_id=4, cents=500)],
])
def test_bill_validation_precedes_connection(monkeypatch, bills):
    """Stored shares must be positive integer cents, unique, and no more than the payback."""
    connect = MagicMock()
    monkeypatch.setattr(settlements, 'get_connection', connect)
    with pytest.raises(ValueError):
        settlements.add_settlement(**write_args(bills=iter(bills)))
    connect.assert_not_called()


@pytest.mark.parametrize('inserted', [True, False])
def test_bill_rows_share_the_settlement_commit_only_on_insert(monkeypatch, inserted):
    """One handover and all its shares are saved atomically; retries attach nothing."""
    manager = MagicMock()
    conn = manager.__enter__.return_value
    conn.execute.return_value.rowcount = int(inserted)
    conn.execute.return_value.scalar_one.return_value = 42
    monkeypatch.setattr(settlements, 'get_connection', lambda: manager)
    bills = [dict(transaction_id='lodging', for_id=3, cents=300),
             dict(transaction_id='lodging', for_id=4, cents=400)]
    assert settlements.add_settlement(**write_args(bills=iter(bills))) is inserted
    assert conn.execute.call_count == (2 if inserted else 1)
    sql, params = conn.execute.call_args_list[0].args
    assert 'RETURNING id' in str(sql) and params['clears_cents'] == 1000
    if inserted:
        sql, params = conn.execute.call_args_list[1].args
        assert 'INSERT INTO settlement_bills' in str(sql)
        assert params == [dict(settlement_id=42, **bill) for bill in bills]
    else:
        conn.execute.return_value.scalar_one.assert_not_called()
    assert [call[0] for call in conn.method_calls] == ['execute'] * (2 if inserted else 1) + ['commit']
    conn.commit.assert_called_once_with()


def test_bill_insert_failure_does_not_commit(monkeypatch):
    """A failed allocation leaves the connection to roll back the handover too."""
    manager = MagicMock()
    conn = manager.__enter__.return_value
    conn.execute.side_effect = [MagicMock(rowcount=1), RuntimeError('offline')]
    monkeypatch.setattr(settlements, 'get_connection', lambda: manager)
    with pytest.raises(RuntimeError):
        settlements.add_settlement(**write_args(bills=[dict(transaction_id='bill', for_id=3, cents=100)]))
    conn.commit.assert_not_called()


def test_trip_paybacks_include_ordered_bill_rows_and_fail_closed(monkeypatch):
    """Both reads share one connection, and a failed bill read is not an empty payback."""
    manager = MagicMock()
    conn = manager.__enter__.return_value
    rows, bills = MagicMock(), MagicMock()
    rows.mappings.return_value.all.return_value = [dict(id=2), dict(id=1)]
    bills.mappings.return_value.all.return_value = [
        dict(settlement_id=1, transaction_id='a', for_id=3, cents=100, payer_id=1),
        dict(settlement_id=1, transaction_id='b', for_id=4, cents=200, payer_id=2),
    ]
    conn.execute.side_effect = [rows, bills]
    connect = MagicMock(return_value=manager)
    monkeypatch.setattr(ledger_queries, 'get_connection', connect)
    assert ledger_queries.get_trip_settlements(7) == [dict(id=2, bills=[]), dict(id=1, bills=[
        dict(transaction_id='a', for_id=3, cents=100, payer_id=1),
        dict(transaction_id='b', for_id=4, cents=200, payer_id=2)])]
    connect.assert_called_once_with()
    sql, params = conn.execute.call_args.args
    assert 'ORDER BY b.settlement_id, b.transaction_id, b.for_participant_id' in str(sql)
    # The bill's payer comes from the real table, so a deleted bill still names it (ADR-0052).
    assert 'JOIN raw_transactions t ON t.transaction_id = b.transaction_id' in str(sql)
    assert 's.trip_id IS NOT DISTINCT FROM :trip_id' in str(sql) and params == {'trip_id': 7}
    conn.execute.side_effect = [rows, RuntimeError('offline')]
    assert ledger_queries.get_trip_settlements(7) is None


@pytest.mark.parametrize('changes', [dict(clears_amount='0'), dict(paid_amount='-1'),
                                     dict(to_id=1), dict(for_id=2), dict(paid_amount='11')])
def test_writer_validates_before_connecting(monkeypatch, changes):
    """Invalid money or people cannot reach a connection, even for a loader."""
    connect = MagicMock()
    monkeypatch.setattr(settlements, 'get_connection', connect)
    with pytest.raises(ValueError):
        settlements.add_settlement(**write_args(**changes))
    connect.assert_not_called()


def test_writer_idempotency_and_errors(monkeypatch):
    """Only an inserted row returns True, while a database failure is raised."""
    manager = MagicMock()
    conn = manager.__enter__.return_value
    monkeypatch.setattr(settlements, 'get_connection', lambda: manager)
    for count in (1, 0):
        conn.execute.return_value.rowcount = count
        assert settlements.add_settlement(**write_args(for_id=3)) is bool(count)
    sql, params = conn.execute.call_args.args
    assert 'ON CONFLICT (idempotency_key) DO NOTHING' in str(sql)
    assert params['clears_cents'] == params['paid_cents'] == 1000
    assert params['for_id'] == 3 and 'for_participant_id' in str(sql)
    assert 'created_at' not in params and 'id' not in params
    conn.execute.side_effect = RuntimeError('offline')
    with pytest.raises(RuntimeError):
        settlements.add_settlement(**write_args())


def test_read_shape_order_and_failure(monkeypatch):
    """The clears side feeds balances; the paid side survives for the report."""
    manager = MagicMock()
    conn = manager.__enter__.return_value
    row = dict(id=1, date=date(2026, 11, 17), from_id=1, to_id=2, for_id=3, cents=1000,
               currency='AUD', paid_cents=900, paid_currency='SGD')
    results = [MagicMock(), MagicMock()]
    results[0].mappings.return_value.all.return_value = [row]
    results[1].mappings.return_value.all.return_value = []
    conn.execute.side_effect = results
    monkeypatch.setattr(ledger_queries, 'get_connection', lambda: manager)
    assert ledger_queries.get_trip_settlements(7) == [dict(row, bills=[])]
    sql, params = conn.execute.call_args_list[0].args
    assert 'for_participant_id AS for_id' in str(sql)
    assert 'ORDER BY date, created_at, id' in str(sql) and params == {'trip_id': 7}
    conn.execute.side_effect = RuntimeError('offline')
    assert ledger_queries.get_trip_settlements(7) is None


async def test_a_debt_inside_a_family_gets_its_own_payback_card(flow):
    """"2. Bob pays Alice" is a line between relatives (ADR-0051): Bob is already the payer,
    and Confirm is offered straight away."""
    update, query = tap('settle:new:7:2:3000', 10)
    await bot.handle_settle_button(update, flow)
    pending = flow.user_data['pending_settlements'][20]
    assert (pending['from_id'], pending['to_id'], pending['debtor_member_ids']) == (2, 1, (2,))
    assert pending['family'] is None
    assert 'Confirm' in _buttons(query.message.reply_text.await_args.kwargs['reply_markup'])


@pytest.mark.parametrize('bills', [
    [dict(transaction_id='dinner', for_id=2, payer_id=2, cents=100)],
    [dict(transaction_id='dinner', for_id=2, cents=100)],
    [dict(transaction_id='taxi', for_id=4, payer_id=3, cents=100)],
    [dict(transaction_id='dinner', for_id=3, payer_id=2, cents=99),
     dict(transaction_id='taxi', for_id=4, payer_id=3, cents=100)],
    [dict(transaction_id='dinner', for_id=3, payer_id=2, cents=1001)],
])
def test_offset_validation_precedes_connection(monkeypatch, bills):
    connect = MagicMock()
    monkeypatch.setattr(settlements, 'get_connection', connect)
    with pytest.raises(ValueError):
        settlements.add_settlement(**write_args(bills=bills))
    connect.assert_not_called()


@pytest.mark.parametrize('explicit_payer', [True, False])
def test_offset_rows_insert_without_payer_metadata(monkeypatch, explicit_payer):
    manager = MagicMock()
    conn = manager.__enter__.return_value
    conn.execute.return_value.rowcount = 1
    conn.execute.return_value.scalar_one.return_value = 42
    monkeypatch.setattr(settlements, 'get_connection', lambda: manager)
    bills = [dict(transaction_id='dinner', for_id=3, cents=1000),
             dict(transaction_id='taxi', for_id=4, payer_id=3, cents=1000),
             dict(transaction_id='lunch', for_id=5, payer_id=4, cents=1000)]
    if explicit_payer:
        bills[0]['payer_id'] = 2
    assert settlements.add_settlement(**write_args(bills=bills))
    sql, params = conn.execute.call_args_list[1].args
    assert 'payer_id' not in str(sql)
    assert params == [dict(settlement_id=42, transaction_id=b['transaction_id'],
                           for_id=b['for_id'], cents=b['cents']) for b in bills]
    conn.commit.assert_called_once()


@pytest.mark.parametrize('shortened', [True, False])
async def test_mark_paid_keeps_parts_and_confirm_uses_snapshot(flow, monkeypatch, shortened):
    from app.services.balances import payment_rows
    entries = [dict(transaction_id='dinner', currency='AUD', paid_by=1, splits={3: 6000})]
    if shortened:
        entries[0]['splits'][2] = 4000
        entries.append(dict(transaction_id='taxi', currency='AUD', paid_by=2, splits={3: 4000}))
    monkeypatch.setattr(bot, 'get_trip_split_entries', MagicMock(return_value=entries))
    amount = 10000 if shortened else 6000
    update, _ = tap(f'settle:new:7:1:{amount}', 10)
    await bot.handle_settle_button(update, flow)
    pending = flow.user_data['pending_settlements'][20]
    parts = pending['line_parts']
    assert bool(parts) is shortened
    # Payer changes and Confirm must use the card, even if the stored bills have changed.
    for name in ('get_trip_split_entries', 'get_trip_settlements', '_load_bals_trip'):
        monkeypatch.setattr(bot, name, MagicMock(side_effect=AssertionError('Do not reload')))
    update, _ = tap('settle:person:3')
    await bot.handle_settle_button(update, flow)
    writer = MagicMock(return_value=True)
    monkeypatch.setattr(bot, 'add_settlement', writer)
    update, _ = tap('settle:confirm')
    await bot.handle_settle_button(update, flow)
    writer.assert_called_once()
    if shortened:
        assert writer.call_args.kwargs['bills'] == payment_rows(parts, 3, amount)
        assert len(writer.call_args.kwargs['bills']) == 3
    else:
        assert 'bills' not in writer.call_args.kwargs


async def test_outside_part_payment_clears_named_member_first(flow, monkeypatch):
    """Dan, outside the Ng family, pays 30 of its shortened 130 line to Alice.

    The card names Bob, the lowest id, for the family. Bob's 30 goes first, as it would on
    a line with no shortcut, even though Carol owes more.
    """
    from app.services.participants import Directory, Participant
    from app.services import trips
    people = (Participant(1, "Alice", str(ALICE), 10, True), Participant(2, "Bob", None, None),
              Participant(3, "Carol", None, None), Participant(4, "Erin", None, None),
              Participant(5, "Dan", None, None))
    flow.user_data['bals_reports'][10] = Directory(people, ())
    trip = trips.Trip(7, "Beach", date(2026, 12, 1), date(2026, 12, 7), member_ids=(1, 2, 3, 4, 5),
                      families=((1, "Tan"), (2, "Ng"), (3, "Ng"), (4, "Lee"), (5, "Ong")))
    monkeypatch.setattr(bot, '_load_bals_trip', MagicMock(return_value=trip))
    # Alice paid dinner for Bob 30, Carol 60 and Erin 40; Erin paid a taxi for Carol 40.
    # The shortcut moves Carol's taxi debt onto her line to Alice: Ngs owe Alice 130.
    entries = [dict(transaction_id='dinner', currency='AUD', paid_by=1, splits={2: 3000, 3: 6000, 4: 4000}),
               dict(transaction_id='taxi', currency='AUD', paid_by=4, splits={3: 4000})]
    monkeypatch.setattr(bot, 'get_trip_split_entries', MagicMock(return_value=entries))
    update, _ = tap('settle:new:7:1:13000', 10)
    await bot.handle_settle_button(update, flow)
    update, _ = tap('settle:person:5')
    await bot.handle_settle_button(update, flow)
    card = flow.user_data['pending_settlements'][20]
    assert card['for_id'] == 2 and card['line_parts']
    # As if Dan had typed 30 as the amount; the reply itself is tested elsewhere.
    card.update(clears_cents=3000, paid_cents=3000)
    writer = MagicMock(return_value=True)
    monkeypatch.setattr(bot, 'add_settlement', writer)
    update, _ = tap('settle:confirm')
    await bot.handle_settle_button(update, flow)
    assert writer.call_args.kwargs['bills'] == [
        dict(transaction_id='dinner', for_id=2, payer_id=1, cents=3000)]


def _answer_fails_if_reply_deleted(message, telegram_bot):
    """Act like Telegram in a group chat: an answer quoting an already-deleted reply fails."""
    async def answer(*args, **kwargs):
        deleted = [c.kwargs['message_id'] for c in telegram_bot.delete_message.await_args_list]
        assert message.message_id not in deleted, 'answered a reply that was already deleted'
        return SimpleNamespace(message_id=31)
    message.reply_text.side_effect = answer


@pytest.mark.parametrize('case', ['refused', 'redraw_fails', 'gone', 'restart'])
async def test_answer_is_sent_before_the_reply_is_deleted(flow, authorized, monkeypatch, case):
    """Every answer to a typed amount goes out before the reply is deleted, so it can quote it."""
    flow.user_data.update(settlement_prompts={30: (20, 'paid')}, settlement_prompt_of_card={20: 30})
    if case != 'gone':
        flow.user_data['pending_settlements'] = {20: card()}
    if case == 'redraw_fails':
        flow.bot.edit_message_text.side_effect = RuntimeError('offline')
    update = _update(ALICE, 'banana' if case == 'refused' else '40')
    update.message.reply_to_message.message_id = 30
    if case == 'restart':
        # Links lost; only the bot's own prompt wording says this is a payback reply.
        flow.user_data.update(settlement_prompts={}, settlement_prompt_of_card={})
        update.message.reply_to_message.text = bot.SETTLE_PROMPTS['paid']
        update.message.reply_to_message.from_user.id = flow.bot.id
    _answer_fails_if_reply_deleted(update.message, flow.bot)
    extract = AsyncMock()
    monkeypatch.setattr(bot, 'process_expense_text', extract)
    await bot.handle_text(update, flow)
    update.message.reply_text.assert_awaited_once()
    assert update.message.message_id in [c.kwargs['message_id'] for c in flow.bot.delete_message.await_args_list]
    extract.assert_not_awaited()


async def test_concurrent_confirms_leave_the_newest_report(flow, monkeypatch):
    """Two cards confirmed at once: redraws run one at a time, so the newest report is kept.

    Without the redraw lock, the first card's slow send would finish last and delete the
    second card's newer report, leaving balances that miss the second payback.
    """
    directory = flow.user_data['bals_reports'][10]
    flow.user_data['pending_settlements'] = {
        i: card(directory=directory, report_message_id=10, idempotency_key=f'key-{i}') for i in (20, 21)}
    writer = MagicMock(return_value=True)
    monkeypatch.setattr(bot, 'add_settlement', writer)
    # Each screen records how many paybacks were saved when its balances were read.
    monkeypatch.setattr(bot, '_bals_screen',
                        lambda trip, snapshot, today: (f'after {writer.call_count} saved', None))
    release, sent = asyncio.Event(), []

    async def send(**kwargs):
        sent.append(kwargs['text'])
        if len(sent) == 1:
            await release.wait()  # the first report is slow to send
        return SimpleNamespace(message_id=39 + len(sent))
    flow.bot.send_message.side_effect = send
    first = asyncio.create_task(bot.handle_settle_button(tap('settle:confirm', 20)[0], flow))
    while not sent:
        await asyncio.sleep(0)
    second = asyncio.create_task(bot.handle_settle_button(tap('settle:confirm', 21)[0], flow))
    for _ in range(20):
        await asyncio.sleep(0)
    assert writer.call_count == 2  # the second payback is saved ...
    assert sent == ['after 1 saved']  # ... but its redraw waits for the first to finish
    release.set()
    await asyncio.gather(first, second)
    assert sent == ['after 1 saved', 'after 2 saved']
    assert flow.user_data['bals_last_redraw'] == {(100, 7): 41}
    assert [c.kwargs['message_id'] for c in flow.bot.delete_message.await_args_list] == [10, 10, 40]
    assert 41 in flow.user_data['bals_reports'] and 40 not in flow.user_data['bals_reports']
