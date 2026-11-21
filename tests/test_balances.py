"""Trip repayments use fictional people and whole cents, without network or database."""

import pytest
from app.services import balances

from app.services.balances import (
    NO_PAYER_LINE,
    NO_SPLITS_LINE,
    Payment,
    open_bill_shares,
    trip_payments,
    trip_balance_lines,
    trip_payment_lines,
    trip_payback_lines,
    unit_balances,
    unit_of,
)
from app.services.splits import share_out


def bill(transaction_id, payer, splits, currency='SGD'):
    """Give a fictional entry an id so a payback can name its shares."""
    return dict(transaction_id=transaction_id, paid_by=payer, splits=splits, currency=currency)


def row_payback(transaction_id='lodging', for_id=3, cents=100, **changes):
    """Erin pays Alice on Carol's behalf, unless a case specifies otherwise."""
    result = dict(from_id=5, to_id=1, cents=cents, currency='SGD',
                  bills=[dict(transaction_id=transaction_id, for_id=for_id, cents=cents)])
    result.update(changes)
    return result


@pytest.mark.parametrize('entries,payback,expected', [
    ([bill('lodging', 1, {3: 100, 4: 200})], row_payback(), {(4, 1): 200}),
    ([bill('lodging', 1, {3: 100})], row_payback(), {}),
    ([bill('lodging', 1, {3: 100})], row_payback('deleted'), {(3, 1): 100, (1, 5): 100}),
    ([bill('lodging', 2, {3: 100})], row_payback(), {(3, 2): 100, (1, 5): 100}),
    ([bill('lodging', 1, {3: 100})],
     row_payback(cents=150, bills=[dict(transaction_id='lodging', for_id=3, cents=100)]),
     {(1, 5): 50}),
    ([bill('lodging', 1, {3: 100})], row_payback(cents=150), {(1, 5): 50}),
    ([bill('lodging', 1, {3: 100}), bill('car', 3, {1: 100})],
     row_payback(), {(1, 5): 100}),
    ([bill('lodging', None, {3: 100})], row_payback(), {(1, 5): 100}),
    ([bill('lodging', 1, {})], row_payback(), {(1, 5): 100}),
])
def test_bill_rows_clear_only_their_named_line(entries, payback, expected):
    """Unused cents return to the giver; an on-behalf payment never creates a loan."""
    result = unit_balances(entries, {}, [payback])
    assert result.lines == {'SGD': {(('person', debtor), creditor): cents
                                   for (debtor, creditor), cents in expected.items()}}
    assert sum(result.net['SGD'].values()) == 0


def test_row_on_a_bill_in_another_currency_clears_nothing():
    """An SGD payback naming an AUD bill must not take cents off the AUD debt: currencies
    never net, so the row's cents are owed back to the giver in SGD and the AUD debt stays."""
    entries = [bill('lodging', 1, {3: 100}, 'AUD')]
    result = unit_balances(entries, {}, [row_payback()])
    assert result.lines == {'AUD': {(('person', 3), 1): 100}, 'SGD': {(('person', 1), 5): 100}}
    assert open_bill_shares(entries, {}, [row_payback()]) == {('lodging', 3): 100}


def test_second_payback_for_a_paid_share_never_clears_another_bill():
    """Carol owes Alice 100 for lodging and 200 for dinner. Erin pays Alice 100 on Carol's
    behalf for lodging, twice: the second finds the lodging share already paid, so dinner's
    200 stays owed and Alice owes the second 100 back to Erin."""
    entries = [bill('lodging', 1, {3: 100}), bill('dinner', 1, {3: 200})]
    twice = [row_payback(), row_payback()]
    result = unit_balances(entries, {}, twice)
    assert result.lines == {'SGD': {(('person', 3), 1): 200, (('person', 1), 5): 100}}
    assert open_bill_shares(entries, {}, twice) == {('dinner', 3): 200}


def test_empty_bill_rows_keep_the_old_family_payback():
    """An explicit empty list is the same old beneficiary-first payback."""
    entries = [bill('lodging', 1, {3: 100, 4: 200})]
    payment = dict(from_id=5, to_id=1, for_id=3, cents=250, currency='SGD')
    families = {3: 'Lee', 4: 'Lee'}
    assert unit_balances(entries, families, [payment]) == unit_balances(
        entries, families, [dict(payment, bills=[])])


def test_open_shares_are_gross_and_rows_touch_only_the_named_bill():
    """Netting changes the report, while the gross lodging and car shares stay separate."""
    entries = [bill('lodging', 1, {1: 100, 2: 100, 3: 0}), bill('car', 2, {1: 50}),
               bill('dinner', 1, {2: 30}), bill('unknown', None, {2: 20})]
    assert open_bill_shares(iter(entries), {}) == {('lodging', 2): 100, ('car', 1): 50, ('dinner', 2): 30}
    payment = row_payback(for_id=2, cents=120)
    assert open_bill_shares(entries, {}, [payment, payment]) == {('car', 1): 50, ('dinner', 2): 30}


@pytest.mark.parametrize('transaction_id', ['missing', 'car', 'unknown'])
def test_ineligible_rows_leave_open_shares_alone(transaction_id):
    """Deleted, differently paid and payerless bills cannot consume another open share."""
    entries = [bill('lodging', 1, {3: 100}), bill('car', 2, {3: 100}),
               bill('unknown', None, {3: 100})]
    assert open_bill_shares(entries, {}, [row_payback(transaction_id)]) == {
        ('lodging', 3): 100, ('car', 3): 100}


def test_old_payback_uses_oldest_receiver_bills_and_netted_amount():
    """Only the 80 net cents cleared are allocated, in entry order and the same currency."""
    entries = [bill('other', 2, {3: 50}), bill('aud', 1, {3: 50}, 'AUD'),
               bill('old', 1, {3: 60}), bill('new', 1, {3: 70}), bill('opposite', 3, {1: 50})]
    payment = dict(from_id=3, to_id=1, cents=100, currency='SGD')
    assert open_bill_shares(entries, {}, [payment]) == {
        ('other', 3): 50, ('aud', 3): 50, ('new', 3): 50, ('opposite', 1): 50}


def test_family_payback_shares_follow_beneficiary_then_size_then_id():
    """Carol's own smaller share goes first; ties between the remaining members use id."""
    entries = [bill('old', 1, {3: 20, 4: 100, 5: 100}), bill('new', 1, {3: 30})]
    payment = dict(from_id=6, to_id=1, for_id=3, cents=180, currency='SGD')
    assert open_bill_shares(entries, {3: 'Lee', 4: 'Lee', 5: 'Lee'}, [payment]) == {('old', 5): 70}


def test_mixed_paybacks_replay_in_order_without_negative_shares():
    """An old payback consumes only debt remaining after the earlier named allocation."""
    entries = [bill('old', 1, {3: 50}), bill('new', 1, {3: 100})]
    named = row_payback('new', cents=80)
    old = dict(from_id=3, to_id=1, cents=60, currency='SGD')
    assert open_bill_shares(entries, {}, [named, old]) == {('new', 3): 10}
    assert open_bill_shares(entries, {}, [named, old, old]) == {}


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
    assert 'J family pays Ben SGD 2.00' in trip_payment_lines(entries, names, families, 'SGD')
    assert sum(unit_balances(entries, families).net['SGD'].values()) == 0


def test_three_family_person_cycle_cancels():
    """A cycle of actual people cancels even when each belongs to a different family."""
    families = {1: 'T', 2: 'J', 3: 'M'}
    entries = [entry(1, {2: 100}), entry(2, {3: 100}), entry(3, {1: 100})]
    assert trip_payments(entries, families) == []
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
    report = '\n'.join(trip_payback_lines(entries, {1: 'Ann', 2: 'Joe', 3: 'Max'},
                                          families, 'SGD', settlements=[gift]))
    assert f'Max paid Ann SGD {paid / 100:.2f} on behalf of J family' in report


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
        'SGD', '', 'T family is settled', '', '2 payments to settle']


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
    # Person 2 can pay 185 of person 1's debt to person 3 along the existing family
    # line. That removes their internal payment without changing the family's total.
    assert [p for _, p in trip_payments(entries, families)] == [
        Payment(('family', 'T'), ('person', 3), 6958)]


def test_no_splits_and_missing_payers():
    assert unit_balances([entry(None, {}), entry(1, None)], {}).net == {}
    orphan = entry(None, {1: 500, 2: 500})
    result = unit_balances([orphan, entry(1, {2: 100})], {})
    assert result.net == {'SGD': {('person', 1): 100, ('person', 2): -100}}
    assert result.no_payer == (orphan,)
    assert trip_balance_lines([], {}, {}) == [NO_SPLITS_LINE]
    assert trip_balance_lines([orphan], {}, {}) == ['SGD', '', '', 'Everyone is settled.', '', NO_PAYER_LINE.format(
        count=1, entries='entry has', they='it is')]
    assert trip_balance_lines([orphan, orphan], {}, {}) == ['SGD', '', '', 'Everyone is settled.', '', NO_PAYER_LINE.format(
        count=2, entries='entries have', they='they are')]


def test_unit_is_family_or_person_without_label():
    assert unit_of(1, {1: 'T'}) == ('family', 'T')
    for families in ({}, {1: None}, {1: ''}):
        assert unit_of(1, families) == ('person', 1)
    assert unit_of(1, {1: '1'}) != unit_of(1, {})


def test_exact_lines_group_credits_but_pay_people():
    entries = [entry(2, {3: 70000, 4: 10000}), entry(1, {3: 10000})]
    names, families = {1: 'Alice', 2: 'Bob', 3: 'Carol', 4: 'Erin'}, {1: 'T', 2: 'T', 3: 'U'}
    assert trip_balance_lines(entries, names, families) == [
        'SGD', '', 'T family is owed SGD 900.00', 'U family owes SGD 800.00',
        'Erin owes SGD 100.00', '', '3 payments to settle']
    assert trip_payment_lines(entries, names, families, 'SGD') == [
        'U family pays Bob SGD 700.00', 'U family pays Alice SGD 100.00', 'Erin pays Bob SGD 100.00']


def test_owing_family_lists_total_and_lone_creditor_has_name():
    entries = [entry(1, {3: 3000}), entry(3, {1: 10000})]
    assert trip_balance_lines(entries, {1: 'Alice', 2: 'Bob', 3: 'Carol'}, {1: 'T', 2: 'T'}) == [
        'SGD', '', 'T family owes SGD 70.00', 'Carol is owed SGD 70.00', '', '1 payment to settle']


def test_currency_order_settled_currency_single_member_family_and_warning():
    entries = [entry(1, {2: 1000}), entry(2, {1: 1000}),
               entry(3, {1: 500}, 'AUD'), entry(None, {2: 100}, 'AUD')]
    assert trip_balance_lines(entries, {1: 'Alice'}, {3: 'Cedar Grove'}) == [
        'AUD · 1 of 2', '', 'Cedar Grove is owed AUD 5.00', 'Alice owes AUD 5.00',
        '', '1 payment to settle', '',
        NO_PAYER_LINE.format(count=1, entries='entry has', they='it is')]
    assert trip_balance_lines(entries, {}, {}, currency='SGD')[:4] == [
        'SGD · 2 of 2', '', '', 'Everyone is settled.']


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
    assert params == {'trip_id': 7} and 't.trip_id IS NOT DISTINCT FROM :trip_id' in sql
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
    assert any('1 payback' in line for line in report)
    assert any('Everyone is settled.' in line for line in report) == (paid == 5000)


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
    names = {1: 'Alice', 2: 'Bob'}
    assert trip_payment_lines(entries, names, families, 'IDR', settlements) == ['T family pays Bob IDR 200.00']
    assert trip_payment_lines(entries, names, families, 'SGD', settlements) == ['T family pays Bob SGD 100.00']
    lines = trip_payback_lines(entries, names, families, 'IDR', settlements)
    assert '• Alice paid Bob IDR 600.00 (as SGD 0.50), 27 May' in lines
    same = dict(settlements[0], paid_currency='IDR', paid_cents=60000)
    assert '(as ' not in '\n'.join(trip_payback_lines(entries, {}, families, 'IDR', [same]))



# --- ADR-0051: debts per person, then rolled up to families. ---

TAN_LEE = {1: 'Tan', 2: 'Tan', 3: 'Lee', 4: 'Lee'}
TAN_LEE_NAMES = {1: 'Alice', 2: 'Bob', 3: 'Carol', 4: 'Dave'}


def test_adr_0051_example():
    """Alice paid 400 and Bob paid 200, each shared by all four. Bob's 100 for Alice's bill
    and Alice's 50 for Bob's net to 50; the Lees shortcut that amount to Alice."""
    entries = [entry(1, dict.fromkeys((1, 2, 3, 4), 10000)),
               entry(2, dict.fromkeys((1, 2, 3, 4), 5000))]
    result = unit_balances(entries, TAN_LEE)
    assert result.net['SGD'] == {('person', 1): 25000, ('person', 2): 5000,
                                 ('person', 3): -15000, ('person', 4): -15000}
    assert trip_balance_lines(entries, TAN_LEE_NAMES, TAN_LEE) == [
        'SGD', '', 'Lee family owes SGD 300.00', 'Tan family is owed SGD 300.00',
        '', '2 payments to settle']
    assert trip_payment_lines(entries, TAN_LEE_NAMES, TAN_LEE, 'SGD') == [
        'Lee family pays Alice SGD 250.00', 'Lee family pays Bob SGD 50.00',
        '💡 Lee family pays Alice the SGD 50.00 Bob owes, and pays Bob SGD 50.00 less.']



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


@pytest.mark.parametrize('giver,note', [(3, ' on behalf of Bob'), (4, ' on behalf of Bob'), (2, '')])
def test_payback_history_names_the_line_it_cleared(giver, note):
    """Bob owes his relative Alice. Carol (also family) or Max (outside) paying it is noted
    "on behalf of Bob", not "on behalf of Tan family"; Bob paying his own line has no note."""
    families = {1: 'Tan', 2: 'Tan', 3: 'Tan', 4: 'J'}
    names = {1: 'Alice', 2: 'Bob', 3: 'Carol', 4: 'Max'}
    payback = dict(from_id=giver, to_id=1, for_id=2 if giver != 2 else None, cents=3000, currency='SGD')
    report = trip_payback_lines([entry(1, {2: 3000})], names, families, 'SGD', settlements=[payback])
    assert f'• {names[giver]} paid Alice SGD 30.00{note}' in report

# --- #129: simplify per person before rolling up. ---

SIMPLIFY_NAMES = {1: 'Alice', 2: 'Bob', 3: 'Tom', 4: 'Tia'}
SIMPLIFY_FAMILIES = {3: 'Tan', 4: 'Tan'}


def tan_bills():
    """Bob and both Tans owe Alice; both Tans also owe Bob, in AUD cents."""
    return [bill('alice', 1, {2: 6000, 3: 22500, 4: 22500}, 'AUD'),
            bill('bob', 2, {3: 16500, 4: 16500}, 'AUD')]


def test_simplified_tan_report_and_notes():
    """The family takes over Bob's debt, with an explanation even in the short report."""
    entries = tan_bills()
    result = unit_balances(entries, SIMPLIFY_FAMILIES)
    assert result.lines == {'AUD': {(('family', 'Tan'), 1): 51000,
                                    (('family', 'Tan'), 2): 27000}}
    assert result.shortcuts == {'AUD': ((('family', 'Tan'), 1, ('person', 2),
                                         ('family', 'Tan'), 2, 6000),)}
    report = trip_payment_lines(entries, SIMPLIFY_NAMES, SIMPLIFY_FAMILIES, 'AUD')
    assert report == [
        'Tan family pays Alice AUD 510.00', 'Tan family pays Bob AUD 270.00',
        '💡 Tan family pays Alice the AUD 60.00 Bob owes, and pays Bob AUD 60.00 less.']
    assert open_bill_shares(entries, SIMPLIFY_FAMILIES) == {
        ('alice', 2): 6000, ('alice', 3): 22500, ('alice', 4): 22500,
        ('bob', 3): 16500, ('bob', 4): 16500}


def test_shortcut_of_inside_family_debt():
    """Dave's own debt to Carol can be paid along the Ngs' existing line to her."""
    entries = [entry(1, {2: 5000, 3: 6000, 4: 6000}, 'AUD'),
               entry(2, {3: 4000, 4: 4000}, 'AUD')]
    families = {1: 'Lee', 2: 'Lee', 3: 'Ng', 4: 'Ng'}
    report = trip_payment_lines(entries, {1: 'Carol', 2: 'Dave', 3: 'Ian', 4: 'Jo'}, families, 'AUD')
    assert report == [
        'Ng family pays Carol AUD 170.00', 'Ng family pays Dave AUD 30.00',
        '💡 Ng family pays Carol the AUD 50.00 Dave owes, and pays Dave AUD 50.00 less.']


def test_shortcut_never_adds_a_destination():
    """Erin cannot pay Carol when she only owes Dave."""
    result = unit_balances([entry(1, {2: 4000}, 'AUD'), entry(2, {3: 2500}, 'AUD')], {})
    assert result.lines == {'AUD': {(('person', 2), 1): 4000, (('person', 3), 2): 2500}}
    assert result.shortcuts == {}


def test_paying_suggested_family_amount_clears_it():
    """A rowless Mark paid payback makes a person cycle which must cancel completely."""
    paid = [dict(from_id=3, to_id=1, cents=51000, currency='AUD')]
    result = unit_balances(tan_bills(), SIMPLIFY_FAMILIES, paid)
    assert result.lines == {'AUD': {(('family', 'Tan'), 2): 27000}}
    assert ('person', 1) not in result.net['AUD']
    assert result.shortcuts == {}
    assert trip_payments(tan_bills(), SIMPLIFY_FAMILIES, paid) == [
        ('AUD', Payment(('family', 'Tan'), ('person', 2), 27000))]


def person_net(owed):
    """Compute signed person totals independently of the simplifier and roll-up."""
    result = {}
    for (u, c), cents in owed.items():
        result[u] = result.get(u, 0) - cents
        result[c] = result.get(c, 0) + cents
    return {pid: cents for pid, cents in result.items() if cents}


@pytest.mark.parametrize('budget', [1, 20000])
def test_simplification_preserves_people_lines_currencies_and_order(monkeypatch, budget):
    """Check invariants after paybacks as well as family-only destinations and cycles."""
    monkeypatch.setattr(balances, 'SIMPLIFY_BUDGET', budget)
    shapes = [
        (tan_bills(), SIMPLIFY_FAMILIES, []),
        (tan_bills(), SIMPLIFY_FAMILIES,
         [dict(from_id=3, to_id=1, cents=51000, currency='AUD')]),
        ([entry(1, {2: 5000, 3: 6000, 4: 6000}, 'AUD'),
          entry(2, {3: 4000, 4: 4000}, 'AUD')],
         {1: 'Lee', 2: 'Lee', 3: 'Ng', 4: 'Ng'}, []),
        ([entry(1, {2: 4000}, 'AUD'), entry(2, {3: 2500}, 'AUD')], {}, []),
        # Tom has no debt to Alice, but Tia provides the existing Tan-to-Alice line.
        ([entry(1, {2: 60, 4: 20}, 'AUD'), entry(2, {3: 100}, 'AUD')],
         SIMPLIFY_FAMILIES, []),
        ([entry(1, {2: 80}, 'AUD'), entry(2, {3: 60}, 'AUD'),
          entry(3, {1: 40}, 'AUD')], {}, []),
        (tan_bills() + [entry(3, {1: 100}, 'SGD')], SIMPLIFY_FAMILIES, []),
    ]
    for entries, families, paid in shapes:
        original, _, _ = balances._fold_debts(entries, families, paid)
        result = unit_balances(entries, families, paid)
        assert result == unit_balances(entries, families, paid)
        assert result == unit_balances(list(reversed(entries)), families, paid)
        for currency, owed in original.items():
            simplified, _ = balances._simplify_debts(owed, families)
            assert person_net(simplified) == person_net(owed)
            assert result.net[currency] == {('person', p): x for p, x in person_net(owed).items()}
            assert set(result.lines[currency]) <= balances._rolled_keys(owed, families)
            assert all(isinstance(x, int) and x > 0 for x in simplified.values())


def test_family_destination_can_use_a_relatives_line():
    """A new person edge is allowed only because its family already pays that creditor."""
    owed = {(2, 1): 60, (4, 1): 20, (3, 2): 100}
    simplified, notes = balances._simplify_debts(owed, SIMPLIFY_FAMILIES)
    assert simplified == {(4, 1): 20, (3, 1): 60, (3, 2): 40}
    assert notes == ((('family', 'Tan'), 1, ('person', 2), ('family', 'Tan'), 2, 60),)


def test_notes_name_a_different_incoming_unit():
    """Hide invisible moves; distinguish a personal payer from their incoming family."""
    families = {2: 'Tan', 3: 'Tan'}
    entries = [entry(1, {2: 60, 3: 20}, 'AUD'), entry(2, {3: 100}, 'AUD')]
    report = trip_payment_lines(entries, SIMPLIFY_NAMES, families, 'AUD')
    # Both outgoing debts roll to the same line, so the invisible move has no note.
    assert not any(line.startswith('💡') for line in report)
    families = {1: 'Tan', 3: 'Tan'}
    report = trip_payment_lines(entries, SIMPLIFY_NAMES, families, 'AUD')
    assert ('💡 Tom pays Alice the AUD 0.60 Bob owes, and Tan family pays Bob AUD 0.60 less.'
            in report)


def test_search_beats_greedy_on_small_debt_shapes():
    """The id-tied first shortcut leaves four payments; the other leaves three."""
    owed = {(3, 1): 200, (3, 2): 100, (4, 1): 100, (4, 2): 100, (4, 3): 100}
    assert balances._shortcut_moves(owed, {}) == [(100, 3, 1, 4), (100, 3, 2, 4)]
    greedy = dict(owed)
    while moves := balances._shortcut_moves(greedy, {}):
        x, p, c, u = moves[0]
        for edge in ((u, p), (p, c)):
            greedy[edge] -= x
            if not greedy[edge]:
                del greedy[edge]
        greedy[(u, c)] += x
    best, _ = balances._simplify_debts(owed, {})
    assert len(greedy) == 4
    assert best == {(3, 1): 200, (4, 1): 100, (4, 2): 200}
    assert person_net(best) == person_net(owed)
    # These are the only two first moves and both lead to terminal states, so three
    # is the minimum reachable under the full-amount shortcut rule.
    assert balances._shortcut_moves(best, {}) == []
    assert len(best) == 3


def test_family_cycle_without_person_cycle_stays():
    """Relatives cannot trade balances merely because their rolled families form a loop."""
    families = {1: 'Tan', 2: 'Tan', 3: 'Lee', 4: 'Lee', 5: 'Ng', 6: 'Ng'}
    owed = {(1, 3): 100, (4, 5): 100, (6, 2): 100}
    assert balances._simplify_debts(owed, families) == (owed, ())


# --- #145: bill provenance and balanced offsets on shortened lines. ---

def dinner_taxi():
    """Erin owes Alice directly and through Bob; Frank is Erin's relative."""
    return [bill('dinner', 1, {2: 4000, 5: 6000}), bill('taxi', 2, {5: 4000})]


@pytest.mark.parametrize('giver', [5, 6])
@pytest.mark.parametrize('amount', [5000, 8000, 10000, 12000])
def test_shortened_dinner_line_clears_bills_for_either_relative(giver, amount):
    families = {5: 'Ng', 6: 'Ng'}
    entries = dinner_taxi()
    parts = balances.line_parts(entries, families, [], 'SGD', ('family', 'Ng'), 1)
    rows = balances.payment_rows(parts, giver, amount)
    paid = [dict(from_id=giver, to_id=1, cents=amount, currency='SGD', bills=rows)]
    lines = unit_balances(entries, families, paid).lines['SGD']
    if amount < 10000:
        assert lines == {(('family', 'Ng'), 1): 10000 - amount}
    elif amount == 10000:
        assert lines == {}
    else:
        assert lines == {(('person', 1), giver): amount - 10000}
    # Erin's chain through Bob is paid before her direct 60, so a part payment leaves her
    # direct debt, and the rest stays one Ngs-to-Alice line (see payment_rows).
    if amount < 10000:
        assert {(r['transaction_id'], r['for_id']): r['cents'] for r in rows} == {
            ('taxi', 5): 4000, ('dinner', 2): 4000, ('dinner', 5): amount - 4000}
    if amount >= 10000:
        assert open_bill_shares(entries, families, paid) == {}


@pytest.mark.parametrize('giver', [3, 4])
def test_tans_pay_shortcut_with_offsets(giver):
    entries = tan_bills()
    parts = balances.line_parts(entries, SIMPLIFY_FAMILIES, [], 'AUD', ('family', 'Tan'), 1)
    rows = balances.payment_rows(parts, giver, 51000)
    paid = [dict(from_id=giver, to_id=1, cents=51000, currency='AUD', bills=rows)]
    assert unit_balances(entries, SIMPLIFY_FAMILIES, paid).lines == {
        'AUD': {(('family', 'Tan'), 2): 27000}}
    assert ('alice', 2) not in open_bill_shares(entries, SIMPLIFY_FAMILIES, paid)
    # A part payment clears the giver's own pieces first (their chain through Bob, if the
    # search gave them one), never their relative's.
    own = balances.payment_rows(parts, giver, 1000)
    assert {r['for_id'] for r in own} <= {giver, 2}
    assert sum(r['cents'] for r in own if r['payer_id'] == 1) == 1000


def test_line_parts_falls_back_for_normal_line_and_unbacked_hop():
    assert balances.line_parts([bill('dinner', 1, {5: 100})], {}, [],
                               'SGD', ('person', 5), 1) is None
    # Bob's overpayment to Erin creates Erin -> Bob with no bill behind it.
    entries = [bill('dinner', 1, {2: 40, 5: 60})]
    paid = [dict(from_id=2, to_id=5, cents=40, currency='SGD')]
    assert unit_balances(entries, {}, paid).lines['SGD'] == {(('person', 5), 1): 100}
    assert balances.line_parts(entries, {}, paid, 'SGD', ('person', 5), 1) is None


@pytest.mark.parametrize('families', [{}, {2: 'Ng', 5: 'Ng', 6: 'Ng'}])
def test_long_chain_and_middle_in_paying_family(families):
    # Erin -> Carol -> Bob -> Alice, with existing destinations for each shortcut.
    entries = [bill('dinner', 1, {2: 40, 3: 1, 5: 60}),
               bill('taxi', 2, {3: 40}), bill('lunch', 3, {5: 41})]
    unit = balances.debtor_unit(5, 1, families)
    parts = balances.line_parts(entries, families, [], 'SGD', unit, 1)
    assert any(len(part.hops) == 3 for part in parts)
    rows = balances.payment_rows(parts, 5, 101)
    paid = [dict(from_id=5, to_id=1, cents=101, currency='SGD', bills=rows)]
    assert unit_balances(entries, families, paid).lines['SGD'] == {}
    assert open_bill_shares(entries, families, paid) == {}


@pytest.mark.parametrize('failure', ['share', 'balance', 'debt', 'duplicate'])
def test_offsets_rejected_together_against_pre_payment_state(failure):
    entries = dinner_taxi()
    rows = [dict(transaction_id='dinner', for_id=2, cents=4000),
            dict(transaction_id='taxi', for_id=5, cents=4000)]
    if failure == 'share':
        rows[1]['cents'] = 4001
    elif failure == 'balance':
        rows[0]['cents'] = 3999
    elif failure == 'debt':
        entries.append(bill('return', 5, {2: 1}))
    else:
        rows.append(dict(rows[1]))
    paid = [dict(from_id=6, to_id=1, cents=4000, currency='SGD', bills=rows)]
    debts, _, _ = balances._fold_debts(entries, {}, paid)
    assert debts['SGD'][(1, 6)] == 4000
    assert debts['SGD'][(2, 1)] == 4000
    assert open_bill_shares(entries, {}, paid) == open_bill_shares(entries, {})


def test_payment_rows_splits_oldest_hop_rows_and_merges_shared_bills():
    entries = [bill('old', 1, {2: 10, 5: 60}), bill('new', 1, {2: 30}),
               bill('taxi', 2, {5: 40})]
    parts = balances.line_parts(entries, {}, [], 'SGD', ('person', 5), 1)
    # 25 pays part of Erin's chain first: Bob's 10 on the old bill, then 15 of the new.
    rows = balances.payment_rows(parts, 5, 25)
    assert {(r['transaction_id'], r['for_id']): r['cents'] for r in rows} == {
        ('taxi', 5): 25, ('old', 2): 10, ('new', 2): 15}
    rows = balances.payment_rows(parts, 5, 85)
    assert {(r['transaction_id'], r['for_id']): r['cents'] for r in rows} == {
        ('taxi', 5): 40, ('old', 2): 10, ('new', 2): 30, ('old', 5): 45}
    # Two pieces can end at the same bill share, which must be inserted only once.
    part = balances.LinePart(5, 10, ((('dinner', 5, 1, 10),),))
    assert balances.payment_rows((part, part), 5, 20) == [
        dict(transaction_id='dinner', for_id=5, payer_id=1, cents=20)]


@pytest.mark.parametrize('budget', [1, balances.SIMPLIFY_BUDGET])
def test_provenance_replay_matches_search_on_seeded_trips(monkeypatch, budget):
    import random
    rng = random.Random(51)
    monkeypatch.setattr(balances, 'SIMPLIFY_BUDGET', budget)
    for _ in range(200):
        people = list(range(1, rng.randint(3, 10)))
        families = {pid: rng.choice('ABC') for pid in people if rng.random() < 0.8}
        entries = []
        for number in range(rng.randint(1, 6)):
            payer = rng.choice(people)
            sharers = rng.sample(people, rng.randint(1, len(people)))
            entries.append(bill(number, payer, {pid: rng.randint(1, 50000) for pid in sharers}))
        paid = []
        for _ in range(rng.randint(0, 3)):
            giver, receiver = rng.sample(people, 2)
            paid.append(dict(from_id=giver, to_id=receiver, cents=rng.randint(1, 50000), currency='SGD'))
        debts, _, _ = balances._fold_debts(entries, families, paid)
        for owed in debts.values():
            pieces = balances._debt_pieces(owed, families)
            expected, _ = balances._simplify_debts(owed, families)
            assert {edge: sum(cents for _, cents in parts) for edge, parts in pieces.items()} == expected
            for (u, c), parts in pieces.items():
                for path, cents in parts:
                    assert cents > 0 and path[0] == u and path[-1] == c
                    assert len(path) == len(set(path))
                    assert all(edge in owed for edge in zip(path, path[1:]))


def test_invalid_offset_keeps_unrelated_direct_rows_on_capped_path():
    entries = dinner_taxi()
    rows = [dict(transaction_id='dinner', for_id=5, cents=7000),
            dict(transaction_id='dinner', for_id=2, cents=4000),
            dict(transaction_id='taxi', for_id=5, cents=4001)]
    paid = [dict(from_id=6, to_id=1, cents=11000, currency='SGD', bills=rows)]
    debts, _, _ = balances._fold_debts(entries, {}, paid)
    assert debts['SGD'] == {(2, 1): 4000, (5, 2): 4000, (1, 6): 5000}
    assert open_bill_shares(entries, {}, paid) == {('dinner', 2): 4000, ('taxi', 5): 4000}


def test_payment_piece_order_uses_member_total_then_id():
    # Erin's two small pieces together outrank Carol; equal totals choose Bob first.
    parts = tuple(balances.LinePart(member, cents, (((name, member, 1, cents),),))
                  for name, member, cents in [('erin-old', 5, 30), ('carol', 3, 50),
                                               ('bob', 2, 60), ('erin-new', 5, 30)])
    assert [row['transaction_id'] for row in balances.payment_rows(parts, 6, 180)] == [
        'bob', 'erin-old', 'erin-new', 'carol']
    assert [row['transaction_id'] for row in balances.payment_rows(parts, 3, 100)] == [
        'carol', 'bob']


# --- ADR-0052 (2026-11-20): a deleted bill behind an offset row breaks its chain. ---

def saved_dinner_taxi_payback():
    """Erin paid Alice 100 on the shortened line; rows as the reader returns them."""
    entries = dinner_taxi()
    parts = balances.line_parts(entries, {}, [], 'SGD', ('person', 5), 1)
    payer = {'dinner': 1, 'taxi': 2}
    rows = [dict(transaction_id=r['transaction_id'], for_id=r['for_id'], cents=r['cents'],
                 payer_id=payer[r['transaction_id']])
            for r in balances.payment_rows(parts, 5, 10000)]
    return entries, [dict(from_id=5, to_id=1, cents=10000, currency='SGD', bills=rows)]


def test_deleted_offset_bill_leaves_middle_owing_and_money_owed_back():
    # Bob's taxi is deleted after the payback. Erin only owed 60, so Bob's 40 must stay
    # open and Alice owes Erin the 40 back; before, everyone read as settled.
    entries, paid = saved_dinner_taxi_payback()
    dinner_only = [e for e in entries if e['transaction_id'] != 'taxi']
    assert unit_balances(dinner_only, {}, paid).lines == {
        'SGD': {(('person', 2), 1): 4000, (('person', 1), 5): 4000}}
    assert open_bill_shares(dinner_only, {}, paid) == {('dinner', 2): 4000}
    # Undoing the delete brings the taxi back, and the chain counts again.
    assert unit_balances(entries, {}, paid).lines == {'SGD': {}}


def test_deleted_receiver_bill_still_clears_nothing_only():
    # A plain row on a deleted bill the receiver paid is not an offset: it clears nothing,
    # and the payback's other rows still count, as before this rule.
    entries = [bill('dinner', 1, {5: 6000}), bill('lunch', 1, {6: 3000})]
    rows = [dict(transaction_id='dinner', for_id=5, cents=6000, payer_id=1),
            dict(transaction_id='lunch', for_id=6, cents=3000, payer_id=1)]
    paid = [dict(from_id=5, to_id=1, cents=9000, currency='SGD', bills=rows)]
    lunch_deleted = entries[:1]
    assert unit_balances(lunch_deleted, {}, paid).lines == {'SGD': {(('person', 1), 5): 3000}}
    assert open_bill_shares(lunch_deleted, {}, paid) == {}
