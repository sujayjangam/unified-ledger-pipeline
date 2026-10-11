from datetime import date
from app.services.dates import resolve_date_reference

# fixed "today" for testing, so that the expected answers will always be the same
# regardless of which day the suite runs. 2026-09-23 is a Wednesday.

TODAY = date(2026, 9, 23)

def test_days_ago_is_yesterday():
    # set up: e.g. what the model returns for "lunch 12.50 yesterday"
    ref = {"kind": "days_ago", "days_ago": 1}

    # call the function to test
    result = resolve_date_reference(ref, TODAY)

    # check that the output date is one day earlier
    assert result == date(2026, 9, 22)

def test_kind_is_unstated():
    # set up: e.g. what the model returns for "lunch 12.50"
    ref = {"kind": "unstated"}

    # call the function to test
    result = resolve_date_reference(ref, TODAY)

    # check that the output date is TODAY
    assert result == date(2026, 9, 23)

def test_kind_is_today():
    # set up: e.g. what the model returns for "lunch 12.50 today"
    ref = {"kind": "today"}

    # call the function to test
    result = resolve_date_reference(ref, TODAY)

    # check that the output date is TODAY
    assert result == date(2026, 9, 23)

def test_unhandled_kind_returns_none():
    # set up: a kind this function doesn't handle
    ref = {"kind": "not_a_real_kind"}

    # call the function to test
    result = resolve_date_reference(ref, TODAY)

    # check that the output is None
    assert result is None