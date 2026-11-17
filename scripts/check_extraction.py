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
from app.services.participants import Participant, mentions_paying  # noqa: E402 - load_dotenv() first
from app.services.splits import resolve_split, sender_shares  # noqa: E402 - load_dotenv() first

# The payment methods offered to the model. Fictional, and fixed here rather than loaded from the
# database: this script must never touch the database (#53), and the messages below test amounts
# and dates, not which card was chosen.
FICTIONAL_ACCOUNTS = ["Card A", "Card B", "Cash", "YouTrip"]

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


SPLIT_CASES = [
    {"message": "Dinner with everyone 80", "kind": "none", "outcome": "no split"},
    {"message": "Dinner 480 cash, split evenly between all", "kind": "everyone", "include_sender": True, "uneven": False, "payer_name": None},
    {"message": "Petrol 90, split between Alice's and Bob's families", "kind": "named", "include_sender": False, "people": {("alice", True), ("bob", True)}},
    {"message": "Airbnb 2300, split by nights: Alice's and Bob's families 5, Carol and Dave 4", "kind": "named", "uneven": True,
     "counts": {("alice", True, 5), ("bob", True, 5), ("carol", False, 4), ("dave", False, 4)}, "default_count": None},
    {"message": "Airbnb 2300 on my Visa, 5 nights, everyone except Carol's family who only stayed 4", "kind": "everyone",
     "default_count": 5, "counts": {("carol", True, 4)}, "except_people": set(), "count_unit": "nights"},
    {"message": "Hotel 600 split by nights, me 3 and Carol 2", "kind": "named", "include_sender": True,
     "counts": {("me", False, 3), ("carol", False, 2)}, "default_count": None},
    {"message": "Groceries 150, Carol's share is 50, rest split evenly", "uneven": True, "counts": set(), "default_count": None},
    {"message": "dinner 80 split with Carol", "kind": "named", "include_sender": True, "people": {("carol", False)}, "payer_name": None},
    {"message": "lunch 60 split between Carol and Dave", "kind": "named", "include_sender": False, "payer_name": None},
    {"message": "taxi 30 shared with Dave and Erin", "kind": "named", "include_sender": True},
    {"message": "drinks 120 split between everyone except Dave", "kind": "everyone", "except_people": {"dave"}},
    {"message": "Airbnb 900 split between everyone except Carol's family", "kind": "everyone", "except_people": {"carol"}, "uneven": False},
    {"message": "Carol covered dinner 90", "kind": "none", "payer_name": "carol"},
    {"message": "lunch 45, Dave's treat", "kind": "none", "payer_name": "dave"},
    {"message": "dinner 200 paid by Erin, split with Carol and Dave", "kind": "named", "include_sender": True, "payer_name": "erin"},
    {"message": "bought Carol a coffee 6", "kind": "none", "payer_name": None},
    {"message": "gift for Dave 40", "kind": "none", "payer_name": None},
    {"message": "Carol's birthday cake 55", "kind": "none", "payer_name": None},
    {"message": "dinner with Carol 70", "kind": "none", "payer_name": None},
    {"message": "lunch 20 I paid", "kind": "none", "payer_name": None},
    {"message": "groceries 55 Card A", "kind": "none", "payer_name": None},
]

# Every case checks the final sharers. Bob has his own family so mistakenly including
# the whole trip is visible, and expanding Bob's family must also include Frank.
SPLIT_PEOPLE = tuple(Participant(i, name, None, None) for i, name in enumerate(
    ('Alice', 'Bob', 'Carol', 'Dave', 'Erin', 'Frank'), start=1))
# Two labels are nobody's name, as real trip labels often are, so a message can name a
# family by its label alone ("Reds and Blues 5 nights").
SPLIT_FAMILIES = {1: 'Alice', 2: 'Reds', 6: 'Reds', 3: 'Blues', 4: 'Blues'}
_EVEN_NAMES = [
    (), ('Alice', 'Bob', 'Carol', 'Dave', 'Erin', 'Frank'), ('Alice', 'Bob', 'Frank'),
    (), (), (), (), ('Alice', 'Carol'), ('Carol', 'Dave'), ('Alice', 'Dave', 'Erin'),
    ('Alice', 'Bob', 'Carol', 'Erin', 'Frank'), ('Alice', 'Bob', 'Erin', 'Frank'),
    (), (), ('Alice', 'Carol', 'Dave'), (), (), (), (), (), (),
]
for case, names in zip(SPLIT_CASES, _EVEN_NAMES, strict=True):
    case.setdefault('outcome', {(name, None) for name in names} if names else 'no split')
SPLIT_CASES[3]['outcome'] = {('Alice', 5), ('Bob', 5), ('Frank', 5), ('Carol', 4), ('Dave', 4)}
SPLIT_CASES[4]['outcome'] = {(p.display_name, 4 if p.id in (3, 4) else 5) for p in SPLIT_PEOPLE}
SPLIT_CASES[5]['outcome'] = {('Alice', 3), ('Carol', 2)}

# Cases the directory above would otherwise hide, each with its outcome stated outright.
SPLIT_CASES += [
    # Carol misread as a family would add Dave at 4 nights.
    {"message": "Airbnb 2300, split by nights: Alice's family 5, Carol and Erin 4",
     "outcome": {('Alice', 5), ('Carol', 4), ('Erin', 4)}},
    # The sender (Alice) isn't named, so she must not get a share.
    {"message": "Airbnb 2300, split by nights: Bob's family 5, Carol and Dave 4",
     "outcome": {('Bob', 5), ('Frank', 5), ('Carol', 4), ('Dave', 4)}},
    # A number that isn't a count must leave an even split even.
    {"message": "3 pizzas 60 split with Carol", "outcome": {('Alice', None), ('Carol', None)}},
    {"message": "dinner for 4, 120, split between Carol and Dave", "outcome": {('Carol', None), ('Dave', None)}},
    # A real message (2026-11-17), names swapped. Families named by label, the payer's "for 6
    # nights" is the bill's length, not her count, and the rest's 6 is in a later sentence.
    # Carol is a Blue, so she shares at 5.
    {"message": "Carol paid 2717.84 fir airbnb for 6 nights. Reds and Blues only staying 5 nights. "
                "Rest all stay 6 nights.",
     "outcome": {('Alice', 6), ('Bob', 5), ('Frank', 5), ('Carol', 5), ('Dave', 5), ('Erin', 6)}},
    # A rest word with no split word and nobody's count: never a whole-trip split.
    {"message": "Dinner with the others 80", "outcome": "no split"},
    # The /help examples for "the rest" (app/services/help_topics.py). Bob and Frank are the Reds.
    # Without "split by nights" the model dropped the Reds' 5 in 4 of 8 runs (2026-11-17), so
    # the help says it.
    {"message": "Airbnb 900 split by nights. Reds 5 nights, rest all 6 nights.",
     "outcome": {('Alice', 6), ('Bob', 5), ('Frank', 5), ('Carol', 6), ('Dave', 6), ('Erin', 6)}},
    {"message": "Carol paid 600 for her 4 nights. Rest all stay 6 nights.",
     "outcome": {('Alice', 6), ('Bob', 6), ('Frank', 6), ('Carol', 4), ('Dave', 6), ('Erin', 6)}},
]


def _name(value):
    """Ignore case and a trailing possessive when comparing the model's copied names."""
    if not value:
        return None
    # "Carol's family" and "Carol's" both compare as "carol", as match_person reads them.
    value = value.strip().lower().removesuffix(" families").removesuffix(" family")
    return value.removesuffix("'s").removesuffix("’s")


def _split_reading(transaction):
    """Raw diagnostics plus the resolved sharers/counts the card would actually show.

    run_once puts the original message in description. All split guards and count checks
    run in resolve_split, so this harness cannot accidentally use an older copy of them.
    """
    reference = dict(transaction.get("split_reference") or {})
    message = transaction.get("description") or ""
    resolved = resolve_split(reference, SPLIT_PEOPLE, SPLIT_PEOPLE[0], SPLIT_FAMILIES, True, message)
    from_words = sender_shares(message)
    if from_words is not None and reference.get("kind") == "named":
        reference["include_sender"] = from_words
    return {
        **reference,
        "outcome": {(p.display_name, resolved.counts.get(p.id)) for p in resolved.people} or 'no split',
        "people": {(_name(p["name"]), p["is_family"]) for p in reference.get("people", [])},
        "counts": {(_name(p["name"]), p["is_family"], p["count"])
                   for p in reference.get("people", []) if p.get("count") is not None},
        "default_count": reference.get("default_count"),
        "except_people": {_name(p["name"]) for p in reference.get("except_people", [])},
        # The bot ignores the model's payer unless the message has a paying word.
        "payer_name": _name(transaction.get("payer_name")) if mentions_paying(message) else None,
    }


async def run_once(message: str, semaphore: asyncio.Semaphore):
    """Return the extracted entry with its resolved date, or an empty dict on failure."""
    # The semaphore caps how many requests are in flight at once, so a big --runs doesn't
    # fire dozens of simultaneous calls at the API.
    async with semaphore:
        result = await extract_transactions(message, TODAY, FICTIONAL_ACCOUNTS)
    if not result or not result.get("transactions"):
        return {}
    txn = result["transactions"][0]
    # The same two steps process_expense_text takes: the raw message becomes the description,
    # then the date is resolved from the reference plus that text.
    txn["description"] = message
    apply_date_resolution(txn, TODAY)
    return txn


async def main(runs: int) -> int:
    """Check old amount/date cases and split readings with the same all-runs exit rule."""
    semaphore = asyncio.Semaphore(10)
    # Every run of every message, all started together and awaited as one batch.
    jobs = [run_once(message, semaphore) for message, _, _ in CASES for _ in range(runs)]
    jobs += [run_once(case["message"], semaphore) for case in SPLIT_CASES for _ in range(runs)]
    results = await asyncio.gather(*jobs)

    all_right = True
    for i, (message, want_amount, want_date) in enumerate(CASES):
        wrong = []
        for transaction in results[i * runs:(i + 1) * runs]:
            got_amount = transaction.get("amount")
            got_date = date.fromisoformat(transaction["date"]) if transaction.get("date") else None
            amount_ok = got_amount == want_amount
            date_ok = want_date is None or got_date == want_date
            reading = _split_reading(transaction)
            if not (amount_ok and date_ok and reading['outcome'] == 'no split' and reading["payer_name"] is None):
                wrong.append(f"amount={got_amount} date={got_date} kind={reading.get('kind')} payer={reading['payer_name']}")
        right = runs - len(wrong)
        all_right = all_right and not wrong
        mark = "ok  " if not wrong else "FAIL"
        print(f"{mark} {right}/{runs}  {message!r}  (want amount={want_amount} date={want_date})")
        # Show each distinct wrong answer once, with how often it came up.
        for answer in sorted(set(wrong)):
            print(f"           got {answer}  x{wrong.count(answer)}")
    for i, case in enumerate(SPLIT_CASES, start=len(CASES)):
        expected = {key: value for key, value in case.items() if key not in ("message", "outcome")}
        wrong = []
        info = []
        for transaction in results[i * runs:(i + 1) * runs]:
            reading = _split_reading(transaction)
            actual = {key: reading.get(key) for key in expected}
            # The payer is checked on its own: a right split outcome must not hide a wrong
            # payer ("Carol covered dinner 90" is "no split" whoever the model says paid).
            payer_wrong = 'payer_name' in expected and reading.get('payer_name') != expected['payer_name']
            if not transaction or reading['outcome'] != case['outcome'] or payer_wrong:
                wrong.append(f"{reading['outcome']} payer={reading.get('payer_name')}")
            elif actual != expected:
                info.append(str(actual))
        all_right = all_right and not wrong
        mark = "ok  " if not wrong else "FAIL"
        print(f"{mark} {runs - len(wrong)}/{runs}  {case['message']!r}  (want {case['outcome']})")
        for answer in sorted(set(wrong)):
            print(f"           got {answer}  x{wrong.count(answer)}")
        for answer in sorted(set(info)):
            print(f"           info: raw fields {answer}  x{info.count(answer)}")
    return 0 if all_right else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5, help="runs per message (default 5)")
    args = parser.parse_args()
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY isn't set - add it to .env.")
    sys.exit(asyncio.run(main(args.runs)))
