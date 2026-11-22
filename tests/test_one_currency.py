"""Whole-cent conservation and deterministic netting, independent of I/O."""
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.services.balances import LinePart, Payment, payment_rows
from app.services.one_currency import one_currency, take_line_parts


def payment(unit, receiver, cents, currency='SGD'):
    return currency, Payment(unit, ('person', receiver), cents)


def test_conversion_grouping_and_global_number():
    payments = [payment(('family', 'Lee'), 1, 3, 'USD'),
                payment(('family', 'Lee'), 1, 100),
                payment(('family', 'Ng'), 1, 101)]
    lines = one_currency(payments, {1: 'Tan'}, {'USD': Decimal(2)})
    assert [line.sgd_cents for line in lines] == [102, 101]
    assert [p.number for p in lines[0].parts] == [1, 2]
    assert lines[0].parts[0].sgd_cents == 2  # Half up, not bankers' rounding.
    with pytest.raises(FrozenInstanceError):
        lines[0].sgd_cents = 1


def test_same_family_only_exact_reverse():
    payments = [payment(('person', 1), 2, 100), payment(('person', 2), 1, 30, 'USD'),
                payment(('person', 2), 3, 40)]
    lines = one_currency(payments, {1: 'Tan', 2: 'Tan', 3: 'Tan'}, {'USD': Decimal(1)})
    assert [(line.from_unit, line.to_person, line.sgd_cents) for line in lines] == [
        (('person', 1), 2, 70), (('person', 2), 3, 40)]
    assert lines[0].parts[1].reverse


def test_reverse_split_conserves_both_currencies_and_keeps_even_line():
    families = {1: 'Tan', 2: 'Tan', 3: 'Lee'}
    payments = [payment(('family', 'Lee'), 1, 70), payment(('family', 'Lee'), 2, 60),
                payment(('family', 'Tan'), 3, 151, 'USD')]
    lines = one_currency(payments, families, {'USD': Decimal('1.5')})
    assert [(line.to_person, line.sgd_cents) for line in lines] == [(2, 29), (1, 0)]
    reverse = [p for line in lines for p in line.parts if p.reverse]
    assert sum(p.cents_in_currency for p in reverse) == 151
    assert sum(p.sgd_cents for p in reverse) == 101
    assert [(p.cents_in_currency, p.sgd_cents) for p in reverse] == [(46, 31), (105, 70)]
    assert all(p.number == 3 for p in reverse)
    assert lines == one_currency(payments, dict(reversed(list(families.items()))), {'USD': Decimal('1.5')})


def test_equal_totals_keep_original_order_and_exact_zero():
    payments = [payment(('family', 'Tan'), 3, 100), payment(('family', 'Lee'), 1, 100)]
    lines = one_currency(payments, {1: 'Tan', 3: 'Lee'}, {})
    assert len(lines) == 1 and lines[0].sgd_cents == 0
    assert lines[0].from_unit == ('family', 'Tan')
    assert [part.reverse for part in lines[0].parts] == [False, True]


def test_order_ties_follow_payment_numbers_and_sgd_stays_exact():
    payments = [payment(('person', 3), 1, 101), payment(('person', 2), 1, 101)]
    assert [line.from_unit for line in one_currency(payments, {}, {})] == [('person', 3), ('person', 2)]
    assert one_currency([], {}, {}) == ()


def test_missing_or_invalid_rate_is_never_silently_ignored():
    payments = [payment(('person', 2), 1, 100, 'USD')]
    with pytest.raises(KeyError):
        one_currency(payments, {}, {})
    with pytest.raises(ValueError):
        one_currency(payments, {}, {'USD': Decimal(0)})


def test_split_shortcut_paths_are_disjoint_and_conserve_each_hop():
    original = (LinePart(1, 100, ((('a', 1, 3, 80), ('b', 1, 3, 20)),
                                 (('c', 3, 4, 100),))),
                LinePart(2, 50, ((('d', 2, 4, 50),),)))
    first, remaining = take_line_parts(original, 1, 90)
    second, remaining = take_line_parts(remaining, 2, 60)
    assert remaining == ()
    assert first[0].hops[0] == (('a', 1, 3, 80), ('b', 1, 3, 10))
    assert second[1].hops[0] == (('b', 1, 3, 10),)
    assert sum(part.cents for part in first + second) == 150
    combined = {}
    for rows in (payment_rows(first, 1, 90), payment_rows(second, 2, 60)):
        for row in rows:
            key = row['transaction_id'], row['for_id']
            combined[key] = combined.get(key, 0) + row['cents']
    assert combined == {(r['transaction_id'], r['for_id']): r['cents']
                        for r in payment_rows(original, 1, 150)}


def _cash_matches(lines, families):
    """Every part saved as a payback must add up, per unit, to the cash on each card."""
    from app.services.balances import unit_of
    net = {}
    for line in lines:
        payer = line.from_unit if line.from_unit[0] == 'family' else unit_of(line.from_unit[1], families)
        for part in line.parts:
            giver = payer if part.giver is None else unit_of(part.giver, families)
            net[giver] = net.get(giver, 0) - part.sgd_cents
            receiver = unit_of(part.payment.to_unit[1], families)
            net[receiver] = net.get(receiver, 0) + part.sgd_cents
        net[payer] = net.get(payer, 0) + line.sgd_cents
        receiver = unit_of(line.to_person, families)
        net[receiver] = net.get(receiver, 0) - line.sgd_cents
    return not any(net.values())


def test_shortcut_moves_along_existing_lines_through_a_family():
    """Lees owe Bob (a Ng), the Ngs owe Alice, and the Lees already pay Alice (#171)."""
    families = {1: 'Tan', 2: 'Ng', 3: 'Ng', 4: 'Lee'}
    payments = [payment(('family', 'Lee'), 1, 500), payment(('family', 'Ng'), 1, 300),
                payment(('family', 'Lee'), 2, 40, 'USD')]
    lines = one_currency(payments, families, {'USD': Decimal(2)})
    assert [(line.from_unit, line.to_person, line.sgd_cents) for line in lines] == [
        (('family', 'Lee'), 1, 520), (('family', 'Ng'), 1, 280)]
    lee = lines[0]
    assert lee.shortcuts == ((('family', 'Ng'), 2, 20),)
    # The Lees pay Alice Bob's 20 instead, and Bob passes on 20 of what the Ngs owe Alice.
    assert [(p.number, p.cents_in_currency, p.sgd_cents, p.giver) for p in lee.parts] == [
        (1, 500, 500, None), (3, 40, 20, None), (2, 20, 20, 2)]
    assert [(p.number, p.cents_in_currency, p.giver) for p in lines[1].parts] == [(2, 280, None)]
    assert _cash_matches(lines, families)


def test_no_shortcut_to_someone_never_paid():
    """The Lees never owed Alice, so they are never sent to her: only existing lines."""
    families = {1: 'Tan', 2: 'Ng', 4: 'Lee'}
    payments = [payment(('family', 'Ng'), 1, 300), payment(('family', 'Lee'), 2, 20)]
    lines = one_currency(payments, families, {})
    assert [(line.from_unit, line.to_person, line.sgd_cents, line.shortcuts) for line in lines] == [
        (('family', 'Ng'), 1, 300, ()), (('family', 'Lee'), 2, 20, ())]


def test_shortcut_inside_a_family_moves_the_whole_line_with_its_reverse_part():
    """Bob owes Alice net 20 (30 less 10 back); the Lees owe Bob and already pay Alice."""
    families = {1: 'Tan', 2: 'Tan', 4: 'Lee'}
    payments = [payment(('family', 'Lee'), 1, 500), payment(('family', 'Lee'), 2, 400),
                payment(('person', 2), 1, 30), payment(('person', 1), 2, 10, 'USD')]
    lines = one_currency(payments, families, {'USD': Decimal(1)})
    assert [(line.from_unit, line.to_person, line.sgd_cents) for line in lines] == [
        (('family', 'Lee'), 1, 520), (('family', 'Lee'), 2, 380)]
    assert lines[0].shortcuts == ((('person', 2), 2, 20),)
    # Bob hands on his own 30, and Alice's 10 back to Bob stays a reverse part from Alice.
    assert [(p.number, p.sgd_cents, p.giver, p.reverse) for p in lines[0].parts] == [
        (1, 500, None, False), (2, 20, None, False), (3, 30, 2, False), (4, 10, 1, True)]
    assert _cash_matches(lines, families)
    assert lines == one_currency(payments, dict(reversed(list(families.items()))), {'USD': Decimal(1)})
