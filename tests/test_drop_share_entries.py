"""drop_share_entries: a share the model read as a second expense is folded back (#112)."""
import pytest

from app.services.splits import drop_share_entries


def _t(amount, currency="SGD"):
    return {"amount": amount, "currency": currency}


SHARE_MSG = "I paid 135 split with Bob. Bob's split is 90"


def test_share_read_as_second_expense_is_dropped():
    first, share = _t(135.0), _t(90.0)
    assert drop_share_entries([first, share], SHARE_MSG) == [first]


@pytest.mark.parametrize("message, total, share", [
    ("Groceries 150, Carol's share is 50", 150.0, 50.0),
    ("Dinner 120 split with Carol, Carol owes 40.50", 120.0, 40.5),
    ("Hotel 300, Dave's portion was $100", 300.0, 100.0),
])
def test_other_share_phrases(message, total, share):
    assert drop_share_entries([_t(total), _t(share)], message) == [_t(total)]


@pytest.mark.parametrize("message, entries", [
    # Real separate expenses: no share phrase, so both stay (the guard then refuses them today).
    ("Dinner 80 split with Carol. Taxi 20.", [_t(80.0), _t(20.0)]),
    ("coffee 5 and lunch 12", [_t(5.0), _t(12.0)]),
    # A share phrase, but the second amount isn't its number.
    ("Dinner 135, Bob's split is 90. Taxi 20", [_t(135.0), _t(20.0)]),
    # The share number, but not smaller than the first entry.
    ("Taxi 20. Lunch 90, Bob's share is 90", [_t(20.0), _t(90.0)]),
    # A percentage is never an amount.
    ("Dinner 100, Carol pays 30%. Coffee 30", [_t(100.0), _t(30.0)]),
    # Different currency from the first entry.
    ("Dinner 135 split with Bob. Bob's split is 90", [_t(135.0), _t(90.0, "MYR")]),
])
def test_real_expenses_are_kept(message, entries):
    assert drop_share_entries(entries, message) == entries


def test_only_the_share_goes_when_there_is_also_a_real_expense():
    first, share, taxi = _t(135.0), _t(90.0), _t(20.0)
    assert drop_share_entries([first, share, taxi], SHARE_MSG + ". Taxi 20") == [first, taxi]


@pytest.mark.parametrize("entries", [
    [],
    [_t(135.0)],
    [_t(None), _t(90.0)],      # first has no amount: nothing to compare against
    [_t(135.0), _t(None)],     # a missing amount is never a share
])
def test_edge_cases_unchanged(entries):
    assert drop_share_entries(entries, SHARE_MSG) == entries


def test_input_list_not_changed():
    entries = [_t(135.0), _t(90.0)]
    drop_share_entries(entries, SHARE_MSG)
    assert len(entries) == 2
