"""Issue 160 wording and family defaults, using fictional ledger snapshots only."""
from dataclasses import replace

import pytest

from app.services.settle import (
    PAYMENT_METHODS, SettleChoices, SettleRefused, is_settle, match_bills, normalise,
    read_settle, settle_up,
)
from app.services.settle_card import build_settle_card, settle_card_state
from test_settle import FAMILIES, NAMES, PEOPLE, bill, read, settle
from test_settle_card import buttons, card


@pytest.mark.parametrize('payment', [
    '500000 idr in cash', 'cash 500000 idr', '500000 idr to Laura in cash',
    '500000 idr by PayNow', '500000 idr via bank transfer',
])
def test_reported_payment_wordings(payment):
    """Method decoration never prevents the intended white-rock allocation."""
    people = (*PEOPLE, replace(PEOPLE[0], id=7, display_name='Laura'))
    reading = read_settle(f'Settle Dave paid {payment} for white rock', people, PEOPLE[0], FAMILIES)
    assert (reading.giver_id, reading.currency, reading.cents) == (4, 'IDR', 50000000)
    receiver = 7 if 'Laura' in payment else 1
    result = settle_up(reading, [bill('rock', 'White rock', payer=receiver, currency='IDR',
                                    shares={4: 50000000})], trip_id=10, names={**NAMES, 7: 'Laura'})
    assert result.parts[0].ticked == ('rock',)
    assert result.rows[0].for_id == 4 and result.clears_all


@pytest.mark.parametrize('method', PAYMENT_METHODS)
@pytest.mark.parametrize('lead', ['', 'in ', 'by ', 'via ', 'with ', 'through ', 'using ', 'over '])
def test_listed_methods_any_case(method, lead):
    """The fixed whole-word allowlist works on either side of money and receivers."""
    phrase = (lead + method).upper()
    for payment in (f'{phrase} 50', f'50 {phrase}', f'50 to Alice {phrase}'):
        reading = read(f'Settle Carol paid {payment} for dinner')
        assert reading.cents == 5000 and reading.parts[0].words == ('dinner',)
    if lead:
        assert read(f'Settle Carol paid 50 for dinner {phrase}').parts[0].words == ('dinner',)


def test_unknown_methods_bare_bill_words_and_person_collisions():
    """Refuse unknown payment words, retain bill cash and never erase a person's name."""
    for payment in ('500 by cheque', '500 by cashless'):
        with pytest.raises(SettleRefused):
            read(f'Settle Carol paid {payment}')
    result = settle('Settle Carol paid 50 for dinner cash', [bill('d', 'Dinner 480 cash')])
    assert result.parts[0].kind == 'full' and result.parts[0].ticked == ('d',)
    cash = replace(PEOPLE[1], display_name='Cash')
    people = (*PEOPLE[:1], cash, *PEOPLE[2:])
    for text in ('Settle Carol paid Cash 50', 'Settle Carol paid 50 to Cash'):
        assert read_settle(text, people, PEOPLE[0], FAMILIES).receiver_id == cash.id
    assert read_settle('Settle Cash paid 50', people, PEOPLE[0], FAMILIES).giver_id == cash.id
    assert read_settle('Settle Carol paid 50 for Cash', people, PEOPLE[0], FAMILIES).parts[0].people == (2,)
    with pytest.raises(SettleRefused):
        read_settle('Settle Carol paid 50 in cash', people, PEOPLE[0], FAMILIES)


def test_most_words_ties_fillers_and_oldest_choice():
    """Suggest the strongest overlap, preserving oldest order and explicit fallback."""
    bills = [bill('one', 'Rock'), bill('two', 'White rock'), bill('tie', 'White rock dinner')]
    result = settle('Settle Carol paid 50 for white rock bill that overran', bills)
    assert result.parts[0].kind == 'close'
    assert result.parts[0].matched == ('two', 'tie')
    assert result.parts[0].ticked == ('two',)
    assert 'Did you mean this bill?' in result.parts[0].warning
    single = settle('Settle Carol paid 50 for white rock overran', bills[:2])
    assert single.parts[0].matched == ('two',)
    oldest = settle('Settle Carol paid 50 for white rock overran', bills,
                    choices=SettleChoices(oldest_first=frozenset({0})))
    assert oldest.rows[0].transaction_id == 'one'
    none = settle('Settle Carol paid 50 for hotel', bills)
    assert none.parts[0].kind == 'none' and 'oldest bills' in none.parts[0].warning
    assert normalise('bill that which this was is') == ()
    assert match_bills(('hotel',), [bill(description='Bill that was rock')], 1, (3,)).kind == 'none'
    # Amount-backed close still wins over a stronger uncorroborated overlap.
    backed = [bill('one', 'Rock', shares={3: 5000}), *bills[1:]]
    assert settle('Settle Carol paid 50 for white rock overran', backed).parts[0].matched == ('one',)


@pytest.mark.parametrize('phrase,people', [
    ('himself and Erin', (4, 5)), ('herself', (4,)), ('themselves', (4,)), ('myself', (1,)),
])
def test_reflexives_keep_allocation_order(phrase, people):
    """Giver pronouns and sender pronouns consume shares in the spoken order."""
    reading = read(f'Settle Dave paid 150 for {phrase}')
    assert reading.parts[0].people == people and not reading.parts[0].words
    result = settle_up(reading, [bill(payer=2, shares={1: 10000, 4: 10000, 5: 10000})],
                       trip_id=10, names=NAMES)
    assert tuple(r.for_id for r in result.rows) == people
    assert result.rows[0].cents == 10000


def family_card(text='Family settle Carol paid 500 for white rock', **kwargs):
    """A two-family trip with a bill that supports switching the giver."""
    return card(text, [bill('rock', 'White rock', shares={3: 25000, 4: 25000, 5: 25000, 6: 25000})],
                scope_families={10: FAMILIES}, **kwargs)


def test_family_reading_default_toggle_and_named_people():
    """Family is per payment; explicit names and manual share selections win."""
    assert is_settle(' FaMiLy SeTtLe Carol paid 50')
    assert not is_settle('family settlement Carol paid 50')
    value = family_card()
    assert value['reading'].family and value['result'].clears_all
    assert tuple(r.for_id for r in value['result'].rows) == (3, 4)
    text, rows = build_settle_card(value)
    assert text.startswith('👪 Family settle') and "paid on Dave's behalf" not in text
    assert buttons(rows)["👤 Only Carol's shares"] == 'su:family'
    assert all(len(data.encode()) < 64 for data in buttons(rows).values())
    single = settle_card_state({**value, 'choices': SettleChoices(family=False)})
    text, rows = build_settle_card(single)
    assert text.startswith('🤝 Settle up') and 'more than the shares named' in text
    assert tuple(r.for_id for r in single['result'].rows) == (3,)
    assert buttons(rows)['👪 Whole family'] == 'su:family'
    assert settle_card_state({**single, 'choices': SettleChoices(family=True)})['result'] == value['result']
    for phrase, expected in [('himself', (3,)), ('Dave', (4,)), ('Erin', (5,))]:
        named = family_card(f'Family settle Carol paid 500 for {phrase} for white rock')
        assert tuple(r.for_id for r in named['result'].rows) == expected
        if phrase == 'Erin':
            assert "paid on Erin's behalf" in build_settle_card(named)[0]
    manual = family_card(choices=SettleChoices(family=False, shares=(('rock', 4),)))
    assert tuple(r.for_id for r in manual['result'].rows) == (4,)


def test_family_scope_giver_and_toggle_presence():
    """The active or inferred trip owns its families; everyday has none."""
    changed = family_card(choices=SettleChoices(giver_id=5))
    assert tuple(r.for_id for r in changed['result'].rows) == (5, 6)
    inferred = family_card(trip_id=None)
    assert inferred['result'].trip_id == 10
    assert tuple(r.for_id for r in inferred['result'].rows) == (3, 4)
    plain = family_card('Settle Carol paid 500 for white rock')
    assert '👪 Whole family' in buttons(build_settle_card(plain)[1])
    solo = card('Family settle Carol paid 500', [bill()], scope_families={10: {3: 'Lee'}})
    assert 'su:family' not in buttons(build_settle_card(solo)[1]).values()
    everyday = card('Family settle Carol paid 500', [bill(trip=None)], trip_id=None,
                    scope_families={10: FAMILIES})
    text, rows = build_settle_card(everyday)
    assert "⚠️ Families are only on a trip: only Carol's shares." in text
    assert text.startswith('🤝 Settle up') and 'su:family' not in buttons(rows).values()
    assert tuple(r.for_id for r in everyday['result'].rows) == (3,)


def test_everyday_only_close_match_still_blocks_trip_allocation():
    """New word suggestions keep the everyday-only guard and its explicit switch."""
    bills = [bill('trip', 'Taxi'), bill('home', 'White rock', trip=None)]
    result = settle('Settle Carol paid 50 for white rock overran', bills)
    assert result.parts[0].kind == 'everyday_only' and not result.can_confirm
    result = settle('Settle Carol paid 50 for white rock overran', bills,
                    choices=SettleChoices(everyday=True))
    assert result.parts[0].kind == 'close' and result.rows[0].transaction_id == 'home'


def test_inferred_family_scope_when_only_a_relative_owes():
    """Trip mode off still discovers a bill that only the giver's family owes."""
    value = card('Family settle Carol paid 50 for white rock',
                 [bill('rock', 'White rock', shares={4: 5000})], trip_id=None,
                 scope_families={10: FAMILIES})
    assert value['result'].trip_id == 10
    assert tuple(r.for_id for r in value['result'].rows) == (4,)
    # Dave is not Carol's relative on this different trip, and everyday has no families.
    for scope in (11, None):
        with pytest.raises(SettleRefused, match='No match'):
            card('Family settle Carol paid 50 for white rock',
                 [bill('rock', 'White rock', shares={4: 5000}, trip=scope)], trip_id=None,
                 scope_families={10: FAMILIES, 11: {3: 'Lee', 4: 'Ng'}})
