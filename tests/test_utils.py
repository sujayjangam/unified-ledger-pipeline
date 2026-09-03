from datetime import datetime

from app.services.utils import get_sgt_now, get_week_start, get_month_start

def test_get_sgt_now_is_timezone_aware_at_utc_plus_8():
    now = get_sgt_now()
    assert now.tzinfo is not None
    assert now.utcoffset().total_seconds() == 8 * 60 * 60

# get_week_start/get_month_start take `now` as a plain argument rather than calling
# get_sgt_now() internally - that's what lets these tests use a fixed, known date instead
# of depending on whatever the real wall clock happens to read when the suite runs.

def test_get_week_start_returns_monday_for_a_midweek_date():
    wednesday = datetime(2026, 9, 2)  # a Wednesday
    assert get_week_start(wednesday) == datetime(2026, 8, 31)  # the preceding Monday

def test_get_week_start_returns_same_day_when_now_is_monday():
    # Boundary case: weekday() == 0 means timedelta(days=0), so "start of week" should be
    # today itself, not last Monday.
    monday = datetime(2026, 8, 31)
    assert get_week_start(monday) == monday

def test_get_month_start_returns_first_of_month_for_a_midmonth_date():
    midmonth = datetime(2026, 9, 17)
    assert get_month_start(midmonth) == datetime(2026, 9, 1)

def test_get_month_start_returns_same_day_when_now_is_already_the_first():
    # Boundary case: .replace(day=1) on a date that's already the 1st should be a no-op.
    first = datetime(2026, 9, 1)
    assert get_month_start(first) == first
