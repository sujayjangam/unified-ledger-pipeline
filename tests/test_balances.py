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


def apply_payments(net, payments):
    """Apply the suggested lines to their signed display totals."""
    after = dict(net)
    for payment in payments:
        after[payment.from_unit] += payment.cents
        after[payment.to_unit] -= payment.cents
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


def test_beneficiary_in_receivers_unit_is_skipped():
    """The beneficiary, rather than the giver, decides whether a payment is internal."""
    settlement = dict(from_id=3, to_id=1, for_id=2, cents=100, currency='SGD')
    assert unit_balances([], {1: 'T', 2: 'T', 3: 'J'}, [settlement]).lines == {'SGD': {}}


def test_internal_bill_does_not_give_alice_bobs_repayment():
    families = {1: 'T', 2: 'T', 3: 'T', 4: 'U', 5: 'V', 6: 'W', 7: 'X'}
    entries = [entry(1, {1: 10000, 2: 10000, 3: 10000}),
               entry(2, dict.fromkeys(range(1, 8), 10000))]
    units = unit_balances(entries, families).net['SGD']
    assert units == {('person', 2): 40000, ('family', 'U'): -10000,
                     ('family', 'V'): -10000, ('family', 'W'): -10000,
                     ('family', 'X'): -10000}
    payments = [p for _, p in trip_payments(entries, families)]
    assert payments == [Payment(('family', label), ('person', 2), 10000)
                        for label in 'UVWX']
    assert set(apply_payments(units, payments).values()) == {0}


def test_bill_inside_one_family_moves_nothing():
    entries = [entry(1, {1: 10000, 2: 10000, 3: 10000})]
    assert unit_balances(entries, dict.fromkeys((1, 2, 3), 'T')).net == {'SGD': {}}
    assert trip_balance_lines(entries, {}, dict.fromkeys((1, 2, 3), 'T')) == [
        'SGD', 'Everyone is settled.']


@pytest.mark.parametrize(('alice_credit', 'bob_credit', 'debt', 'remaining'), [
    (20000, 40000, 30000, {1: 20000, 2: 10000}),
    (40000, 40000, 30000, {1: 10000, 2: 40000}),
    (20000, 40000, 50000, {1: 10000}),
    (20000, 40000, 60000, {}),
])
def test_pairwise_cancellation_largest_first(alice_credit, bob_credit, debt, remaining):
    """Opposite lines cancel largest first, with creditor id breaking ties."""
    entries = [entry(2, {3: bob_credit}), entry(1, {3: alice_credit}), entry(3, {1: debt})]
    families = {1: 'T', 2: 'T', 3: 'U'}
    result = unit_balances(entries, families)
    assert result.lines['SGD'] == {(('family', 'U'), pid): cents for pid, cents in remaining.items()}
    assert sum(result.net['SGD'].values()) == 0


def test_unlabelled_persons_debts_and_credits_net_naturally():
    entries, families = [entry(1, {2: 1001}), entry(2, {1: 400})], {}
    units = unit_balances(entries, families).net['SGD']
    assert units == {('person', 1): 601, ('person', 2): -601}
    assert [p for _, p in trip_payments(entries, families)] == [Payment(('person', 2), ('person', 1), 601)]


def test_currencies_are_kept_apart_including_family_absorption():
    entries = [entry(1, {3: 1001}), entry(3, {2: 400}),
               entry(3, {1: 701}, 'AUD'), entry(2, {3: 200}, 'AUD')]
    families = {1: 'T', 2: 'T', 3: 'U'}
    result = unit_balances(entries, families)
    assert result.net == {
        'SGD': {('person', 1): 601, ('family', 'U'): -601},
        'AUD': {('family', 'T'): -501, ('person', 3): 501},
    }
    for currency, units in result.net.items():
        assert sum(units.values()) == 0
        payments = [p for c, p in trip_payments(entries, families) if c == currency]
        assert set(apply_payments(units, payments).values()) == {0}


def test_uneven_whole_cents_are_conserved():
    entries = [entry(1, share_out(555, {1: 1, 2: 1, 3: 1})),
               entry(3, share_out(10000, {1: 5, 2: 5, 3: 4}))]
    families = {1: 'T', 2: 'T', 3: 'U'}
    units = unit_balances(entries, families).net['SGD']
    assert units == {('family', 'T'): -6958, ('person', 3): 6958}
    assert [p for _, p in trip_payments(entries, families)] == [Payment(('family', 'T'), ('person', 3), 6958)]


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
    lines += ['U family owes 800.00', 'Erin owes 100.00', 'To settle:',
              '1. U family pays Bob 700.00', '2. U family pays Alice 100.00',
              '3. Erin pays Bob 100.00']
    assert trip_balance_lines(entries, {1: 'Alice', 2: 'Bob', 3: 'Carol', 4: 'Erin'},
                              {1: 'T', 2: 'T', 3: 'U'}, show_members=show_members) == lines


def test_owing_family_has_no_member_lines_and_lone_creditor_has_name():
    entries = [entry(1, {3: 3000}), entry(3, {1: 10000})]
    assert trip_balance_lines(entries, {1: 'Alice', 2: 'Bob', 3: 'Carol'}, {1: 'T', 2: 'T'}) == [
        'SGD', 'T family owes 70.00', 'Carol is owed 70.00', 'To settle:',
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
    (4000, {('family', 'T'): -6000, ('person', 2): 6000}),
    (15000, {('family', 'U'): -5000, ('person', 1): 5000}),
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
    assert result.net == {'SGD': {('family', 'T'): -10000, ('person', 2): 10000},
                          'AUD': {('person', 1): 5000, ('family', 'U'): -5000}}
    assert all(sum(units.values()) == 0 for units in result.net.values())
    assert unit_balances([], {}, settlements).net == {
        'AUD': {('person', 1): 5000, ('person', 2): -5000}}


def test_inside_family_payback_is_skipped_but_listed():
    """An internal payment changes nothing outsiders owe, but its history is visible."""
    settlements = [dict(from_id=1, to_id=2, cents=5000, currency='SGD')]
    assert unit_balances([], {1: 'T', 2: 'T'}, settlements).net == {'SGD': {}}
    lines = trip_balance_lines([], {1: 'Alice', 2: 'Bob'}, {1: 'T', 2: 'T'}, settlements=settlements)
    assert 'Everyone is settled.' in lines and 'Already paid back:' in lines


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
