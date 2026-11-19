"""Trip repayments use fictional people and whole cents, without network or database."""

import pytest

from app.services.balances import (
    NO_PAYER_LINE,
    NO_SPLITS_LINE,
    Payment,
    trip_payments,
    trip_balance_lines,
    unit_balances,
    unit_of,
)
from app.services.splits import share_out


def entry(paid_by, splits, currency='SGD'):
    """Make a fictional bill without touching storage."""
    return {'currency': currency, 'paid_by': paid_by, 'splits': splits}


def apply_payments(net, payments, families):
    """Apply the suggested payments to the totals, added up per family (or lone person).

    Totals are per person, but a family pays as one, so the check is made per unit: after
    every suggested payment, every unit must be at zero.
    """
    after = {}
    for (_, pid), cents in net.items():
        unit = unit_of(pid, families)
        after[unit] = after.get(unit, 0) + cents
    for payment in payments:
        payer = payment.from_unit if payment.from_unit[0] == 'family' else unit_of(payment.from_unit[1], families)
        after[payer] = after.get(payer, 0) + payment.cents
        receiver = unit_of(payment.to_unit[1], families)
        after[receiver] = after.get(receiver, 0) - payment.cents
    return after


def test_only_joes_family_pays_ben():
    """Ann's other sharers never acquire a debt to Ben through pooling."""
    families = {1: 'T', 2: 'T', 3: 'J', 4: 'J', 5: 'A', 6: 'B', 7: 'M', 8: 'M'}
    names = dict(enumerate(('Ann', 'Ben', 'Joe', 'Jo', 'Amy', 'Bea', 'Max', 'Mia'), 1))
    entries = [entry(1, {1: 100, 2: 100, 3: 100, 4: 100, 5: 150, 6: 150, 7: 150, 8: 150}),
               entry(2, dict.fromkeys((1, 2, 3, 4), 100))]
    expected = [('SGD', Payment(('family', family), ('person', creditor), cents))
                for family, creditor, cents in [('M', 1, 300), ('J', 1, 200), ('J', 2, 200),
                                                ('A', 1, 150), ('B', 1, 150)]]
    assert trip_payments(entries, families) == expected
    assert trip_payments(reversed(entries), families) == expected
    assert '3. J family pays Ben 2.00' in trip_balance_lines(entries, names, families)
    assert sum(unit_balances(entries, families).net['SGD'].values()) == 0


def test_three_family_cycle_keeps_every_line():
    """A zero net position does not erase debts through a third family."""
    families = {1: 'T', 2: 'J', 3: 'M'}
    entries = [entry(1, {2: 100}), entry(2, {3: 100}), entry(3, {1: 100})]
    assert trip_payments(entries, families) == [
        ('SGD', Payment(('family', 'J'), ('person', 1), 100)),
        ('SGD', Payment(('family', 'M'), ('person', 2), 100)),
        ('SGD', Payment(('family', 'T'), ('person', 3), 100))]
    assert sum(unit_balances(entries, families).net['SGD'].values()) == 0


@pytest.mark.parametrize('paid,remaining', [(40, 60), (100, 0), (150, -50)])
def test_gift_clears_only_beneficiary_line(paid, remaining):
    """Partial and exact gifts create no loan; excess belongs to the actual giver."""
    families = {1: 'T', 2: 'J', 3: 'M'}
    entries = [entry(1, {2: 100})]
    gift = dict(from_id=3, to_id=1, for_id=2, cents=paid, currency='SGD')
    expected = ([] if remaining == 0 else
                [('SGD', Payment(('family', 'J'), ('person', 1), remaining))] if remaining > 0 else
                [('SGD', Payment(('family', 'T'), ('person', 3), -remaining))])
    assert trip_payments(entries, families, [gift]) == expected
    assert sum(unit_balances(entries, families, [gift]).net['SGD'].values()) == 0
    report = '\n'.join(trip_balance_lines(entries, {1: 'Ann', 2: 'Joe', 3: 'Max'},
                                          families, settlements=[gift]))
    assert f'Max paid Ann {paid / 100:.2f} SGD for J family' in report


def test_gift_for_the_receivers_relative_clears_their_line():
    """Max (J) pays Ann what her relative Ben owes her: Ben's line is cleared, nobody owes Max.
    A second gift for Ben, who now owes nothing, is excess: Ann's family owes it back to Max."""
    families = {1: 'T', 2: 'T', 3: 'J'}
    gift = dict(from_id=3, to_id=1, for_id=2, cents=100, currency='SGD')
    assert unit_balances([entry(1, {2: 100})], families, [gift]).lines == {'SGD': {}}
    assert unit_balances([entry(1, {2: 100})], families, [gift, gift]).lines == {
        'SGD': {(('family', 'T'), 3): 100}}


def test_bill_inside_a_family_is_owed_per_person():
    """Ann's bill inside family T is owed to Ann; Ben's bill shared with everyone is owed to
    Ben. Ann and Ben owe each other 100, so that nets out; Cy owes both of them."""
    families = {1: 'T', 2: 'T', 3: 'T', 4: 'U', 5: 'V', 6: 'W', 7: 'X'}
    entries = [entry(1, {1: 10000, 2: 10000, 3: 10000}),
               entry(2, dict.fromkeys(range(1, 8), 10000))]
    units = unit_balances(entries, families).net['SGD']
    assert units == {('person', 1): 10000, ('person', 2): 50000, ('person', 3): -20000,
                     ('person', 4): -10000, ('person', 5): -10000, ('person', 6): -10000,
                     ('person', 7): -10000}
    payments = [p for _, p in trip_payments(entries, families)]
    assert payments == [Payment(('family', label), ('person', 2), 10000) for label in 'UVWX'] + [
        Payment(('person', 3), ('person', 1), 10000), Payment(('person', 3), ('person', 2), 10000)]
    assert set(apply_payments(units, payments, families).values()) == {0}


def test_bill_inside_one_family_is_settled_per_person():
    """The family as a whole is settled, but its members still owe the one who paid."""
    entries = [entry(1, {1: 10000, 2: 10000, 3: 10000})]
    assert trip_balance_lines(entries, {1: 'Ann', 2: 'Ben', 3: 'Cy'}, dict.fromkeys((1, 2, 3), 'T')) == [
        'SGD', 'T family is settled', '    Ann is owed 200.00', '    Ben owes 100.00',
        '    Cy owes 100.00', 'To settle:', '1. Ben pays Ann 100.00', '2. Cy pays Ann 100.00']


@pytest.mark.parametrize(('alice_credit', 'bob_credit', 'debt', 'lines'), [
    (20000, 40000, 30000, {(('family', 'U'), 2): 40000, (('family', 'T'), 3): 10000}),
    (40000, 40000, 30000, {(('family', 'U'), 2): 40000, (('family', 'U'), 1): 10000}),
    (20000, 40000, 60000, {(('family', 'U'), 2): 40000, (('family', 'T'), 3): 40000}),
])
def test_opposite_debts_cancel_only_between_the_same_two_people(alice_credit, bob_credit, debt, lines):
    """Carol (U) and Alice owe each other, so that nets out. Carol's debt to Bob never cancels
    against what Alice's family owes Carol: that would pay Bob less than he put in."""
    entries = [entry(2, {3: bob_credit}), entry(1, {3: alice_credit}), entry(3, {1: debt})]
    families = {1: 'T', 2: 'T', 3: 'U'}
    result = unit_balances(entries, families)
    assert result.lines['SGD'] == lines
    assert sum(result.net['SGD'].values()) == 0


def test_unlabelled_persons_debts_and_credits_net_naturally():
    entries, families = [entry(1, {2: 1001}), entry(2, {1: 400})], {}
    units = unit_balances(entries, families).net['SGD']
    assert units == {('person', 1): 601, ('person', 2): -601}
    assert [p for _, p in trip_payments(entries, families)] == [Payment(('person', 2), ('person', 1), 601)]


def test_currencies_are_kept_apart():
    entries = [entry(1, {3: 1001}), entry(3, {2: 400}),
               entry(3, {1: 701}, 'AUD'), entry(2, {3: 200}, 'AUD')]
    families = {1: 'T', 2: 'T', 3: 'U'}
    result = unit_balances(entries, families)
    assert result.net == {
        'SGD': {('person', 1): 1001, ('person', 2): -400, ('person', 3): -601},
        'AUD': {('person', 1): -701, ('person', 2): 200, ('person', 3): 501},
    }
    for currency, units in result.net.items():
        assert sum(units.values()) == 0
        payments = [p for c, p in trip_payments(entries, families) if c == currency]
        assert set(apply_payments(units, payments, families).values()) == {0}


def test_uneven_whole_cents_are_conserved():
    entries = [entry(1, share_out(555, {1: 1, 2: 1, 3: 1})),
               entry(3, share_out(10000, {1: 5, 2: 5, 3: 4}))]
    families = {1: 'T', 2: 'T', 3: 'U'}
    # 185 each of the 555; of the 10000, 3572 / 3571 / 2857 (the spare cent to the lowest id).
    units = unit_balances(entries, families).net['SGD']
    assert units == {('person', 1): -3202, ('person', 2): -3756, ('person', 3): 6958}
    assert [p for _, p in trip_payments(entries, families)] == [
        Payment(('family', 'T'), ('person', 3), 6958), Payment(('person', 2), ('person', 1), 185)]


def test_no_splits_and_missing_payers():
    assert unit_balances([entry(None, {}), entry(1, None)], {}).net == {}
    orphan = entry(None, {1: 500, 2: 500})
    result = unit_balances([orphan, entry(1, {2: 100})], {})
    assert result.net == {'SGD': {('person', 1): 100, ('person', 2): -100}}
    assert result.no_payer == (orphan,)
    assert trip_balance_lines([], {}, {}) == [NO_SPLITS_LINE]
    assert trip_balance_lines([orphan], {}, {}) == [NO_SPLITS_LINE, '', NO_PAYER_LINE.format(
        count=1, entries='entry has', they='it is')]
    assert trip_balance_lines([orphan, orphan], {}, {}) == [NO_SPLITS_LINE, '', NO_PAYER_LINE.format(
        count=2, entries='entries have', they='they are')]


def test_unit_is_family_or_person_without_label():
    assert unit_of(1, {1: 'T'}) == ('family', 'T')
    for families in ({}, {1: None}, {1: ''}):
        assert unit_of(1, families) == ('person', 1)
    assert unit_of(1, {1: '1'}) != unit_of(1, {})


@pytest.mark.parametrize('show_members', [True, False])
def test_exact_lines_group_credits_but_pay_people(show_members):
    entries = [entry(2, {3: 70000, 4: 10000}), entry(1, {3: 10000})]
    lines = ['SGD', 'T family is owed 900.00']
    if show_members:
        lines += ['    Alice is owed 100.00', '    Bob is owed 800.00']
    lines += ['U family owes 800.00']
    if show_members:
        lines += ['    Carol owes 800.00']
    lines += ['Erin owes 100.00', 'To settle:',
              '1. U family pays Bob 700.00', '2. U family pays Alice 100.00',
              '3. Erin pays Bob 100.00']
    assert trip_balance_lines(entries, {1: 'Alice', 2: 'Bob', 3: 'Carol', 4: 'Erin'},
                              {1: 'T', 2: 'T', 3: 'U'}, show_members=show_members) == lines


def test_owing_family_lists_its_members_and_lone_creditor_has_name():
    entries = [entry(1, {3: 3000}), entry(3, {1: 10000})]
    assert trip_balance_lines(entries, {1: 'Alice', 2: 'Bob', 3: 'Carol'}, {1: 'T', 2: 'T'}) == [
        'SGD', 'T family owes 70.00', '    Alice owes 70.00', 'Carol is owed 70.00', 'To settle:',
        '1. T family pays Carol 70.00']


def test_currency_order_settled_currency_single_member_family_and_warning():
    entries = [entry(1, {2: 1000}), entry(2, {1: 1000}),
               entry(3, {1: 500}, 'AUD'), entry(None, {2: 100}, 'AUD')]
    assert trip_balance_lines(entries, {1: 'Alice'}, {3: 'Cedar Grove'}) == [
        'AUD', 'Cedar Grove is owed 5.00', '    Person #3 is owed 5.00',
        'Alice owes 5.00', 'To settle:', '1. Alice pays Person #3 5.00', '',
        'SGD', 'Everyone is settled.', '',
        NO_PAYER_LINE.format(count=1, entries='entry has', they='it is')]


def _fake_connection(monkeypatch, rows):
    from unittest.mock import MagicMock

    import app.services.ledger_queries as ledger_queries
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = rows
    context_manager = MagicMock()
    context_manager.__enter__.return_value = conn
    monkeypatch.setattr(ledger_queries, 'get_connection', lambda: context_manager)
    return conn, ledger_queries


def test_trip_query_folds_share_rows_into_entries(monkeypatch):
    rows = [('t1', '2026-11-27', 'dinner split with Bob', 6000, 'AUD', 1, 1, 3000),
            ('t1', '2026-11-27', 'dinner split with Bob', 6000, 'AUD', 1, 2, 3000),
            ('t2', '2026-11-28', 'petrol', 900, 'AUD', None, 2, 900)]
    conn, ledger_queries = _fake_connection(monkeypatch, rows)
    entries = ledger_queries.get_trip_split_entries(7)
    assert [(e['transaction_id'], e['paid_by'], e['splits']) for e in entries] == [
        ('t1', 1, {1: 3000, 2: 3000}), ('t2', None, {2: 900})]
    # Straight into the balances: the entry with no payer is handed back, not counted.
    result = unit_balances(entries, {})
    assert result.net == {'AUD': {('person', 1): 3000, ('person', 2): -3000}} and len(result.no_payer) == 1
    sql, params = conn.execute.call_args.args
    sql = ' '.join(str(sql).split())
    assert params == {'trip_id': 7} and 't.trip_id = :trip_id' in sql
    assert 'COALESCE(t.paid_by_participant_id, a.owner_participant_id, t.entered_by_participant_id)' in sql
    assert 'base_amount' not in sql and 'SELECT *' not in sql


def test_trip_query_failure_is_none_not_an_empty_trip(monkeypatch):
    conn, ledger_queries = _fake_connection(monkeypatch, [])
    assert ledger_queries.get_trip_split_entries(7) == []
    conn.execute.side_effect = RuntimeError('database is down')
    assert ledger_queries.get_trip_split_entries(7) is None


@pytest.mark.parametrize('paid,expected', [
    (10000, {}),
    (4000, {('person', 1): -6000, ('person', 2): 6000}),
    (15000, {('person', 2): -5000, ('person', 1): 5000}),
])
def test_payback_absorbs_family_debt_and_overpay_flips(paid, expected):
    """A family member pays Bob: full, partial and excess all conserve whole cents."""
    settlement = dict(from_id=1, to_id=2, cents=paid, currency='SGD')
    result = unit_balances([entry(2, {1: 10000})], {1: 'T', 2: 'U'}, [settlement])
    assert result.net == {'SGD': expected}
    assert sum(result.net['SGD'].values()) == 0


def test_paybacks_without_bills_keep_currencies_separate():
    """The paid currency is descriptive; only the clears side moves balances."""
    settlements = [dict(from_id=1, to_id=2, cents=5000, currency='AUD',
                        paid_cents=4000, paid_currency='SGD')]
    result = unit_balances([entry(2, {1: 10000})], {1: 'T', 2: 'U'}, settlements)
    assert result.net == {'SGD': {('person', 1): -10000, ('person', 2): 10000},
                          'AUD': {('person', 1): 5000, ('person', 2): -5000}}
    assert all(sum(units.values()) == 0 for units in result.net.values())
    assert unit_balances([], {}, settlements).net == {
        'AUD': {('person', 1): 5000, ('person', 2): -5000}}


@pytest.mark.parametrize('paid,lines', [
    (5000, {}),
    (2000, {(('person', 1), 2): 3000}),
    (8000, {(('person', 2), 1): 3000}),
])
def test_inside_family_payback_pays_down_a_person_line(paid, lines):
    """Alice pays her relative Bob back: full, partial, and an overpayment Bob owes back."""
    families = {1: 'T', 2: 'T'}
    settlements = [dict(from_id=1, to_id=2, cents=paid, currency='SGD')]
    result = unit_balances([entry(2, {1: 5000})], families, settlements)
    assert result.lines == {'SGD': lines}
    report = trip_balance_lines([entry(2, {1: 5000})], {1: 'Alice', 2: 'Bob'}, families,
                                settlements=settlements)
    assert 'Already paid back:' in report
    assert ('Everyone is settled.' in report) == (paid == 5000)


def test_stable_numbering_and_payback_history():
    """Numbers in the report match the pure payment list across currencies and input order."""
    from datetime import date
    from app.services.balances import trip_payments

    entries = [entry(2, {1: 10000}, 'SGD'), entry(2, {1: 80000}, 'IDR')]
    settlements = [dict(from_id=1, to_id=2, cents=60000, currency='IDR',
                        paid_cents=50, paid_currency='SGD', date=date(2026, 5, 27))]
    families = {1: 'T', 2: 'U'}
    payments = trip_payments(entries, families, settlements)
    assert payments == trip_payments(list(reversed(entries)), families, settlements)
    assert payments == [('IDR', Payment(('family', 'T'), ('person', 2), 20000)),
                        ('SGD', Payment(('family', 'T'), ('person', 2), 10000))]
    lines = trip_balance_lines(entries, {1: 'Alice', 2: 'Bob'}, families, settlements=settlements)
    assert '1. T family pays Bob 200.00' in lines and '2. T family pays Bob 100.00' in lines
    assert ' • Alice paid Bob 600.00 IDR (as 0.50 SGD), 27 May' in lines
    same = dict(settlements[0], paid_currency='IDR', paid_cents=60000)
    assert '(as ' not in '\n'.join(trip_balance_lines(entries, {}, families, settlements=[same]))


# --- ADR-0051: debts per person, then rolled up to families. ---

TAN_LEE = {1: 'Tan', 2: 'Tan', 3: 'Lee', 4: 'Lee'}
TAN_LEE_NAMES = {1: 'Alice', 2: 'Bob', 3: 'Carol', 4: 'Dave'}


def test_adr_0051_example():
    """Alice paid 400 and Bob paid 200, each shared by all four. Bob's 100 for Alice's bill
    and Alice's 50 for Bob's net to "Bob pays Alice 50"; the Lees pay each payer in full."""
    entries = [entry(1, dict.fromkeys((1, 2, 3, 4), 10000)),
               entry(2, dict.fromkeys((1, 2, 3, 4), 5000))]
    result = unit_balances(entries, TAN_LEE)
    assert result.net['SGD'] == {('person', 1): 25000, ('person', 2): 5000,
                                 ('person', 3): -15000, ('person', 4): -15000}
    assert trip_balance_lines(entries, TAN_LEE_NAMES, TAN_LEE) == [
        'SGD', 'Lee family owes 300.00', '    Carol owes 150.00', '    Dave owes 150.00',
        'Tan family is owed 300.00', '    Alice is owed 250.00', '    Bob is owed 50.00',
        'To settle:', '1. Lee family pays Alice 200.00', '2. Lee family pays Bob 100.00',
        '3. Bob pays Alice 50.00']


def test_family_payback_clears_the_beneficiarys_own_debt_first():
    """Dave pays Alice 150 for the Lees: his own 100 first, then 50 of Carol's."""
    entries = [entry(1, {3: 10000, 4: 10000})]
    settlement = dict(from_id=4, to_id=1, cents=15000, currency='SGD')
    result = unit_balances(entries, TAN_LEE, [settlement])
    assert result.net['SGD'] == {('person', 3): -5000, ('person', 1): 5000}
    assert result.lines['SGD'] == {(('family', 'Lee'), 1): 5000}


def test_a_relative_can_pay_another_familys_debt_as_a_gift():
    """Bob pays Alice, his relative, what Carol owes her: Carol's line is cleared and nobody
    owes Bob. Then Bob pays his own line to Alice."""
    entries = [entry(1, {2: 3000, 3: 3000})]
    gift = dict(from_id=2, to_id=1, for_id=3, cents=3000, currency='SGD')
    result = unit_balances(entries, TAN_LEE, [gift])
    assert result.lines['SGD'] == {(('person', 2), 1): 3000}
    own = dict(from_id=2, to_id=1, cents=3000, currency='SGD')
    assert unit_balances(entries, TAN_LEE, [gift, own]).lines == {'SGD': {}}


def test_each_persons_total_is_what_they_paid_less_what_they_owe():
    """The rule ADR-0051 rests on, over many made-up trips: rolling up never changes anyone's
    total, and the suggested payments settle every family and lone person."""
    import random
    rng = random.Random(51)
    for _ in range(200):
        people = list(range(1, rng.randint(3, 10)))  # 2 to 9 people
        families = {pid: rng.choice('ABC') for pid in people if rng.random() < 0.8}
        entries, expected = [], dict.fromkeys(people, 0)
        for _ in range(rng.randint(1, 6)):
            payer = rng.choice(people)
            sharers = rng.sample(people, rng.randint(1, len(people)))
            splits = {pid: rng.randint(1, 50000) for pid in sharers}
            entries.append(entry(payer, splits))
            for pid, cents in splits.items():
                expected[pid] -= cents
                expected[payer] += cents
        settlements = []
        for _ in range(rng.randint(0, 3)):
            giver, receiver = rng.sample(people, 2)
            cents = rng.randint(1, 50000)
            settlements.append(dict(from_id=giver, to_id=receiver, cents=cents, currency='SGD'))
            # A payback is money handed over: the giver is up by it, the receiver down.
            expected[giver] += cents
            expected[receiver] -= cents
        result = unit_balances(entries, families, settlements)
        net = result.net.get('SGD', {})
        got = {pid: net.get(('person', pid), 0) for pid in people}
        if not settlements:
            assert got == expected
        else:
            # One member paying the family's line pays the others' part too, on their behalf,
            # and that is never a new debt (ADR-0051). So with paybacks, the totals hold per
            # family (or lone person), not per member of an owing family.
            def per_unit(totals):
                units = {}
                for pid, cents in totals.items():
                    units[unit_of(pid, families)] = units.get(unit_of(pid, families), 0) + cents
                return units
            assert per_unit(got) == per_unit(expected)
        payments = [p for _, p in trip_payments(entries, families, settlements)]
        assert set(apply_payments(net, payments, families).values()) <= {0}
