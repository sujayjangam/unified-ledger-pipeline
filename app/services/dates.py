"""Turns what the user said about a date into the date itself (#15, #75, ADR-0030).

The model reads the message and returns a `date_reference` - what was said ("yesterday",
"last Tuesday", "3/7") - but never calculates a date. The calculation happens here, in plain
Python with no network or database, so every rule below is covered by tests/test_dates.py
with a fixed "today".
"""

import re
from datetime import date, timedelta

from app.services.utils import get_week_start

# Same order as date.weekday() counts: Monday is 0, Sunday is 6. So a day's position in this
# list is the number date.weekday() would give for it, which is what lets the two be compared.
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# "last week" / "previous week", also abbreviated to "wk". The \b on each side means "a whole
# word only", so "weekend" or "weekly" don't match. re.IGNORECASE because phone keyboards
# capitalise freely ("Last week Tuesday").
_LAST_WEEK = re.compile(r"\b(last|previous|prev)\s+(week|wk)\b", re.IGNORECASE)


def mentions_last_week(text: str) -> bool:
    """True if the message says "last week" (or "previous week", "last wk").

    This is what separates "Tuesday last week" (that day in the previous Monday-Sunday week)
    from "last Tuesday" (the most recent Tuesday). The model couldn't tell the two apart -
    it read both as last week - but the difference is one literal phrase, so it is checked
    here on the raw message instead. See the note in DateReference in extraction.py.
    """
    return bool(_LAST_WEEK.search(text or ""))


def resolve_date_reference(ref, today: date):
    ''' ref is a dictionary that is returned by DateReference in extraction.py
    The model outputs a dictionary where "kind" is the key to figure out the date
    of transaction.

    Returns the date, or None if it can't be worked out - a field the kind needs is
    missing, or the date doesn't exist (29 February in a year without one). The caller
    decides what None means; apply_date_resolution below falls back to today. '''
    try:
        if ref["kind"] == "unstated":
            return today

        if ref["kind"] == "today":
            return today

        if ref["kind"] == "days_ago":
            # A negative number would put the date in the future, which "N days ago" never
            # means - treat it as the model misreading the message, not as a date.
            if ref["days_ago"] < 0:
                return None
            return today - timedelta(days=ref["days_ago"])

        if ref["kind"] == "weekday":
            return _resolve_weekday(ref, today)

        if ref["kind"] == "calendar":
            return _resolve_calendar(ref, today)

    # KeyError: a field this kind needs is missing from the dict.
    # TypeError: a field is there but null (e.g. days_ago is None, so `None < 0` fails).
    # ValueError: the date doesn't exist (date(2026, 2, 29)) or the weekday isn't a real one.
    # OverflowError: a number so large it runs off the calendar (days_ago of a billion).
    # All of them mean "couldn't work it out", which is what None already says.
    except (KeyError, TypeError, ValueError, OverflowError):
        return None

    # if none of the above conditions matched, then return None
    return None


def _resolve_weekday(ref, today: date) -> date:
    """A named day: 'Tuesday', 'last Tuesday', or 'Tuesday last week'.

    Raises ValueError if the name isn't a real weekday (resolve_date_reference catches it).
    """
    # .index() raises ValueError for a name not in the list, e.g. "Tues".
    target = WEEKDAYS.index(ref["weekday"])

    # "Tuesday last week": that day inside the previous Monday-to-Sunday week - the same week
    # boundary /week uses, so the bot never disagrees with itself about what "this week" is.
    # last_week isn't something the model returns: apply_date_resolution adds it from
    # mentions_last_week. .get() because a ref built any other way may not have it.
    if ref.get("last_week"):
        start_of_last_week = get_week_start(today) - timedelta(weeks=1)
        return start_of_last_week + timedelta(days=target)

    # "Tuesday" or "last Tuesday": the most recent Tuesday *before* today.
    # Subtracting the two day numbers gives how far back the day is, but goes negative when
    # the named day comes later in the week than today (Thursday, said on a Wednesday:
    # 2 - 3 = -1). `% 7` wraps that round to the same day in the previous week (-1 -> 6 days
    # back). A result of 0 means the named day IS today; the rule says that means a week
    # ago, never today, so `or 7` turns 0 into 7.
    days_back = (today.weekday() - target) % 7 or 7
    return today - timedelta(days=days_back)


def _resolve_calendar(ref, today: date) -> date:
    """A day and month, with a year only if the user said one: '3rd of July', '12 Sep', '3/7'.

    Raises ValueError for a date that doesn't exist (resolve_date_reference catches it).
    """
    day, month, year = ref["day"], ref["month"], ref.get("year")

    if year is None:
        # No year said: the most recent time this day and month came round, today included.
        # Comparing (month, day) pairs works because Python compares tuples left to right:
        # month first, and day only when the months are equal. So "12 Sep" said on 23 Sep is
        # this year, while "25 Dec" said on 23 Sep hasn't happened yet this year - last year.
        # Picking the year this way, before building the date, also means 29 February said
        # in a non-leap year fails below instead of silently moving to another year.
        if (month, day) <= (today.month, today.day):
            year = today.year
        else:
            year = today.year - 1

    # date() raises ValueError for a day that doesn't exist: 31 April, or 29 Feb 2026.
    return date(year, month, day)


def apply_date_resolution(transaction: dict, today: date) -> dict:
    """Sets transaction['date'] from its date_reference, mutating and returning it in place.

    Called from process_expense_text, so voice and typed entries are resolved the same way.
    Standalone like apply_payment_defaults, so it can be tested on a plain dict.

    'date' is stored as a 'YYYY-MM-DD' string because that is what the card shows and what
    add_expense validates. A reference that can't be worked out falls back to today, the
    same date every entry got before #15 - so the worst case is no worse than before.
    """
    # `or {}` covers a transaction with no date_reference at all; {} then fails the
    # ref["kind"] lookup and resolves to None, i.e. today.
    # A copy, with last_week added from the message text - the one part of the reference the
    # model can't read reliably (see mentions_last_week). 'description' is the raw message,
    # set by process_expense_text before this runs.
    ref = {**(transaction.get("date_reference") or {}),
           "last_week": mentions_last_week(transaction.get("description", ""))}
    resolved = resolve_date_reference(ref, today)
    transaction["date"] = (resolved or today).isoformat()
    return transaction
