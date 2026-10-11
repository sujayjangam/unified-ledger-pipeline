"""Tests for turning a date reference into a date (#15, #75, ADR-0030).

No network and no database: the resolver is plain Python, and the one test that runs
process_expense_text fakes the model call and the clock.
"""

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.bot_core as bot_core
from app.services.dates import apply_date_resolution, mentions_last_week, resolve_date_reference
from app.services.extraction import DateReference
from app.services.utils import SG_TZ

# fixed "today" for testing, so that the expected answers will always be the same
# regardless of which day the suite runs. 2026-09-23 is a Wednesday.

TODAY = date(2026, 9, 23)


def model_ref(**fields):
    """Builds a date_reference exactly as it reaches the resolver from the model.

    The model's answer is parsed into DateReference and then turned into a plain dict by
    model_dump(mode='json') in extract_transactions - so doing the same here gives the real
    shape, every field present and the unused ones None, rather than a hand-typed guess at it.
    """
    return DateReference(**fields).model_dump(mode="json")


def test_model_ref_has_every_field_with_the_unused_ones_null():
    # Not a test of the resolver - a record of what its input looks like. For "yday", this
    # is the dict resolve_date_reference receives.
    assert model_ref(kind="days_ago", days_ago=1) == {
        "kind": "days_ago", "days_ago": 1, "weekday": None,
        "day": None, "month": None, "year": None,
    }


# --- unstated, today, days_ago -------------------------------------------------------------

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


# parametrize runs the test below once per row, passing that row's values in as the
# arguments - one row per case instead of one near-identical function per case. Each row
# shows up separately in `pytest -v`, so a failure names the exact case that broke.
@pytest.mark.parametrize("days_ago, expected", [
    (0, date(2026, 9, 23)),  # "today", phrased as zero days ago
    (2, date(2026, 9, 21)),  # "day before yesterday"
    (30, date(2026, 8, 24)),  # crosses a month boundary
])
def test_days_ago_counts_back_from_today(days_ago, expected):
    assert resolve_date_reference(model_ref(kind="days_ago", days_ago=days_ago), TODAY) == expected


def test_negative_days_ago_returns_none():
    # "-1 days ago" would be tomorrow - the model misread something, so no date.
    assert resolve_date_reference(model_ref(kind="days_ago", days_ago=-1), TODAY) is None


# --- weekday: "Tuesday" and "last Tuesday" -------------------------------------------------

@pytest.mark.parametrize("weekday, expected", [
    ("Tuesday", date(2026, 9, 22)),    # the day before today
    ("Monday", date(2026, 9, 21)),
    ("Sunday", date(2026, 9, 20)),
    ("Thursday", date(2026, 9, 17)),   # later in the week than today: last week's Thursday
    ("Wednesday", date(2026, 9, 16)),  # today's own weekday means a week ago, never today
])
def test_weekday_is_the_most_recent_one_before_today(weekday, expected):
    # Same result whether the user said "Tuesday" or "last Tuesday": the model returns the
    # same reference for both, and neither message says "last week".
    ref = model_ref(kind="weekday", weekday=weekday)

    assert resolve_date_reference(ref, TODAY) == expected


# --- weekday: "Tuesday last week" -----------------------------------------------------------

# last_week isn't in what the model returns - apply_date_resolution adds it from the message
# text (mentions_last_week) - so these tests add it to the reference the same way.

# Today's week runs Mon 21 - Sun 27 Sep, so "last week" is Mon 14 - Sun 20 Sep.
@pytest.mark.parametrize("weekday, expected", [
    ("Monday", date(2026, 9, 14)),
    ("Tuesday", date(2026, 9, 15)),
    ("Wednesday", date(2026, 9, 16)),
    ("Sunday", date(2026, 9, 20)),
])
def test_weekday_last_week_is_that_day_in_the_previous_monday_to_sunday_week(weekday, expected):
    ref = {**model_ref(kind="weekday", weekday=weekday), "last_week": True}

    assert resolve_date_reference(ref, TODAY) == expected


def test_tuesday_last_week_and_last_tuesday_can_differ():
    # Said on a Thursday, "last Tuesday" is two days ago, in this week, while "Tuesday last
    # week" is nine days ago - which is why the model has to tell the two apart.
    thursday = date(2026, 9, 24)

    last_tuesday = {**model_ref(kind="weekday", weekday="Tuesday"), "last_week": False}
    tuesday_last_week = {**model_ref(kind="weekday", weekday="Tuesday"), "last_week": True}

    assert resolve_date_reference(last_tuesday, thursday) == date(2026, 9, 22)
    assert resolve_date_reference(tuesday_last_week, thursday) == date(2026, 9, 15)


@pytest.mark.parametrize("text, expected", [
    ("coffee 5 tues last week", True),
    ("Last week Tuesday dinner 40", True),   # capitalised by the phone keyboard
    ("grab 9 mon last wk", True),            # abbreviated
    ("lunch 12 previous week friday", True),
    ("coffee 5 last tues", False),           # "last" is about the day, not the week
    ("lunch 10 last monday", False),
    ("brunch 30 last weekend", False),       # "weekend" isn't "week"
    ("lunch 15", False),
    ("", False),
])
def test_mentions_last_week_only_matches_the_phrase_last_week(text, expected):
    assert mentions_last_week(text) is expected


def test_a_weekday_that_isnt_a_real_day_returns_none():
    # The schema stops "Tues" reaching here from the model, so this is a plain dict - the
    # resolver must still not crash on it.
    assert resolve_date_reference({"kind": "weekday", "weekday": "Tues"}, TODAY) is None


# --- calendar: a day and month, maybe a year -----------------------------------------------

@pytest.mark.parametrize("day, month, expected", [
    (3, 7, date(2026, 7, 3)),     # "3/7" - earlier this year
    (23, 9, date(2026, 9, 23)),   # today's own date, with no year, is today
    (22, 9, date(2026, 9, 22)),   # yesterday
    (24, 9, date(2025, 9, 24)),   # tomorrow's date hasn't come round yet: last year's
    (25, 12, date(2025, 12, 25)), # the year rollover: "25 Dec" in September is last December
])
def test_calendar_date_without_a_year_is_its_most_recent_past_occurrence(day, month, expected):
    ref = model_ref(kind="calendar", day=day, month=month)

    assert resolve_date_reference(ref, TODAY) == expected


def test_calendar_date_with_a_year_uses_that_year():
    ref = model_ref(kind="calendar", day=3, month=7, year=2025)

    assert resolve_date_reference(ref, TODAY) == date(2025, 7, 3)


def test_29_february_in_a_leap_year_is_a_real_date():
    ref = model_ref(kind="calendar", day=29, month=2, year=2024)

    assert resolve_date_reference(ref, TODAY) == date(2024, 2, 29)


@pytest.mark.parametrize("day, month, year", [
    (29, 2, None),   # no year, so this year - and 2026 has no 29 February
    (29, 2, 2025),   # a stated year without one
    (31, 4, None),   # April has 30 days
    (3, 13, None),   # there is no month 13
])
def test_a_date_that_doesnt_exist_returns_none(day, month, year):
    ref = {"kind": "calendar", "day": day, "month": month, "year": year}

    assert resolve_date_reference(ref, TODAY) is None


# --- a reference missing what its kind needs ------------------------------------------------

@pytest.mark.parametrize("ref", [
    {"kind": "days_ago"},                                # no days_ago key at all
    model_ref(kind="days_ago"),                          # days_ago present but null
    model_ref(kind="weekday"),                           # no weekday named
    model_ref(kind="calendar", day=3),                   # no month
    {},                                                  # no kind
])
def test_a_reference_missing_a_field_its_kind_needs_returns_none(ref):
    assert resolve_date_reference(ref, TODAY) is None


# --- apply_date_resolution: setting the date on the transaction ----------------------------

def test_apply_sets_the_date_as_a_yyyy_mm_dd_string():
    txn = {"amount": 5.5, "date_reference": model_ref(kind="days_ago", days_ago=1)}

    apply_date_resolution(txn, TODAY)

    # A string, not a date object: the card prints it and add_expense checks this format.
    assert txn["date"] == "2026-09-22"


@pytest.mark.parametrize("text, expected", [
    ("coffee 5 last tues", date(2026, 9, 22)),        # the most recent Tuesday
    ("coffee 5 tues last week", date(2026, 9, 15)),   # Tuesday in the previous week
])
def test_apply_reads_last_week_from_the_message_text(text, expected):
    # The model returns the same reference for both messages - only the text differs.
    txn = {"description": text, "date_reference": model_ref(kind="weekday", weekday="Tuesday")}

    apply_date_resolution(txn, TODAY)

    assert txn["date"] == expected.isoformat()


def test_apply_falls_back_to_today_when_the_date_cant_be_worked_out():
    txn = {"date_reference": model_ref(kind="calendar", day=29, month=2)}

    apply_date_resolution(txn, TODAY)

    assert txn["date"] == "2026-09-23"


def test_apply_falls_back_to_today_when_there_is_no_date_reference():
    txn = {"amount": 5.5}

    apply_date_resolution(txn, TODAY)

    assert txn["date"] == "2026-09-23"


# --- the whole pipeline: the card shows the resolved date ----------------------------------

async def test_the_card_shows_yesterdays_date_for_yday(monkeypatch):
    # process_expense_text end to end, minus the network: the clock, the model call and the
    # duplicate check are faked, and the card message is a mock.
    monkeypatch.setattr(bot_core, "ALLOWED_TG_IDS", {"111111": "Alice"})
    monkeypatch.setattr(bot_core, "ACCOUNT_OWNERS", {"Alice": ["Card A"]})
    monkeypatch.setattr(bot_core, "find_recent_duplicate", MagicMock(return_value=None))
    # 10am on TODAY, Singapore time. bot_core imported get_sgt_now by name, so it is
    # patched on bot_core - patching it in utils wouldn't reach the copy bot_core holds.
    monkeypatch.setattr(bot_core, "get_sgt_now", lambda: datetime(2026, 9, 23, 10, 0, tzinfo=SG_TZ))
    extract = AsyncMock(return_value={"transactions": [{
        "amount": 12.5, "currency": "SGD", "category": "Food", "transaction_type": "Expense",
        "payment_method": None, "date_reference": model_ref(kind="days_ago", days_ago=1),
    }]})
    monkeypatch.setattr(bot_core, "extract_transactions", extract)

    update = MagicMock()
    update.effective_user.id = 111111
    status_msg = MagicMock()
    status_msg.message_id = 500
    status_msg.edit_text = AsyncMock()
    context = MagicMock()
    context.user_data = {}

    await bot_core.process_expense_text(update, context, "lunch 12.50 yday", status_msg)

    # The model was given the same "today" the date was counted back from.
    extract.assert_awaited_once_with("lunch 12.50 yday", TODAY)
    # The card shows yesterday, and the entry waiting for Confirm carries it too.
    assert "2026-09-22" in status_msg.edit_text.await_args.args[0]
    assert context.user_data["pending_cards"][500]["date"] == "2026-09-22"
