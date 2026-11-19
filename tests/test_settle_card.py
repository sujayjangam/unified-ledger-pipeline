"""Exact settle screens, using only fictional snapshots and whole cents."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.add_expense import format_money
from app.services.settle import SettleChoices, bill_share_options, receiver_options
from app.services.settle_card import build_settle_card, settle_card_state
from test_settle import NAMES, bill, read, settle


def card(text='Settle Carol paid 381 for saree pleating and for Erin airbnb', bills=None, **changes):
    """The documented two-share screen, with a stable index snapshot."""
    value = dict(reading=read(text), bills=tuple(bills if bills is not None else [
        bill(description='Saree pleating'), bill('airbnb', 'Airbnb', shares={5: 26100})]),
        trip_id=10, scope_labels={None: 'Everyday', 10: 'Beach trip'}, names=NAMES,
        people=tuple(NAMES), choices=SettleChoices(), view='card')
    value.update(changes)
    return settle_card_state(value)


def buttons(rows):
    return {label: data for row in rows for label, data in row}


@pytest.mark.parametrize('cents,expected', [(645200, 'INR 6,452.00'), (-645200, 'INR -6,452.00'),
                                         (-1, 'INR -0.01'), (0, 'INR 0.00')])
def test_money(cents, expected):
    assert format_money(cents, 'INR') == expected


def test_full_screen_is_pure():
    value = card()
    before = deepcopy(value)
    text, rows = build_settle_card(value)
    assert text == ("🤝 Settle up · Beach trip\nCarol pays Alice SGD 381.00\n"
                    " • Saree pleating: Carol's share SGD 120.00, cleared\n"
                    " • Airbnb: Erin's share SGD 261.00, cleared (paid on Erin's behalf)\nClears all in full.")
    assert list(buttons(rows)) == ['✅ Confirm', '❌ Cancel', 'Change bills', 'Change who paid', 'Change receiver']
    assert value == before


def test_short_and_zero_allocations_remain_visible():
    value = card(bills=[bill(description='Saree pleating'), bill('airbnb', 'Airbnb', shares={5: 28000})])
    text, _ = build_settle_card(value)
    assert "Airbnb: Erin's share SGD 280.00, SGD 261.00 paid, SGD 19.00 still owed" in text
    assert 'Clears all in full.' not in text
    text, _ = build_settle_card(card('Settle Carol paid 120 for saree and for Erin airbnb'))
    assert 'SGD 0.00 paid, SGD 261.00 still owed' in text


def test_close_full_warning_and_oldest_choices():
    bills = [bill('a', 'Airbnb 2300', shares={3: 26100}),
             replace(bill('b', 'Airbnb 900', shares={3: 26100}), date=date(2026, 11, 14))]
    value = card('Settle Carol paid 261 for airbnb booking', bills)
    text, rows = build_settle_card(value)
    assert "Airbnb 2300 (12 Nov): Carol's share" in text
    assert '❓ Did you mean this bill? 2 bills could fit.' in text
    assert buttons(rows)['✅ Airbnb 2300 · 12 Nov'] == 'su:pick:0'
    assert buttons(rows)['Airbnb 900 · 14 Nov'] == 'su:pick:1'
    assert buttons(rows)['Pay oldest first'] == 'su:oldest:0'
    text, _ = build_settle_card(card('Settle Carol paid 261 for airbnb', bills))
    assert '⚠️ 2 bills matched' in text


def test_everyday_only_and_switch():
    bills = [bill('bed', 'Bed', trip=None, shares={2: 33000})]
    value = card('Settle Bob paid 330 for bed', bills)
    text, rows = build_settle_card(value)
    assert '❓ "bed" matched an everyday bill, not a trip bill.' in text
    assert 'su:confirm' not in buttons(rows).values()
    text, rows = build_settle_card({**value, 'choices': SettleChoices(everyday=True)})
    assert '· Everyday' in text and 'su:confirm' in buttons(rows).values()


def test_receiver_question_and_suggestions():
    bills = [bill('a', shares={4: 150000}), bill('b', payer=2, shares={4: 70000})]
    text, rows = build_settle_card(card('Settle Dave paid 1942', bills))
    assert 'Dave pays SGD 1,942.00. Dave owes Alice and Bob. Who got it?' in text
    assert list(buttons(rows)) == ['Alice SGD 1,500.00', 'Bob SGD 700.00', '❌ Cancel']
    value = card('Settle Carol paid Bob 120', [bill()])
    text, _ = build_settle_card(value)
    assert 'Carol owes Bob nothing on the Beach trip. Did you mean this?' in text
    text, _ = build_settle_card(card('Settle Carol paid Bob 120', [bill(trip=None)], trip_id=None))
    assert 'Carol owes Bob nothing. Did you mean this?' in text
    value = card('Settle Carol paid Alice 45 for dinner', [bill(description='dinner', payer=2, shares={3: 4500})])
    text, rows = build_settle_card(value)
    assert 'The dinner was paid by Bob, not Alice. Did you mean Bob?' in text
    assert list(buttons(rows)) == ['Bob SGD 45.00', '❌ Cancel']
    assert value['result'].question == 'paid_by_other' and not value['result'].rows
    text, rows = build_settle_card({**value, 'choices': SettleChoices(receiver_id=2)})
    assert 'Carol pays Bob' in text and '✅ Confirm' in buttons(rows)


def test_overpayment_and_foreign_screens():
    value = card('Settle Carol paid 160', [bill()])
    text, rows = build_settle_card(value)
    assert '⚠️ SGD 40.00 more than the shares named. Alice will owe it back to Carol.' in text
    assert '⚠️ Confirm overpayment' in buttons(rows)
    value = card('Settle Erin paid 100 SGD for diwali deco', [bill(description='diwali deco', currency='INR', shares={5: 726000})])
    text, rows = build_settle_card(value)
    assert "⚠️ Couldn't get an SGD to INR rate. Reply with how much INR this clears." in text
    assert rows == [[('❌ Cancel', 'su:cancel')]]
    value.update(clears_cents=645200, fx_rate=Decimal('64.52'), fx_rate_date=date(2026, 11, 18), fx_source='frankfurter')
    text, rows = build_settle_card(value)
    assert 'Erin pays Alice SGD 100.00' in text
    assert 'INR 7,260.00, INR 6,452.00 paid, INR 808.00 still owed' in text
    assert 'SGD 100.00 ≈ INR 6,452.00 at the rate of 18 Nov. Check against the statement.' in text
    assert '⚠️ Confirm in SGD' in buttons(rows) and 'Change amount cleared' in buttons(rows)
    value.update(clears_cents=640000, fx_source='typed')
    assert 'You set SGD 100.00 = INR 6,400.00.' in build_settle_card(value)[0]
    value['clears_cents'] = 800000
    assert '⚠️ Confirm overpayment in SGD' in buttons(build_settle_card(value)[1])


def test_change_menus_and_callback_limits():
    value = card()
    for view, title in [('bills', "Which bills does Carol's SGD 381.00 pay?"),
                        ('giver', 'Who handed over the SGD 381.00?'),
                        ('receiver', "Who got Carol's SGD 381.00?")]:
        text, rows = build_settle_card({**value, 'view': view})
        assert text == title and rows[-1] == [('⬅️ Back to card', 'su:back')]
        for data in buttons(rows).values():
            assert len(data.encode()) < 64
            assert not any(name in data for name in NAMES.values())
        if view == 'giver':
            assert 'Alice' not in buttons(rows)
            assert len(rows[0]) == 2


def test_giver_menu_follows_card_scope_not_active_trip():
    people = {10: (1, 2, 3, 4), None: tuple(NAMES)}
    value = card('Settle Bob paid 330 for bed', [bill('bed', 'Bed', trip=None, shares={2: 33000})],
                 choices=SettleChoices(everyday=True), scope_people=people, view='giver')
    assert value['giver_options'] == (2, 3, 4, 5, 6)
    value = card('Settle Carol paid 120 for saree', [bill()], trip_id=None, scope_people=people, view='giver')
    assert value['giver_options'] == (2, 3, 4)


def test_manual_shares_order_and_new_giver():
    bills = [bill('a', shares={3: 12000, 5: 10000}), bill('b', shares={3: 12000, 5: 20000})]
    reading = read('Settle Carol paid 150 for saree and for Erin saree')
    choices = SettleChoices(shares=(('b', 5), ('a', 5), ('a', 3)))
    result = settle('Settle Carol paid 150 for saree and for Erin saree', bills, choices=choices)
    assert [(r.transaction_id, r.for_id, r.cents) for r in result.rows] == [('a', 3, 12000), ('a', 5, 3000)]
    assert result.currency == result.paid_currency == 'SGD'
    assert bill_share_options(reading, bills, result, choices) == (
        ('a', 3, 12000, True), ('a', 5, 10000, True), ('b', 3, 12000, False), ('b', 5, 20000, True))
    assert receiver_options(reading, bills, result, choices) == ((1, 54000),)
    result = settle('Settle Carol paid 150 for saree', bills, choices=SettleChoices(giver_id=5))
    assert result.giver_id == 5 and all(r.for_id == 5 for r in result.rows)


def test_empty_manual_selection_keeps_inferred_trip_and_menu():
    value = card('Settle Carol paid 120 for saree', [bill()], trip_id=None,
                 choices=SettleChoices(shares=()), view='bills')
    assert value['result'].trip_id == 10 and value['result'].currency == 'SGD'
    assert value['share_options'] == (('saree', 3, 12000, False),)
    assert not value['result'].can_confirm
    text, rows = build_settle_card(value)
    assert "Which bills does Carol's SGD 120.00 pay?" == text
    assert 'Saree' not in text and 'su:share:0' in buttons(rows).values()


def test_wrong_receiver_does_not_hide_named_trip_and_mixed_currencies_refuse():
    from app.services.settle import SettleRefused
    value = card('Settle Carol paid Bob 120 for saree', [bill()], trip_id=None)
    assert value['result'].question == 'paid_by_other'
    assert value['result'].trip_id == 10 and value['result'].receiver_options == ((1, 12000),)
    with pytest.raises(SettleRefused, match='Say which'):
        card('Settle Carol paid Bob 120', [bill(), bill('b', currency='INR')])


@pytest.mark.parametrize('outcome,line', [('saved', '✅ Payback saved.'), ('cancelled', '❌ Cancelled.')])
def test_finished(outcome, line):
    text, rows = build_settle_card({**card(), 'outcome': outcome})
    assert text.endswith(line) and rows == []
    assert build_settle_card({**card(), 'saving': True})[1] == [[('⏳ Saving...', 'su:saving')]]



def test_a_card_with_many_long_bills_stays_under_telegrams_limit():
    """Telegram refuses over 4096 characters; the rest are counted, Change bills lists all."""
    long = 'Very long description of a shared dinner at a seaside place ' * 3
    bills = [bill(f'b{i}', f'{long}{i}', shares={3: 1000}) for i in range(120)]
    text, rows = build_settle_card(card('Settle Carol paid 1200', bills))
    assert len(text) < 4096
    assert 'more shares (see Change bills)' in text
    assert 'su:bills' in buttons(rows).values()


def test_change_bills_pages_stay_under_telegrams_button_limit():
    """Telegram allows at most 100 buttons; every share is still reachable by page."""
    bills = [bill(f'b{i}', f'Dinner {i}', shares={3: 1000}) for i in range(120)]
    value = card('Settle Carol paid 1200', bills, view='bills')
    seen = set()
    for page in range(20):
        text, rows = build_settle_card({**value, 'bills_page': page})
        assert sum(len(row) for row in rows) <= 100
        seen |= {data for row in rows for _, data in row if data.startswith('su:share:')}
    assert len(seen) == len(value['share_options'])
    assert '(1/15)' in build_settle_card(value)[0]


def test_a_date_that_is_still_text_is_shown_not_crashed():
    from app.services.settle_card import _date
    assert _date(date(2026, 10, 19)) == '19 Oct' and _date('2026-10-19') == '2026-10-19'


def test_everyday_only_shows_the_named_bills_currency():
    """Found on the test bot: an older USD bill Bob doesn't owe made the SGD dinner show USD."""
    bills = [bill('lamp', 'Lamp', trip=None, currency='USD', shares={3: 5000}),
             bill('dinner', 'Hawker dinner', trip=None, shares={2: 2400})]
    text, _ = build_settle_card(card('Settle Bob paid 24 for hawker dinner', bills))
    assert '❓ "hawker dinner" matched an everyday bill, not a trip bill.' in text
    assert 'SGD 24.00' in text and 'USD' not in text


def test_one_close_match_is_not_counted():
    bills = [bill('airbnb', 'Airbnb on the coast', shares={3: 7550})]
    text, _ = build_settle_card(card('Settle Carol paid 75.50 for airbnb booking', bills))
    assert '❓ Did you mean this bill?\n' in text + '\n' and 'could fit' not in text
