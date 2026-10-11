"""Checks the extraction prompt against the real model, by hand (#77, #15).

The test suite can't do this: tests/ never makes a network call, so every test that touches
extraction fakes the model's answer. That proves the code handles an answer correctly, but
not that the model gives the right answer. This script fills that gap. It sends a fixed list
of messages to GPT-4o-mini through the real extract_transactions, several times each since
the model isn't deterministic, and reports how often each one came back right.

Run it after any change to the prompt or the schema descriptions in
app/services/extraction.py, from the repo root:

    python -m scripts.check_extraction            # 5 runs per message
    python -m scripts.check_extraction --runs 10

It calls OpenAI with OPENAI_API_KEY from .env, so each full run costs a little - well under
a cent at 5 runs of each message. It never touches the database.

Exit code 0 means every run of every message was right; 1 means at least one wasn't.
"""

import argparse
import asyncio
import os
import sys
from datetime import date, timedelta

from dotenv import load_dotenv

# Must run before extraction is imported: that module creates its OpenAI client at import
# time, reading OPENAI_API_KEY from the environment.
load_dotenv()

from app.services.dates import apply_date_resolution  # noqa: E402 - load_dotenv() first
from app.services.extraction import extract_transactions  # noqa: E402 - load_dotenv() first

# A fixed "today", so the expected dates below never change. 2026-09-23 is a Wednesday -
# the same day tests/test_dates.py uses.
TODAY = date(2026, 9, 23)
YESTERDAY = TODAY - timedelta(days=1)

# Unpacked as (message, expected amount, expected date).
# The date is checked after apply_date_resolution, i.e. the date the card would show, rather
# than the raw date_reference - that's the result the user actually sees, and it includes the
# "last week" check that dates.py makes on the message text itself.
# None for the date means "don't check it".
CASES = [
    # Short typed messages with a bare-number price (#77)
    ("Lunch 15 yday", 15, YESTERDAY),
    ("lunch 15 yday", 15, YESTERDAY),
    ("Coffee 5 yday", 5, YESTERDAY),
    ("lunch 15", 15, TODAY),
    ("Lunch 15.50 yday", 15.5, YESTERDAY),
    ("Grab 12.80 ytd", 12.8, YESTERDAY),
    # Spoken style, as a Whisper transcript reads
    ("I spent fifteen dollars on lunch yesterday", 15, YESTERDAY),
    ("50 ringgit for dinner", 50, TODAY),
    # Relative and calendar dates (#15). On Wednesday 23 Sep, "last Tuesday" is 22 Sep and
    # "Tuesday last week" is 15 Sep.
    ("grab 12.80 day before yesterday", 12.8, date(2026, 9, 21)),
    ("coffee 5 last tues", 5, date(2026, 9, 22)),
    ("Coffee 5 tues last week", 5, date(2026, 9, 15)),
    ("lunch 10 last monday", 10, date(2026, 9, 21)),
    ("Last week Tuesday dinner 40", 40, date(2026, 9, 15)),
    ("dinner 40 on 3/7", 40, date(2026, 7, 3)),  # day first: 3 July, not 7 March
    ("taxi 18 on 12 Sep", 18, date(2026, 9, 12)),
    # No price at all: the amount must stay null - "never guess an amount" still holds
    ("lunch yesterday", None, None),
    ("hello", None, None),
]


async def run_once(message: str, semaphore: asyncio.Semaphore):
    """One extraction. Returns (amount, resolved date) - (None, None) if nothing came back."""
    # The semaphore caps how many requests are in flight at once, so a big --runs doesn't
    # fire dozens of simultaneous calls at the API.
    async with semaphore:
        result = await extract_transactions(message, TODAY)
    if not result or not result.get("transactions"):
        return None, None
    txn = result["transactions"][0]
    # The same two steps process_expense_text takes: the raw message becomes the description,
    # then the date is resolved from the reference plus that text.
    txn["description"] = message
    apply_date_resolution(txn, TODAY)
    return txn.get("amount"), date.fromisoformat(txn["date"])


async def main(runs: int) -> int:
    semaphore = asyncio.Semaphore(10)
    # Every run of every message, all started together and awaited as one batch.
    jobs = [run_once(message, semaphore) for message, _, _ in CASES for _ in range(runs)]
    results = await asyncio.gather(*jobs)

    all_right = True
    for i, (message, want_amount, want_date) in enumerate(CASES):
        wrong = []
        for got_amount, got_date in results[i * runs:(i + 1) * runs]:
            amount_ok = got_amount == want_amount
            date_ok = want_date is None or got_date == want_date
            if not (amount_ok and date_ok):
                wrong.append(f"amount={got_amount} date={got_date}")
        right = runs - len(wrong)
        all_right = all_right and not wrong
        mark = "ok  " if not wrong else "FAIL"
        print(f"{mark} {right}/{runs}  {message!r}  (want amount={want_amount} date={want_date})")
        # Show each distinct wrong answer once, with how often it came up.
        for answer in sorted(set(wrong)):
            print(f"           got {answer}  x{wrong.count(answer)}")
    return 0 if all_right else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5, help="runs per message (default 5)")
    args = parser.parse_args()
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY isn't set - add it to .env.")
    sys.exit(asyncio.run(main(args.runs)))
