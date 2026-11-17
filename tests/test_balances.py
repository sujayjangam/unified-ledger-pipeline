"""Trip balances: whole-cent sums over made-up entries, with fictional ids only.

Ids used here: 1 Alice, 2 Bob, 3 Carol, 4 Dave, 5 Erin.
"""

import pytest

from app.services.balances import Payment, by_family, net_balances, suggest_payments, unit_of
from app.services.splits import share_out


def entry(paid_by, splits, currency='SGD'):
    return {'currency': currency, 'paid_by': paid_by, 'splits': splits}


def apply_payments(net, payments):
    """What the balances look like after every suggested payment has been made."""
    after = dict(net)
    for payment in payments:
        after[payment.from_unit] += payment.cents
        after[payment.to_unit] -= payment.cents
    return after


def test_payer_is_owed_the_other_shares():
    # Dinner 90.00, Alice paid, three ways: Alice is owed 60, the other two owe 30 each.
    result = net_balances([entry(1, {1: 3000, 2: 3000, 3: 3000})])
    assert result.net == {'SGD': {1: 6000, 2: -3000, 3: -3000}}
    assert result.no_payer == ()


def test_payer_outside_the_split_is_owed_everything():
    # Alice paid for Bob and Carol only: her own share is nothing, so she is owed the lot.
    result = net_balances([entry(1, {2: 2500, 3: 2500})])
    assert result.net == {'SGD': {1: 5000, 2: -2500, 3: -2500}}


def test_entries_add_up_and_can_cancel():
    # Alice pays 60 for both, then Bob pays 60 for both: nobody owes anybody.
    result = net_balances([entry(1, {1: 3000, 2: 3000}), entry(2, {1: 3000, 2: 3000})])
    assert result.net == {'SGD': {1: 0, 2: 0}}


def test_unsplit_entry_moves_nothing():
    assert net_balances([entry(1, {}), entry(1, None)]).net == {}


def test_currencies_are_kept_apart():
    result = net_balances([entry(1, {1: 1000, 2: 1000}), entry(2, {1: 700, 2: 700}, 'AUD')])
    assert result.net == {'SGD': {1: 1000, 2: -1000}, 'AUD': {1: -700, 2: 700}}


def test_split_entry_without_payer_is_handed_back_not_counted():
    orphan = entry(None, {1: 500, 2: 500})
    result = net_balances([orphan, entry(1, {1: 100, 2: 100})])
    assert result.net == {'SGD': {1: 100, 2: -100}}
    assert result.no_payer == (orphan,)


def test_every_currency_nets_to_zero_with_uneven_cents():
    # 5.55 three ways and 100.00 by nights (5, 5, 4): awkward cents, shared out by share_out.
    entries = [entry(1, share_out(555, {1: 1, 2: 1, 3: 1})),
               entry(3, share_out(10000, {1: 5, 2: 5, 3: 4})),
               entry(2, share_out(1001, {2: 1, 4: 1}), 'AUD')]
    for per_person in net_balances(entries).net.values():
        assert sum(per_person.values()) == 0


def test_unit_is_the_family_or_the_person_alone():
    families = {1: 'Tan', 2: 'Tan', 3: 'Lee'}
    assert unit_of(1, families) == ('family', 'Tan')
    assert unit_of(4, families) == ('person', 4)   # no family row on this trip


def test_family_balance_is_its_members_added_up():
    # Alice and Bob are one family: what Bob owes Alice cancels inside the family.
    net = {1: 6000, 2: -3000, 3: -2000, 4: -1000}
    assert by_family(net, {1: 'Tan', 2: 'Tan', 3: 'Lee'}) == {
        ('family', 'Tan'): 3000, ('family', 'Lee'): -2000, ('person', 4): -1000}


def test_largest_debtor_pays_largest_creditor():
    net = {'Tan': 5000, 'Lee': 1000, 'Ong': -4000, 'Wee': -2000}
    assert suggest_payments(net) == [
        Payment('Ong', 'Tan', 4000),   # biggest debt to biggest credit
        Payment('Wee', 'Lee', 1000),   # Wee (2000) now owes the most; Lee and Tan tie at 1000
        Payment('Wee', 'Tan', 1000),
    ]


def test_payments_clear_every_balance():
    net = {1: 12345, 2: -5000, 3: -7000, 4: 655, 5: -1000}
    payments = suggest_payments(net)
    assert all(payment.cents > 0 for payment in payments)
    assert len(payments) <= len(net) - 1
    assert set(apply_payments(net, payments).values()) == {0}


def test_settled_units_get_no_payment():
    assert suggest_payments({}) == []
    assert suggest_payments({'Tan': 0, 'Lee': 0}) == []


def test_same_balances_always_give_the_same_payments():
    # A tie on both sides: the unit that sorts first goes first, whatever order the dict is in.
    one = suggest_payments({'B': -100, 'A': -100, 'D': 100, 'C': 100})
    two = suggest_payments({'C': 100, 'D': 100, 'A': -100, 'B': -100})
    assert one == two == [Payment('A', 'C', 100), Payment('B', 'D', 100)]


def test_balances_that_do_not_add_up_are_refused():
    with pytest.raises(ValueError):
        suggest_payments({'Tan': 500, 'Lee': -400})


def test_whole_trip_from_entries_to_family_payments():
    families = {1: 'Tan', 2: 'Tan', 3: 'Lee', 4: 'Lee', 5: 'Ong'}
    entries = [entry(1, share_out(50000, {1: 1, 2: 1, 3: 1, 4: 1, 5: 1})),   # Alice: villa 500
               entry(3, share_out(9000, {3: 1, 5: 1})),                        # Carol: petrol 90
               entry(5, share_out(12000, {1: 1, 2: 1, 3: 1, 4: 1}))]          # Erin: dinner 120
    units = by_family(net_balances(entries).net['SGD'], families)
    assert units == {('family', 'Tan'): 24000, ('family', 'Lee'): -21500, ('family', 'Ong'): -2500}
    payments = suggest_payments(units)
    assert payments == [Payment(('family', 'Lee'), ('family', 'Tan'), 21500),
                        Payment(('family', 'Ong'), ('family', 'Tan'), 2500)]
    assert set(apply_payments(units, payments).values()) == {0}


# --- the query that feeds net_balances (no database: the connection is a fake) ---

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
    result = net_balances(entries)
    assert result.net == {'AUD': {1: 3000, 2: -3000}} and len(result.no_payer) == 1
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


# --- trip_balance_lines: the words people read ---

def test_balance_lines_for_a_family_and_a_person_alone():
    from app.services.balances import trip_balance_lines
    # Dinner 90.00, Alice paid, three ways; then 140.00, Carol paid, shared 5 to 2 with Alice.
    # Alice +60 -100 = -40, Bob -30, Carol -30 +100 = +70. Alice and Bob are one family.
    entries = [entry(1, {1: 3000, 2: 3000, 3: 3000}), entry(3, {1: 10000, 3: 4000})]
    assert trip_balance_lines(entries, {1: 'Alice', 2: 'Bob', 3: 'Carol'}, {1: 'Tan', 2: 'Tan'}) == [
        'SGD',
        'Tan family owes 70.00',
        '    Alice owes 40.00',
        '    Bob owes 30.00',
        'Carol is owed 70.00',
        'To settle:',
        ' • Tan family pays Carol 70.00',
    ]


def test_balance_lines_for_two_currencies_one_person_families_and_a_missing_payer():
    from app.services.balances import trip_balance_lines
    entries = [entry(1, {1: 1000, 2: 1000}, 'SGD'),      # Alice paid; Bob owes her 10
               entry(2, {1: 1000, 2: 1000}, 'SGD'),      # Bob paid; all square again
               entry(3, {1: 500, 3: 500}, 'AUD'),        # Carol paid; Alice owes her 5
               entry(None, {1: 100, 2: 100}, 'AUD')]     # nobody recorded as payer
    # Carol is a family of one: no member line under it. Id 2 has no name on file.
    assert trip_balance_lines(entries, {1: 'Alice', 3: 'Carol'}, {3: 'Lee'}) == [
        'AUD',
        'Lee family is owed 5.00',
        'Alice owes 5.00',
        'To settle:',
        ' • Alice pays Lee family 5.00',
        '',
        'SGD',
        'Alice is settled',
        'Person #2 is settled',
        'Everyone is settled.',
        '',
        '⚠️ 1 split entry has no payer recorded, so it is not counted.',
    ]
    assert trip_balance_lines([], {}, {}) == ['No split entries on this trip yet.']


def test_family_label_of_several_words_is_shown_as_stored():
    from app.services.balances import trip_balance_lines
    lines = trip_balance_lines([entry(1, {1: 1000, 3: 1000})], {1: 'Alice', 2: 'Bob', 3: 'Carol'},
                               {1: 'Alice and Bob', 3: 'Lee'})
    assert lines == ['SGD', 'Alice and Bob is owed 10.00', 'Lee family owes 10.00',
                     'To settle:', ' • Lee family pays Alice and Bob 10.00']
