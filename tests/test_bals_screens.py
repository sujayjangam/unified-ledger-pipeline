"""Phone-sized balance screens using only fictional trips and stubbed reads."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.services.tg_html import to_plain, visible_len

import pytest

import app.bot_core as bot
from app.add_expense import format_money
from app.services import balances
from app.services.participants import Directory, Participant
from app.services.trips import Trip
from test_bot_core import ALICE, _context, _update
from test_trip_bals import _buttons, _tap

DAY = date(2026, 11, 21)
NAMES = dict(enumerate(('Alice', 'Bob', 'Carol', 'Dave', 'Erin', 'Frank', 'Gina', 'Hugo', 'Ian', 'Jo'), 1))
FAMILIES = {1: 'Reds', 2: 'Reds', 3: 'Blues', 4: 'Blues', 5: 'Greens',
            6: 'Greens', 7: 'Golds', 8: 'Golds', 9: 'Reds', 10: 'Blues'}
TRIP = Trip(7, 'Island trip', DAY, DAY, tuple(NAMES), tuple(FAMILIES.items()))
DIRECTORY = Directory(tuple(Participant(i, name, str(ALICE) if i == 1 else None, None)
                            for i, name in NAMES.items()), ())


def bill(key, payer, splits, currency='IDR', description='Airbnb 2300 on my fictional card'):
    """Give each share a bill label and date without reading any stored data."""
    return dict(transaction_id=key, paid_by=payer, splits=splits, currency=currency,
                description=description, date=DAY)


def payback(giver, receiver, cents, currency='IDR', **changes):
    """Build the query's shape: currency is the aliased clears_currency."""
    return dict(from_id=giver, to_id=receiver, cents=cents, currency=currency, date=DAY, **changes)


@pytest.fixture
def island(monkeypatch):
    """Ten people, four families, three currencies and four kinds of payback."""
    entries = [
        bill('lodging', 1, {2: 10000, 3: 40000, 4: 30000, 5: 15000, 6: 15000, 7: 8000, 9: 9000, 10: 7000}),
        bill('boat', 8, {3: 2000, 5: 5000}),
        bill('dinner', 1, {2: 4000, 5: 6000}, 'SGD'),
        bill('taxi', 2, {5: 4000}, 'SGD'),
        bill('museum', 7, {9: 5000}, 'AUD'),
    ]
    paid = [
        payback(3, 1, 30000, for_id=5, bills=[
            dict(transaction_id='lodging', for_id=5, cents=15000),
            dict(transaction_id='lodging', for_id=6, cents=15000)]),
        payback(4, 1, 5000),  # No bill rows: Dave clears his own debt first.
        payback(6, 1, 10000, 'SGD', for_id=5, bills=[
            dict(transaction_id='dinner', for_id=2, cents=4000),
            dict(transaction_id='dinner', for_id=5, cents=6000),
            dict(transaction_id='taxi', for_id=5, cents=4000)]),
        payback(8, 7, 1200, 'AUD', bills=[
            dict(transaction_id='deleted', for_id=9, cents=1200, payer_id=7)]),
    ]
    monkeypatch.setattr(bot, 'authorize', AsyncMock(return_value=DIRECTORY))
    monkeypatch.setattr(bot, '_load_bals_trip', MagicMock(return_value=TRIP))
    monkeypatch.setattr(bot, 'get_trip_split_entries', MagicMock(return_value=entries))
    monkeypatch.setattr(bot, 'get_trip_settlements', MagicMock(return_value=paid))
    return entries, paid


def test_three_currency_main_and_navigation(island):
    text, markup = bot._bals_screen(TRIP, DIRECTORY, DAY)
    assert to_plain(text).splitlines() == [
        '🧳 Island trip · 21–21 Nov', 'IDR · 2 of 3', '',
        'Blues family owes IDR 740.00', 'Golds family owes IDR 10.00',
        'Greens family owes IDR 50.00', 'Reds family is owed IDR 800.00',
        '', '6 payments to settle · 2 paybacks']
    assert _buttons(markup) == {
        'AUD (2)': 'bals:cur:7:AUD', '✓ IDR (6)': 'bals:cur:7:IDR',
        'SGD (0)': 'bals:cur:7:SGD', 'All payments (6)': 'bals:pay:7:IDR',
        'View paybacks (2)': 'bals:pbk:7:IDR', 'Other trip': 'bals:page:0',
        '💱 All in SGD': 'bals:sgd:7'}
    assert all(not line.startswith('    ') for line in text.splitlines())
    text, markup = bot._bals_screen(TRIP, DIRECTORY, DAY, 'SGD')
    assert 'Everyone is settled. · 1 payback' in text
    assert 'All payments' not in str(_buttons(markup))
    assert '✓ SGD (0)' in _buttons(markup)


def test_default_currency_ties_and_all_settled():
    p = balances.Payment(('person', 1), ('person', 2), 100)
    assert balances.default_balance_currency(['SGD', 'AUD'], [('SGD', p), ('AUD', p)]) == 'AUD'
    assert balances.default_balance_currency(['SGD', 'AUD'], []) == 'AUD'
    assert balances.default_balance_currency([], []) is None


def test_currency_buttons_wrap_at_four(island):
    entries, _ = island
    entries.extend([bill(c, 1, {3: 1}, c) for c in ('EUR', 'GBP', 'USD')])
    _, markup = bot._bals_screen(TRIP, DIRECTORY, DAY)
    assert [len(row) for row in markup.inline_keyboard[:2]] == [4, 2]
    assert all(b.callback_data.startswith('bals:cur:7:') for row in markup.inline_keyboard[:2] for b in row)


@pytest.mark.parametrize('currency', ['AUD', 'IDR', 'SGD'])
def test_payment_callbacks_keep_global_numbers(island, currency):
    entries, paid = island
    text, markup = bot._bals_screen(TRIP, DIRECTORY, DAY, currency, 'pay')
    expected = [f'settle:new:7:{n}:{p.cents}'
                for n, (c, p) in enumerate(balances.trip_payments(entries, FAMILIES, paid), 1)
                if c == currency]
    assert list(_buttons(markup).values()) == expected + [f'bals:cur:7:{currency}']
    assert to_plain(text).startswith(f'🧳 Island trip · {currency} · to settle')
    assert not any(line[:1].isdigit() for line in text.splitlines())


def test_labels_shorten_names_keep_amount_and_plain_characters():
    p = balances.Payment(('family', 'A_' * 80), ('person', 1), 127980000)
    names = {1: 'Leo * < > & ' * 30}
    label = balances.payment_button_label(p, 'IDR', names)
    assert len(label) <= 64 and label.count('…') == 2
    assert label.endswith(format_money(p.cents, 'IDR'))
    assert all(char in label for char in '_*<>&')
    assert balances.payment_button_label(
        balances.Payment(('person', 1), ('person', 2), 100), 'AUD',
        {1: 'A_*<>&', 2: 'B_*<>&'}) == '✅ A_*<>& → B_*<>& · AUD 1.00'


def test_cut_payments_offer_only_whole_visible_lines(island, monkeypatch):
    text, _ = bot._bals_screen(TRIP, DIRECTORY, DAY, 'IDR', 'pay')
    first_two = text.splitlines()[:3]
    limit = visible_len('\n'.join(first_two + [bot.BALS_CUT_SHORT]))
    monkeypatch.setattr(bot, 'TELEGRAM_MESSAGE_LIMIT', limit)
    cut, markup = bot._bals_screen(TRIP, DIRECTORY, DAY, 'IDR', 'pay')
    assert cut.splitlines() == first_two + [bot.BALS_CUT_SHORT]
    assert visible_len(cut) <= limit
    assert len(markup.inline_keyboard) == 3  # Two visible payments and Back.
    expected = balances.trip_payments(*[island[0], FAMILIES, island[1]])
    callbacks = list(_buttons(markup).values())[:-1]
    assert callbacks == [f'settle:new:7:{n}:{p.cents}'
                         for n, (c, p) in enumerate(expected, 1) if c == 'IDR'][:2]


def test_paybacks_show_actual_shares_offsets_deleted_and_old(island):
    entries, paid = island
    before = deepcopy((entries, paid))
    lines = balances.trip_payback_lines(entries, NAMES, FAMILIES, 'IDR', paid)
    assert lines == [
        '• Carol paid Alice IDR 300.00 on behalf of Greens family, 21 Nov',
        '    Erin\'s share of "Airbnb 2300 on my…" (21 Nov): IDR 150.00',
        '    Frank\'s share of "Airbnb 2300 on my…" (21 Nov): IDR 150.00',
        '• Dave paid Alice IDR 50.00, 21 Nov',
        '    Dave: IDR 50.00 (no bill record)', '',
        "Paid on someone else's behalf:", '• Carol paid IDR 300.00 for Greens family',
        '(Not in the balances: settle these between you.)']
    lines = balances.trip_payback_lines(entries, NAMES, FAMILIES, 'SGD', paid)
    assert '    Bob\'s share of "Airbnb 2300 on my…" (21 Nov): SGD 40.00' in lines
    # Frank pays for Erin, in his own family. Bob's dinner share is cleared, but Erin's taxi
    # share owed to Bob is offset by the same 40.00 (a shortened line): Bob gains nothing
    # overall, so nobody outside Frank's family was paid for.
    assert "Paid on someone else's behalf:" not in lines
    lines = balances.trip_payback_lines(entries, NAMES, FAMILIES, 'AUD', paid)
    assert '    Ian\'s share of "a deleted bill": nothing cleared' in lines
    assert "Paid on someone else's behalf:" not in lines
    assert (entries, paid) == before


def test_history_observes_caps_and_rejected_offsets_without_changing_maths():
    entries = [bill('dinner', 1, {2: 4000, 5: 6000}), bill('taxi', 2, {5: 4000})]
    paid = [payback(6, 1, 11000, bills=[
        dict(transaction_id='dinner', for_id=5, cents=7000),
        dict(transaction_id='dinner', for_id=2, cents=4000),
        dict(transaction_id='taxi', for_id=5, cents=4001)])]
    history = []
    before = balances._fold_debts(entries, {}, paid)
    assert balances._fold_debts(entries, {}, paid, history) == before
    assert history == [((6000, 0, 0), {5: 6000})]
    lines = balances.trip_payback_lines(entries, NAMES, FAMILIES, 'IDR', paid)
    assert sum('nothing cleared' in line for line in lines) == 2
    assert any(line.endswith('IDR 60.00') for line in lines)
    # Gross shares can clear even when opposite debts already netted the line away.
    opposite = [bill('one', 1, {3: 100}), bill('two', 3, {1: 100})]
    history = []
    balances._fold_debts(opposite, {}, [payback(5, 1, 100, bills=[
        dict(transaction_id='one', for_id=3, cents=100)])], history)
    assert history == [((100,), {})]


def test_offset_counts_against_the_middle_only_up_to_its_size():
    """Frank clears Bob's 40.00 dinner share but offsets only 20.00 of Erin's taxi debt to
    Bob: Bob's family was paid for by the 20.00 left over, and Erin (Frank's own family) is
    never listed."""
    entries = [bill('dinner', 1, {2: 4000, 5: 6000}), bill('taxi', 2, {5: 4000})]
    # A shortened line's rows: Frank hands Alice 40.00 for Bob's dinner (a direct row), and
    # Erin owes Bob 20.00 less (an offset row on Bob's taxi).
    paid = [payback(6, 1, 4000, bills=[
        dict(transaction_id='dinner', for_id=2, cents=4000),
        dict(transaction_id='taxi', for_id=5, cents=2000, payer_id=2)])]
    lines = balances.trip_payback_lines(entries, NAMES, FAMILIES, 'IDR', paid)
    assert '• Frank paid IDR 20.00 for Reds family' in lines
    assert not any('for Greens family' in line for line in lines)


def test_behalf_counts_only_what_was_really_cleared():
    """Dave owes Alice 100.00, but Alice owes Dave 50.00 on another bill, so the debt is 50.00.
    Gina pays Alice 100.00 for Dave's share: the share shows as paid, but only 50.00 helped
    Dave; the other 50.00 is owed back to Gina, so it is not "paid for" anyone."""
    entries = [bill('one', 1, {4: 10000}), bill('two', 4, {1: 5000})]
    paid = [payback(7, 1, 10000, bills=[dict(transaction_id='one', for_id=4, cents=10000)])]
    lines = balances.trip_payback_lines(entries, NAMES, FAMILIES, 'IDR', paid)
    assert '    Dave\'s share of "Airbnb 2300 on my…" (21 Nov): IDR 100.00' in lines
    assert '• Gina paid IDR 50.00 for Blues family' in lines


def test_old_payback_nothing_cleared_and_same_family_excluded():
    entries = [bill('one', 1, {5: 100})]
    paid = [payback(6, 1, 150), payback(6, 1, 50)]
    lines = balances.trip_payback_lines(entries, NAMES, FAMILIES, 'IDR', paid)
    assert '    Erin: IDR 1.00 (no bill record)' in lines
    assert '    (no bill record)' in lines
    assert "Paid on someone else's behalf:" not in lines


@pytest.mark.parametrize('data', [
    'bals:cur:7', 'bals:pay:7:IDR:extra', 'bals:pbk:7:idr', 'bals:cur:7:US',
    'bals:cur:7:IDR\n', 'bals:show:+7', 'bals:page: 0', 'bals:pay:٧:IDR',
    'bals:cur:７:IDR', 'bals:show:9223372036854775808', 'bals:pay:-1:IDR',
    'other:cur:7:IDR', 'bals:cur:7:1DR', 'bals:show:7\n',
])
async def test_strict_callback_validation(island, data):
    update, query = _tap(data)
    await bot.handle_bals_button(update, _context())
    query.answer.assert_awaited_once_with(bot.BALS_ERROR)
    query.edit_message_text.assert_not_awaited()
    bot._load_bals_trip.assert_not_called()
    bot.get_trip_split_entries.assert_not_called()


@pytest.mark.parametrize('action', ['show', 'cur', 'pay', 'pbk'])
async def test_old_show_and_stale_currency_return_main_and_register_snapshot(island, action):
    data = 'bals:show:7' if action == 'show' else f'bals:{action}:7:ZZZ'
    update, query = _tap(data)
    context = _context()
    await bot.handle_bals_button(update, context)
    query.answer.assert_awaited_once_with(None)
    bot._load_bals_trip.assert_called_once_with(1, 7)
    reply = query.edit_message_text.await_args
    assert reply.args[0].splitlines()[1] == 'IDR · 2 of 3'
    assert reply.kwargs["parse_mode"] == "HTML"
    assert context.user_data['bals_reports'][query.message.message_id] is DIRECTORY


@pytest.mark.parametrize('action', ['cur', 'pay', 'pbk'])
async def test_valid_taps_read_fresh_answer_then_edit(island, action):
    update, query = _tap(f'bals:{action}:7:AUD')
    order = []
    query.answer.side_effect = lambda *a: order.append('answer')
    query.edit_message_text.side_effect = lambda *a, **kw: order.append('edit')
    await bot.handle_bals_button(update, _context())
    assert order == ['answer', 'edit']
    bot.get_trip_split_entries.assert_called_once_with(7)
    bot.get_trip_settlements.assert_called_once_with(7)
    assert query.edit_message_text.await_args.kwargs["parse_mode"] == "HTML"


async def test_fresh_report_opens_saved_currency_instead_of_busiest(island):
    context = _context()
    context.bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=500))
    await bot._send_fresh_balances(context, 99, 1, 7, DIRECTORY, 'AUD')
    kwargs = context.bot.send_message.await_args.kwargs
    assert kwargs['text'].splitlines()[1] == 'AUD · 1 of 3'
    assert '✓ AUD (2)' in _buttons(kwargs['reply_markup'])
    assert context.user_data['bals_reports'][500] is DIRECTORY


def test_ten_people_six_bills_two_paybacks_reduces_baseline(island, monkeypatch):
    """Compare the agreed #148 baseline (45 lines, 13 buttons) with the compact main."""
    entries = [bill(f'bill-{n}', payer, dict.fromkeys(NAMES, 10000 + n * 100))
               for n, payer in enumerate((1, 2, 3, 5, 7, 8))]
    paid = [payback(4, 1, 1000), payback(6, 3, 2000)]
    monkeypatch.setattr(bot, 'get_trip_split_entries', lambda _: entries)
    monkeypatch.setattr(bot, 'get_trip_settlements', lambda _: paid)
    text, markup = bot._bals_screen(TRIP, DIRECTORY, DAY)
    counts = len(text.splitlines()), sum(map(len, markup.inline_keyboard))
    assert counts == (9, 4)
    assert counts[0] < 45 and counts[1] < 13


async def test_command_default_currency(island):
    update = _update(ALICE)
    await bot.trip_bals_command(update, _context())
    assert update.effective_message.reply_text.await_args.args[0].splitlines()[1] == 'IDR · 2 of 3'


@pytest.mark.parametrize('screen', ['cur', 'pay', 'pbk'])
def test_every_screen_cuts_on_whole_lines(island, monkeypatch, screen):
    full, _ = bot._bals_screen(TRIP, DIRECTORY, DAY, 'IDR', screen)
    monkeypatch.setattr(bot, 'TELEGRAM_MESSAGE_LIMIT', 110)
    cut, _ = bot._bals_screen(TRIP, DIRECTORY, DAY, 'IDR', screen)
    assert visible_len(cut) <= 110 and cut.endswith(bot.BALS_CUT_SHORT)
    assert cut.splitlines()[:-1] == full.splitlines()[:len(cut.splitlines()) - 1]


def test_bill_labels_and_no_split_warning():
    assert balances._bill_label({'description': 'one two three four five six seven'}) == 'one two three four five…'
    assert balances._bill_label({'description': 'abcdefghijklmnopqrstuvwx y'}) == 'abcdefghijklmnopqrstuvwx…'
    assert balances.trip_balance_lines([], {}, {}) == [balances.NO_SPLITS_LINE]
    lines = balances.trip_balance_lines([bill('orphan', None, {3: 1}, 'AUD'),
                                        bill('valid', 1, {3: 1}, 'IDR')], NAMES, FAMILIES, currency='IDR')
    assert any('no payer recorded' in line for line in lines)
    assert balances.NO_SPLITS_LINE not in lines


def test_people_without_family_come_after_families(island):
    entries, paid = island
    families = {pid: family for pid, family in FAMILIES.items() if pid not in (9, 10)}
    lines = balances.trip_balance_lines(entries, NAMES, families, paid, 'IDR')
    assert lines.index('Ian owes IDR 90.00') > max(i for i, line in enumerate(lines) if 'family' in line)
    assert lines.index('Jo owes IDR 70.00') > lines.index('Ian owes IDR 90.00')


def test_long_special_names_stay_plain_on_screen(island):
    names = {1: 'Alice_*<>&' * 20, 3: 'Carol_*<>&' * 20}
    directory = replace(DIRECTORY, participants=tuple(
        replace(p, display_name=names.get(p.id, p.display_name)) for p in DIRECTORY.participants))
    text, markup = bot._bals_screen(TRIP, directory, DAY, 'IDR', 'pay')
    assert names[1] in to_plain(text)
    assert all(len(b.text) <= 64 for row in markup.inline_keyboard for b in row)


def test_paybacks_read_a_bill_date_stored_as_text(island):
    """transactions.date is TEXT (YYYY-MM-DD) in production, unlike the fixtures' dates, so
    a payback naming its bills crashed "View paybacks" (#168). Both shapes
    now read as "21 Nov", and text that isn't a date is shown as it is."""
    entries, paid = island
    for e in entries:
        e['date'] = DAY.isoformat()
    text, _ = bot._bals_screen(TRIP, DIRECTORY, DAY, 'IDR', 'pbk')
    assert 'Erin\'s share of "Airbnb 2300 on my…" (21 Nov): IDR 150.00' in to_plain(text).splitlines()
    entries[0]['date'] = 'sometime'
    lines = balances.trip_payback_lines(entries, NAMES, FAMILIES, 'IDR', paid)
    assert any(line.endswith('(sometime): IDR 150.00') for line in lines)


async def test_a_failed_error_toast_is_only_printed(island):
    """A slow read can outlast Telegram's answer window; the error toast failing then must
    not escape the handler as an unhandled traceback."""
    update, query = _tap('bals:pbk:7:IDR')
    bot.get_trip_split_entries.side_effect = RuntimeError('boom')
    query.answer.side_effect = RuntimeError('Query is too old')
    await bot.handle_bals_button(update, _context())
    query.answer.assert_awaited_once_with(bot.BALS_ERROR)
    query.edit_message_text.assert_not_awaited()
