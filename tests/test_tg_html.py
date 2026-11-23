"""HTML boundaries, approved layouts and fallback without Telegram or database I/O."""

import ast
from copy import deepcopy
from dataclasses import replace
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest, TimedOut

import app.bot_core as bot
from app.services import balances
from app.services.one_currency import one_currency
from app.services.settle_card import build_settle_card
from app.services.settlements import settlement_card_text
from app.services.tg_html import esc, b, i, quote, fold, to_plain, visible_len
from app.services.trips import Trip
from test_recent import _entry
from test_button_callback import _make_callback, _txn
from test_settle_card import card as settle_card
from test_settlements import card as payback_card


ODD = 'name_ * < > & <b>literal</b> &lt;'
DAY = date(2026, 11, 23)


class Tags(HTMLParser):
    """Check balanced supported tags, including quotes kept whole during truncation."""

    def __init__(self, text):
        super().__init__()
        self.stack = []
        self.feed(text)
        self.close()
        assert not self.stack

    def handle_starttag(self, tag, attrs):
        assert tag in ('b', 'i', 'blockquote')
        assert not attrs or (tag == 'blockquote' and attrs == [('expandable', None)])
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag


@pytest.mark.parametrize('wrap', [esc, b, i, quote, fold])
def test_primitives_round_trip(wrap):
    text = wrap(ODD + '\n"quoted" and café 🧳')
    assert to_plain(text) == ODD + '\n"quoted" and café 🧳'
    assert visible_len(text) == len(ODD + '\n"quoted" and café 🧳')
    assert '&lt;b&gt;literal&lt;/b&gt;' in text
    Tags(text)


def test_plain_strips_tags_before_unescaping_once():
    text = '<b>A &amp; B</b>\n<blockquote expandable>&lt;i&gt; &#65; &amp;lt;</blockquote>'
    assert to_plain(text) == 'A & B\n<i> A &lt;'
    assert visible_len(text) == len('A & B\n<i> A &lt;')


@pytest.mark.parametrize('keyword', [False, True])
async def test_html_retry_preserves_call_routing_and_keyboard(keyword, capsys):
    send = AsyncMock(side_effect=[BadRequest("Can't parse entities: bad tag"), 'sent'])
    text = b(ODD) + '\n' + fold('details & more')
    routing = dict(chat_id=123, message_id=456, reply_markup=object())
    if keyword:
        result = await bot._send_html(send, text=text, **routing)
    else:
        result = await bot._send_html(send, text, **routing)
    assert result == 'sent' and send.await_count == 2
    first, second = send.await_args_list
    assert first.kwargs['parse_mode'] == 'HTML'
    assert 'parse_mode' not in second.kwargs
    assert {k: v for k, v in second.kwargs.items() if k != 'text'} == routing
    assert (second.kwargs['text'] if keyword else second.args[0]) == to_plain(text)
    assert "Can't parse entities" in capsys.readouterr().out


@pytest.mark.parametrize('error', [BadRequest('Message is not modified'), TimedOut('timeout'), ValueError('oops')])
async def test_other_errors_propagate_without_retry(error):
    send = AsyncMock(side_effect=error)
    with pytest.raises(type(error)) as raised:
        await bot._send_html(send, b('hello'))
    assert raised.value is error
    assert send.await_count == 1


async def test_parse_error_retries_only_once():
    error = BadRequest("Can't parse entities")
    send = AsyncMock(side_effect=error)
    with pytest.raises(BadRequest):
        await bot._send_html(send, b('hello'))
    assert send.await_count == 2


async def test_parsing_entities_wording_also_retries():
    send = AsyncMock(side_effect=[BadRequest('Error parsing entities'), None])
    await bot._send_html(send, b('hello'))
    assert send.await_count == 2


async def test_transcript_is_escaped_but_pipeline_receives_raw_text(monkeypatch, directory):
    from test_bot_core import ALICE, _context, _update, _status_msg

    update, context, status = _update(ALICE), _context(), _status_msg()
    update.message.reply_text.return_value = status
    voice = MagicMock(download_to_drive=AsyncMock())
    context.bot.get_file = AsyncMock(return_value=voice)
    monkeypatch.setattr(bot, 'authorize', AsyncMock(return_value=directory))
    monkeypatch.setattr(bot, 'transcribe_audio', AsyncMock(return_value=ODD))
    pipeline = AsyncMock()
    monkeypatch.setattr(bot, 'process_expense_text', pipeline)
    await bot.handle_voice(update, context)
    assert status.edit_text.await_args.args[0] == f'📝 <b>Transcript:</b> {esc(ODD)}\n\n🧠 Extracting data...'
    assert status.edit_text.await_args.kwargs['parse_mode'] == 'HTML'
    assert pipeline.await_args.args[2] == ODD


def test_cut_counts_visible_characters_and_keeps_fold_whole(monkeypatch):
    lines = [b('&' * 20), fold('a' * 20 + '\n' + '<' * 20), i('tail' * 30)]
    budget = visible_len('\n'.join(lines[:2] + [bot.BALS_CUT_SHORT]))
    monkeypatch.setattr(bot, 'TELEGRAM_MESSAGE_LIMIT', budget)
    cut, kept = bot._cut_bals_lines(lines)
    assert kept == 2 and cut == lines[:2] + [bot.BALS_CUT_SHORT]
    assert visible_len('\n'.join(cut)) == budget
    assert len('\n'.join(cut)) > budget
    Tags('\n'.join(cut))
    monkeypatch.setattr(bot, 'TELEGRAM_MESSAGE_LIMIT', budget - 1)
    cut, kept = bot._cut_bals_lines(lines)
    assert kept == 1 and cut == lines[:1] + [bot.BALS_CUT_SHORT]
    Tags('\n'.join(cut))


def test_confirm_exact_approved_screen():
    transaction = dict(transaction_type='Expense', amount='84.50', currency='AUD', category='Food',
                       payment_method='Test Visa', paid_by='Alice', paid_by_participant_id=1,
                       split=[dict(participant_id=n, name=name, share_cents=cents)
                              for n, name, cents in [(1, 'Alice', 2817), (3, 'Carol', 2817), (4, 'Dave', 2816)]],
                       trip_id=7, trip_offer={'name': 'Beach trip'}, date=DAY,
                       description='dinner at cafe nero split with Carol & Dave 84.50 on visa',
                       payer_warning='⚠️ Read as: Alice paid. Check.')
    assert bot._pending_card_text(transaction) == (
        '<b>Please confirm your Expense</b>\n\n💰 Amount: <b>AUD 84.50</b>\n'
        '🏷️ Category: Food\n💳 Account: Test Visa\n👤 Paid by: <b>Alice</b>\n'
        '👥 Split between 3:\n • Alice: 28.17\n • Carol: 28.17\n • Dave: 28.16\n'
        '🧳 Trip: Beach trip\n<blockquote>📝 dinner at cafe nero split with Carol &amp; Dave 84.50 on visa</blockquote>\n'
        '📅 Date: 2026-11-23\n\n<i>⚠️ Read as: Alice paid. Check.</i>')


def test_confirm_escapes_all_values_and_warning_positions():
    transaction = dict(transaction_type=ODD, amount=1, currency=ODD, category=ODD, payment_method=ODD,
                       paid_by=ODD, description=ODD, date=ODD, trip_id=7, trip_offer={'name': ODD},
                       split=[dict(participant_id=3, name=ODD, share_cents=100)],
                       split_warnings=['⚠️ split ' + ODD], date_warning=ODD, payer_warning='⚠️ payer ' + ODD)
    before = deepcopy(transaction)
    text = bot._pending_card_text(transaction)
    assert text.count(esc(ODD)) == 13
    assert text.endswith(i('⚠️ payer ' + ODD))
    assert i('⚠️ split ' + ODD) in text and i('⚠️ ' + ODD) in text
    assert quote('📝 ' + ODD) in text
    assert transaction == before
    Tags(text)


@pytest.mark.parametrize('saved', [True, False])
async def test_saved_and_error_cards_escape_without_changing_storage(monkeypatch, saved):
    transaction = dict(_txn(), description=ODD, category=ODD, payment_method=ODD, paid_by=ODD,
                       trip_id=7, trip_offer={'name': ODD}, payer_warning='⚠️ ' + ODD,
                       trip_warning='⚠️ trip ' + ODD)
    update, context, query = _make_callback()
    bot._remember_pending_card(context, 100, transaction)
    save = MagicMock(return_value=saved)
    monkeypatch.setattr(bot, 'add_expense', save)
    await bot.handle_button_click(update, context)
    text = query.edit_message_text.await_args.args[0]
    assert query.edit_message_text.await_args.kwargs['parse_mode'] == 'HTML'
    assert quote('📝 ' + ODD) in text and '💳 Account: ' + esc(ODD) in text
    assert '👤 Paid by: ' + b(ODD) in text and '🏷️ Category: ' + esc(ODD) in text
    assert save.call_args.kwargs['description'] == ODD
    assert save.call_args.kwargs['category'] == ODD
    if saved:
        assert text.startswith('✅ <b>Saved to Ledger!</b>') and '<i>⚠️' not in text
    else:
        assert text.startswith('❌ <b>Database Error! Could not save:</b>')
        assert text.endswith(i('⚠️ ' + ODD))
    Tags(text)


def test_duplicate_and_closed_card_warnings_are_escaped_italics(directory):
    directory = replace(directory, participants=tuple(replace(p, display_name=ODD) for p in directory.participants))
    duplicate = dict(entered_by_participant_id=2, seconds_ago=10, description=ODD)
    banner = bot._duplicate_banner(duplicate, ODD, 1, directory)
    text = bot._pending_card_text(dict(_txn(), card_banner=banner, closed_account=True))
    assert text.startswith(i('⚠️ Possible duplicate. ' + ODD + ' logged ' + ODD + ' 10 seconds ago.'))
    assert i('📝 Earlier entry: ' + ODD) in text and i(bot.CLOSED_CARD_WARNING) in text
    Tags(text)


@pytest.mark.parametrize('screen', ['cur', 'pay', 'pbk'])
def test_balances_escape_values_and_keep_service_default_plain(monkeypatch, directory, screen):
    names = {1: 'Alice ' + ODD, 3: 'Carol ' + ODD}
    families = {3: 'Family ' + ODD}
    trip = Trip(7, ODD, DAY, DAY, (1, 3), tuple(families.items()))
    directory = replace(directory, participants=tuple(
        replace(p, display_name=names.get(p.id, p.display_name)) for p in directory.participants))
    entries = [dict(transaction_id='bill', currency='AUD', paid_by=1, splits={3: 20000},
                    date=DAY, description='raw_ * < > &')]
    paid = [dict(from_id=3, to_id=1, cents=10000, currency='AUD', date=DAY,
                 bills=[dict(transaction_id='bill', for_id=3, cents=10000)])]
    monkeypatch.setattr(bot, 'get_trip_split_entries', lambda _: entries)
    monkeypatch.setattr(bot, 'get_trip_settlements', lambda _: paid)
    text, keyboard = bot._bals_screen(trip, directory, DAY, 'AUD', screen)
    assert text.startswith('🧳 ' + b(ODD))
    assert esc(ODD) in text and ODD in to_plain(text)
    Tags(text)
    if screen == 'cur':
        assert '<b>AUD 100.00</b>' in text and '<i>1 payment to settle · 1 payback</i>' in text
        plain = balances.trip_balance_lines(entries, names, families, paid, 'AUD')
    elif screen == 'pay':
        assert '<b>AUD 100.00</b>' in text
        plain = balances.trip_payment_lines(entries, names, families, 'AUD', paid)
        assert not any('&amp;' in button.text for row in keyboard.inline_keyboard for button in row)
    else:
        assert '• <b>Carol ' + esc(ODD) + ' paid Alice ' + esc(ODD) + ' AUD 100.00</b>' in text
        assert '<blockquote expandable>' in text and 'raw_ * &lt; &gt; &amp;' in text
        plain = balances.trip_payback_lines(entries, names, families, 'AUD', paid)
    assert ODD in '\n'.join(plain) and '&amp;' not in '\n'.join(plain)


def test_settle_service_html_and_plain_share_words_and_plain_buttons():
    value = settle_card()
    value['names'] = {pid: name + ODD for pid, name in value['names'].items()}
    value['scope_labels'] = {10: ODD}
    value['bills'] = tuple(replace(bill, description=bill.description + ' < > &') for bill in value['bills'])
    before = deepcopy(value)
    plain, buttons = build_settle_card(value)
    text, html_buttons = build_settle_card(value, html=True)
    assert to_plain(text) == plain and html_buttons == buttons and value == before
    assert text.startswith('🤝 <b>Settle up</b> · ' + esc(ODD))
    assert '<i>Clears all in full.</i>' in text
    assert '<b>Carol' in text and 'SGD 381.00</b>' in text
    Tags(text)


def test_payback_service_html_keeps_plain_default():
    value = payback_card(trip_name=ODD, names={1: ODD, 2: ODD, 3: ODD})
    plain = settlement_card_text(value)
    text = settlement_card_text(value, html=True)
    assert to_plain(text) == plain and esc(ODD) in text
    assert '<b>💸 Payback</b>' in text and 'Clears: <b>' in text
    Tags(text)


def test_recent_approved_layout_and_escaped_delete_screens():
    entry = _entry(description=ODD, trip_name=ODD, split_count=3, category=ODD, currency=ODD)
    text, _ = bot._recent_list([entry], False, 0, {2: ODD}, DAY)
    assert text == (f'📊 <b>Recent entries</b> (1–1)\n\n1. {b(ODD + " 46.00")} · 18 Nov 🧳\n'
                    + fold(ODD + '\nLogged by ' + ODD + ' · split 3 ways'))
    for text in (text, bot._recent_confirm(entry, 0, {2: ODD}, DAY)[0], bot._recent_deleted(entry, 0, DAY)[0]):
        assert esc(ODD) in text and '✂️' not in text
        Tags(text)


def test_recent_page_limit_never_splits_fold_or_skips_omitted_entries(monkeypatch):
    entries = [_entry(str(n), description='&' * 400) for n in range(5)]
    text, _ = bot._recent_list(entries, True, 0, {2: ODD}, DAY)
    assert visible_len(text) <= 4096 and text.count('<blockquote expandable>') == 5
    monkeypatch.setattr(bot, 'TELEGRAM_MESSAGE_LIMIT', 800)
    text, keyboard = bot._recent_list(entries, True, 0, {2: ODD}, DAY)
    shown = text.count('<blockquote expandable>')
    assert 0 < shown < 5 and visible_len(text) <= 800
    assert keyboard.inline_keyboard[-1][-1].callback_data == f'recent:page:{shown}'
    assert len(keyboard.inline_keyboard[0]) == shown
    Tags(text)


def test_trip_totals_categories_and_sgd_escape_values(directory):
    trip = Trip(7, ODD, DAY, DAY, (1, 3), ())
    directory = replace(directory, participants=tuple(replace(p, display_name=ODD) for p in directory.participants))
    payment = balances.Payment(('person', 3), ('person', 1), 100)
    lines = one_currency([('SGD', payment)], {}, {})
    data = ([], [], {}, [('SGD', payment)], [])
    screens = [bot._trip_status(trip, DAY), bot.format_period_summary(ODD, [(ODD, 1, 100)], []),
               bot.format_category_summary(ODD, [(ODD, ODD, 1, 100)]),
               bot._sgd_screen(trip, directory, data)[0],
               bot._sgd_card_text(dict(names={1: ODD, 3: ODD}, line=lines[0], from_id=3,
                                       rates={}, debtor_member_ids=(3,)))]
    for text in screens:
        assert esc(ODD) in text and ODD in to_plain(text)
        Tags(text)


def test_only_send_helper_sets_html_parse_mode():
    """Prevent a new direct HTML send from bypassing the plain retry."""
    tree = ast.parse(Path(bot.__file__).read_text(encoding='utf-8'))
    modes = [(node, kw.value.value) for node in ast.walk(tree) if isinstance(node, ast.Call)
             for kw in node.keywords if kw.arg == 'parse_mode' and isinstance(kw.value, ast.Constant)]
    assert len(modes) == 1 and modes[0][1] == 'HTML'
    helper = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == '_send_html')
    assert modes[0][0] in list(ast.walk(helper))
