"""Trip repayments use fictional people and whole cents, without network or database."""

import pytest

from app.services.balances import (
    NO_PAYER_LINE,
    NO_SPLITS_LINE,
    Payment,
    suggest_payments,
    trip_balance_lines,
    unit_balances,
    unit_of,
)
from app.services.splits import share_out


def entry(paid_by, splits, currency='SGD'):
    return {'currency': currency, 'paid_by': paid_by, 'splits': splits}


def apply_payments(net, payments):
    after = dict(net)
    for payment in payments:
        after[payment.from_unit] += payment.cents
        after[payment.to_unit] -= payment.cents
    return after


def test_internal_bill_does_not_give_alice_bobs_repayment():
    families = {1: 'T', 2: 'T', 3: 'T', 4: 'U', 5: 'V', 6: 'W', 7: 'X'}
    entries = [entry(1, {1: 10000, 2: 10000, 3: 10000}),
               entry(2, dict.fromkeys(range(1, 8), 10000))]
    units = unit_balances(entries, families).net['SGD']
    assert units == {('person', 2): 40000, ('family', 'U'): -10000,
                     ('family', 'V'): -10000, ('family', 'W'): -10000,
                     ('family', 'X'): -10000}
    payments = suggest_payments(units)
    assert payments == [Payment(('family', label), ('person', 2), 10000)
                        for label in 'UVWX']
    assert set(apply_payments(units, payments).values()) == {0}


def test_bill_inside_one_family_moves_nothing():
    entries = [entry(1, {1: 10000, 2: 10000, 3: 10000})]
    assert unit_balances(entries, dict.fromkeys((1, 2, 3), 'T')).net == {'SGD': {}}
    assert trip_balance_lines(entries, {}, dict.fromkeys((1, 2, 3), 'T')) == [
        'SGD', 'Everyone is settled.']


def test_family_debt_is_absorbed_into_its_payers_credit():
    units = unit_balances([entry(2, {3: 40000}), entry(4, {1: 15000})],
                          {1: 'T', 2: 'T', 3: 'U', 4: 'V'}).net['SGD']
    assert units == {('person', 2): 25000, ('family', 'U'): -40000, ('person', 4): 15000}
    assert suggest_payments(units) == [Payment(('family', 'U'), ('person', 2), 25000),
                                       Payment(('family', 'U'), ('person', 4), 15000)]


@pytest.mark.parametrize(('alice_credit', 'bob_credit', 'debt', 'remaining'), [
    (20000, 40000, 30000, {('person', 1): 20000, ('person', 2): 10000}),
    (40000, 40000, 30000, {('person', 1): 10000, ('person', 2): 40000}),
    (20000, 40000, 50000, {('person', 1): 10000}),
    (20000, 40000, 60000, {}),
    (20000, 40000, 70000, {('family', 'T'): -10000}),
])
def test_largest_credit_absorbs_first_then_lowest_id(alice_credit, bob_credit, debt, remaining):
    # Put Bob first in the input so equal credits must use id, not insertion order.
    entries = [entry(2, {3: bob_credit}), entry(1, {3: alice_credit}), entry(4, {1: debt})]
    units = unit_balances(entries, {1: 'T', 2: 'T', 3: 'U', 4: 'V'}).net['SGD']
    assert units == {**remaining, ('family', 'U'): -alice_credit - bob_credit, ('person', 4): debt}
    assert sum(units.values()) == 0
    payments = suggest_payments(units)
    assert set(apply_payments(units, payments).values()) == {0}
    assert all(not (p.from_unit == ('family', 'T') and p.to_unit in [('person', 1), ('person', 2)])
               for p in payments)


def test_unlabelled_persons_debts_and_credits_net_naturally():
    units = unit_balances([entry(1, {2: 1001}), entry(2, {1: 400})], {}).net['SGD']
    assert units == {('person', 1): 601, ('person', 2): -601}
    assert suggest_payments(units) == [Payment(('person', 2), ('person', 1), 601)]


def test_currencies_are_kept_apart_including_family_absorption():
    entries = [entry(1, {3: 1001}), entry(3, {2: 400}),
               entry(3, {1: 701}, 'AUD'), entry(2, {3: 200}, 'AUD')]
    result = unit_balances(entries, {1: 'T', 2: 'T', 3: 'U'})
    assert result.net == {
        'SGD': {('person', 1): 601, ('family', 'U'): -601},
        'AUD': {('family', 'T'): -501, ('person', 3): 501},
    }
    for units in result.net.values():
        assert sum(units.values()) == 0
        assert set(apply_payments(units, suggest_payments(units)).values()) == {0}


def test_uneven_whole_cents_are_conserved():
    entries = [entry(1, share_out(555, {1: 1, 2: 1, 3: 1})),
               entry(3, share_out(10000, {1: 5, 2: 5, 3: 4}))]
    units = unit_balances(entries, {1: 'T', 2: 'T', 3: 'U'}).net['SGD']
    assert units == {('family', 'T'): -6958, ('person', 3): 6958}
    assert suggest_payments(units) == [Payment(('family', 'T'), ('person', 3), 6958)]


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


def test_largest_debtor_pays_largest_creditor():
    net = {'T': 5000, 'U': 1000, 'V': -4000, 'W': -2000}
    assert suggest_payments(net) == [Payment('V', 'T', 4000), Payment('W', 'T', 1000),
                                     Payment('W', 'U', 1000)]


def test_payments_clear_every_balance():
    net = {1: 12345, 2: -5000, 3: -7000, 4: 655, 5: -1000}
    payments = suggest_payments(net)
    assert all(p.cents > 0 for p in payments)
    assert len(payments) <= len(net) - 1
    assert set(apply_payments(net, payments).values()) == {0}
    assert suggest_payments({}) == []
    assert suggest_payments({'T': 0}) == []


def test_tied_payments_are_independent_of_input_order():
    assert suggest_payments({'B': -100, 'A': -100, 'D': 100, 'C': 100}) == [
        Payment('A', 'C', 100), Payment('B', 'D', 100)]
    assert suggest_payments({'C': 100, 'D': 100, 'A': -100, 'B': -100}) == [
        Payment('A', 'C', 100), Payment('B', 'D', 100)]


def test_unbalanced_inputs_are_refused():
    with pytest.raises(ValueError):
        suggest_payments({'T': 500, 'U': -400})


@pytest.mark.parametrize('show_members', [True, False])
def test_exact_lines_group_credits_but_pay_people(show_members):
    entries = [entry(2, {3: 70000, 4: 10000}), entry(1, {3: 10000})]
    lines = ['SGD', 'T family is owed 900.00']
    if show_members:
        lines += ['    Alice is owed 100.00', '    Bob is owed 800.00']
    lines += ['U family owes 800.00', 'Erin owes 100.00', 'To settle:',
              ' • U family pays Bob 800.00', ' • Erin pays Alice 100.00']
    assert trip_balance_lines(entries, {1: 'Alice', 2: 'Bob', 3: 'Carol', 4: 'Erin'},
                              {1: 'T', 2: 'T', 3: 'U'}, show_members=show_members) == lines


def test_owing_family_has_no_member_lines_and_lone_creditor_has_name():
    entries = [entry(1, {3: 3000}), entry(3, {1: 10000})]
    assert trip_balance_lines(entries, {1: 'Alice', 2: 'Bob', 3: 'Carol'}, {1: 'T', 2: 'T'}) == [
        'SGD', 'T family owes 70.00', 'Carol is owed 70.00', 'To settle:',
        ' • T family pays Carol 70.00']


def test_currency_order_settled_currency_single_member_family_and_warning():
    entries = [entry(1, {2: 1000}), entry(2, {1: 1000}),
               entry(3, {1: 500}, 'AUD'), entry(None, {2: 100}, 'AUD')]
    assert trip_balance_lines(entries, {1: 'Alice'}, {3: 'Cedar Grove'}) == [
        'AUD', 'Cedar Grove is owed 5.00', '    Person #3 is owed 5.00',
        'Alice owes 5.00', 'To settle:', ' • Alice pays Person #3 5.00', '',
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
