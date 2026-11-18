"""Checks the extraction prompt against the real model, by hand (#77, #15).

The test suite can't do this: tests/ never makes a network call, so every test that touches
extraction fakes the model's answer. That proves the code handles an answer correctly, but
not that the model gives the right answer. This script fills that gap. It sends a fixed list
of messages to GPT-4o-mini through the real extract_transactions and reports how often each
one came back right.

Extraction runs at temperature 0 (#120), so a message reads the same way nearly every time and
one run per message is the main result. A second run only finds the rare message sitting on a
near-tie, whose answer can still change. Calls are better spent on more different messages
than on repeats of the same one.

Run it after any change to the prompt or the schema descriptions in
app/services/extraction.py, from the repo root:

    python -m scripts.check_extraction                    # 1 run per message
    python -m scripts.check_extraction --runs 2           # also shows near-ties
    python -m scripts.check_extraction --only "for lunch"  # just the messages containing it

It calls OpenAI with OPENAI_API_KEY from .env, one call per message per run, and prints the
number of calls before it starts. Production uses the same prepaid credit, so only the user
runs it - never Claude or a worker (CLAUDE.md). It never touches the database.

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
from app.services.splits import (  # noqa: E402 - load_dotenv() first
    resolve_split, sender_shares, drop_share_entries, UNEVEN_EVEN_SPLIT_WARNING)

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
#
# Every case here must be able to fail in a way the bot would show (#120). A message whose
# result Python decides on its own can't, so it doesn't belong here - it belongs in tests/.
# Cut on 2026-11-18 for that reason or as a repeat: "lunch 15 yday" and "Coffee 5 yday" (same
# as "Lunch 15 yday"), "lunch 10 last monday" and "Last week Tuesday dinner 40" (same model
# reading as the weekday cases; the week rules are Python's, tested in tests/test_dates.py).
CASES = [
    # Short typed messages with a bare-number price (#77)
    ("Lunch 15 yday", 15, YESTERDAY),
    ("lunch 15", 15, TODAY),
    ("Lunch 15.50 yday", 15.5, YESTERDAY),
    ("Grab 12.80 ytd", 12.8, YESTERDAY),
    # Spoken style, as a Whisper transcript reads
    ("I spent fifteen dollars on lunch yesterday", 15, YESTERDAY),
    ("50 ringgit for dinner", 50, TODAY),
    # Trip currencies (#120): rupiah amounts are large, and "k" shorthand is likely abroad.
    ("Dinner 450k rupiah", 450000, TODAY),
    ("Taxi 85000 IDR", 85000, TODAY),
    # The card is the model's to pick from the list; the top-up category decides the wallet
    # and Transfer rules in resolve_payment. Nothing else here checks either.
    ("Lunch 12 on Card A", 12, TODAY),
    ("Topup YouTrip 200", 200, TODAY),
    # Price first, then a sentence about the food. The first came back with no amount on
    # production (2026-11-18, 3 of 32 runs); the second in 15 of 24 runs on an early #112 prompt.
    ("5 for lunch. Hainanese curry rice", 5, TODAY),
    ("8 for dinner. chicken rice and teh", 8, TODAY),
    # Relative and calendar dates (#15). On Wednesday 23 Sep, "last Tuesday" is 22 Sep and
    # "Tuesday last week" is 15 Sep.
    ("grab 12.80 day before yesterday", 12.8, date(2026, 9, 21)),
    ("coffee 5 last tues", 5, date(2026, 9, 22)),
    ("Coffee 5 tues last week", 5, date(2026, 9, 15)),
    ("dinner 40 on 3/7", 40, date(2026, 7, 3)),  # day first: 3 July, not 7 March
    ("taxi 18 on 12 Sep", 18, date(2026, 9, 12)),
    # No price at all: the amount must stay null - "never guess an amount" still holds
    ("lunch yesterday", None, None),
    ("hello", None, None),
]
# The currency is checked on every case with an amount: SGD unless listed here.
CURRENCIES = {"50 ringgit for dinner": "MYR", "Dinner 450k rupiah": "IDR", "Taxi 85000 IDR": "IDR"}
# Checked only for the cases listed: the card picked, and the category.
CARDS = {"Lunch 12 on Card A": "Card A"}
CATEGORIES = {"Topup YouTrip 200": "YouTrip top-up"}


# Each case also checks the amount ("amount"), which costs no extra call.
# Cut on 2026-11-18 because Python decides the whole checked result, so they could never fail:
# "bought Carol a coffee 6", "gift for Dave 40", "Carol's birthday cake 55", "dinner with Carol
# 70" and "groceries 55 Card A" (no split word, no paying word), "lunch 20 I paid" (read_payer
# reads it; the model's payer is never used) and "Dinner with the others 80" (needs a count
# for a name the message doesn't have).
SPLIT_CASES = [
    {"message": "Dinner with everyone 80", "amount": 80, "kind": "none", "outcome": "no split"},
    {"message": "Dinner 480 cash, split evenly between all", "amount": 480, "kind": "everyone", "include_sender": True, "payer_name": None},
    {"message": "Petrol 90, split between Alice's and Bob's families", "amount": 90, "kind": "named", "include_sender": False, "people": {("alice", True), ("bob", True)}},
    {"message": "Airbnb 2300, split by nights: Alice's and Bob's families 5, Carol and Dave 4", "amount": 2300, "kind": "named",
     "counts": {("alice", True, 5), ("bob", True, 5), ("carol", False, 4), ("dave", False, 4)}, "default_count": None},
    {"message": "Airbnb 2300 on my Visa, 5 nights, everyone except Carol's family who only stayed 4", "amount": 2300, "kind": "everyone",
     "default_count": 5, "counts": {("carol", True, 4)}, "except_people": set(), "count_unit": "nights"},
    {"message": "Hotel 600 split by nights, me 3 and Carol 2", "amount": 600, "kind": "named", "include_sender": True,
     "counts": {("me", False, 3), ("carol", False, 2)}, "default_count": None},
    {"message": "Groceries 150, Carol's share is 50, rest split evenly", "amount": 150, "counts": set(), "default_count": None},
    {"message": "dinner 80 split with Carol", "amount": 80, "kind": "named", "include_sender": True, "people": {("carol", False)}, "payer_name": None},
    {"message": "lunch 60 split between Carol and Dave", "amount": 60, "kind": "named", "include_sender": False, "payer_name": None},
    {"message": "taxi 30 shared with Dave and Erin", "amount": 30, "kind": "named", "include_sender": True},
    {"message": "drinks 120 split between everyone except Dave", "amount": 120, "kind": "everyone", "except_people": {"dave"}},
    {"message": "Airbnb 900 split between everyone except Carol's family", "amount": 900, "kind": "everyone", "except_people": {"carol"}},
    # Python can't read "covered" or "treat", so the model is the only reader of these payers.
    {"message": "Carol covered dinner 90", "amount": 90, "kind": "none", "payer_name": "carol"},
    {"message": "lunch 45, Dave's treat", "amount": 45, "kind": "none", "payer_name": "dave"},
    {"message": "dinner 200 paid by Erin, split with Carol and Dave", "amount": 200, "kind": "named", "include_sender": True, "payer_name": "erin"},
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
    # Index 6, "Carol's share is 50": names ticked evenly until Set amounts (ADR-0048).
    (), (), (), ('Alice', 'Carol'), ('Alice', 'Carol'), ('Carol', 'Dave'), ('Alice', 'Dave', 'Erin'),
    ('Alice', 'Bob', 'Carol', 'Erin', 'Frank'), ('Alice', 'Bob', 'Erin', 'Frank'),
    (), (), ('Alice', 'Carol', 'Dave'),
]
for case, names in zip(SPLIT_CASES, _EVEN_NAMES, strict=True):
    case.setdefault('outcome', {(name, None) for name in names} if names else 'no split')
SPLIT_CASES[3]['outcome'] = {('Alice', 5), ('Bob', 5), ('Frank', 5), ('Carol', 4), ('Dave', 4)}
SPLIT_CASES[4]['outcome'] = {(p.display_name, 4 if p.id in (3, 4) else 5) for p in SPLIT_PEOPLE}
SPLIT_CASES[5]['outcome'] = {('Alice', 3), ('Carol', 2)}

# Cases the directory above would otherwise hide, each with its outcome stated outright.
SPLIT_CASES += [
    # A fixed share read as two expenses on the test bot (2026-11-18), so the one-expense guard
    # refused it. Both names ticked evenly, the warning pointing to Set amounts (ADR-0048).
    # "Alice paid ..." was cut as a repeat of this one (#120).
    {"message": "I paid 135 split with Bob. Bob's split is 90", "amount": 135,
     "outcome": {('Alice', None), ('Bob', None)}, "warnings": (UNEVEN_EVEN_SPLIT_WARNING,)},
    # Carol misread as a family would add Dave at 4 nights.
    {"message": "Airbnb 2300, split by nights: Alice's family 5, Carol and Erin 4", "amount": 2300,
     "outcome": {('Alice', 5), ('Carol', 4), ('Erin', 4)}},
    # The sender (Alice) isn't named, so she must not get a share.
    {"message": "Airbnb 2300, split by nights: Bob's family 5, Carol and Dave 4", "amount": 2300,
     "outcome": {('Bob', 5), ('Frank', 5), ('Carol', 4), ('Dave', 4)}},
    # A number that isn't a count must leave an even split even.
    {"message": "3 pizzas 60 split with Carol", "amount": 60, "outcome": {('Alice', None), ('Carol', None)}},
    {"message": "dinner for 4, 120, split between Carol and Dave", "amount": 120,
     "outcome": {('Carol', None), ('Dave', None)}},
    # A real message (2026-11-17), names swapped. Families named by label, the payer's "for 6
    # nights" is the bill's length, not her count, and the rest's 6 is in a later sentence.
    # Carol is a Blue, so she shares at 5.
    {"message": "Carol paid 2717.84 fir airbnb for 6 nights. Reds and Blues only staying 5 nights. "
                "Rest all stay 6 nights.", "amount": 2717.84,
     "outcome": {('Alice', 6), ('Bob', 5), ('Frank', 5), ('Carol', 5), ('Dave', 5), ('Erin', 6)}},
    # "The rest" wordings; the first is a /help example (app/services/help_topics.py). Bob and Frank are the Reds.
    # Without "split by nights" the model dropped the Reds' 5 in 4 of 8 runs (2026-11-17), so
    # the help says it.
    {"message": "Airbnb 900 split by nights. Reds 5 nights, rest all 6 nights.", "amount": 900,
     "outcome": {('Alice', 6), ('Bob', 5), ('Frank', 5), ('Carol', 6), ('Dave', 6), ('Erin', 6)}},
    # Says the 600 is for everyone. It replaced "Carol paid 600 for her 4 nights. Rest all stay
    # 6 nights.", which could fairly mean the 600 was Carol's own stay (#120). "She stayed 4"
    # would not work: a count is read only beside the name, and "Carol paid" is a paying clause.
    {"message": "Carol paid 600 for all. Carol stayed 4 nights, rest all 6 nights", "amount": 600,
     "outcome": {('Alice', 6), ('Bob', 6), ('Frank', 6), ('Carol', 4), ('Dave', 6), ('Erin', 6)}},
]

# Messages with more than one real expense, and how many the model must return. The bot
# refuses these today (ADR-0010), but the share-sentence rule must not merge real expenses,
# or several expenses per message would break the day it is allowed.
MULTI_CASES = [
    ("coffee 5 and lunch 12", 2),
    ("Dinner 80 split with Carol. Taxi 20.", 2),
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
        "warnings": resolved.warnings,
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
    # The same first step process_expense_text takes: drop a share read as a second expense.
    transactions = drop_share_entries(result["transactions"], message)
    txn = transactions[0]
    # Only the first entry is read, so the count is kept: two entries make the bot refuse the
    # message, and must fail here too (2026-11-18).
    txn["_count"] = len(transactions)
    # The same two steps process_expense_text takes: the raw message becomes the description,
    # then the date is resolved from the reference plus that text.
    txn["description"] = message
    apply_date_resolution(txn, TODAY)
    return txn


def _only(text: str | None) -> None:
    """Keep only the cases whose message contains `text` (ignoring case), so one slipping case
    can be rerun - on this branch and on main - without paying for the whole list."""
    if not text:
        return
    def keep(message):
        return text.casefold() in message.casefold()
    CASES[:] = [c for c in CASES if keep(c[0])]
    SPLIT_CASES[:] = [c for c in SPLIT_CASES if keep(c["message"])]
    MULTI_CASES[:] = [c for c in MULTI_CASES if keep(c[0])]


async def main(runs: int) -> int:
    """Check old amount/date cases and split readings with the same all-runs exit rule."""
    messages = len(CASES) + len(SPLIT_CASES) + len(MULTI_CASES)
    print(f"{messages} messages x {runs} runs = {messages * runs} OpenAI calls\n")
    semaphore = asyncio.Semaphore(10)
    # Every run of every message, all started together and awaited as one batch.
    jobs = [run_once(message, semaphore) for message, _, _ in CASES for _ in range(runs)]
    jobs += [run_once(case["message"], semaphore) for case in SPLIT_CASES for _ in range(runs)]
    jobs += [run_once(message, semaphore) for message, _ in MULTI_CASES for _ in range(runs)]
    results = await asyncio.gather(*jobs)

    all_right = True
    for i, (message, want_amount, want_date) in enumerate(CASES):
        wrong = []
        for transaction in results[i * runs:(i + 1) * runs]:
            got_amount = transaction.get("amount")
            got_date = date.fromisoformat(transaction["date"]) if transaction.get("date") else None
            amount_ok = got_amount == want_amount
            date_ok = want_date is None or got_date == want_date
            # Only with a price: "hello" can come back with no entry at all.
            got_currency = transaction.get("currency")
            currency_ok = want_amount is None or got_currency == CURRENCIES.get(message, "SGD")
            card_ok = message not in CARDS or transaction.get("payment_method") == CARDS[message]
            category_ok = message not in CATEGORIES or transaction.get("category") == CATEGORIES[message]
            reading = _split_reading(transaction)
            # 0 is fine: "hello" comes back as an empty list, which the bot treats as no price.
            one = transaction.get("_count", 0) <= 1
            if not (one and amount_ok and date_ok and currency_ok and card_ok and category_ok
                    and reading['outcome'] == 'no split' and reading["payer_name"] is None):
                wrong.append(f"amount={got_amount} {got_currency} date={got_date} kind={reading.get('kind')} "
                             f"card={transaction.get('payment_method')} category={transaction.get('category')} "
                             f"payer={reading['payer_name']} expenses={transaction.get('_count')}")
        right = runs - len(wrong)
        all_right = all_right and not wrong
        mark = "ok  " if not wrong else "FAIL"
        print(f"{mark} {right}/{runs}  {message!r}  (want amount={want_amount} date={want_date})")
        # Show each distinct wrong answer once, with how often it came up.
        for answer in sorted(set(wrong)):
            print(f"           got {answer}  x{wrong.count(answer)}")
    for i, case in enumerate(SPLIT_CASES, start=len(CASES)):
        expected = {key: value for key, value in case.items() if key not in ("message", "outcome", "amount")}
        wrong = []
        info = []
        for transaction in results[i * runs:(i + 1) * runs]:
            reading = _split_reading(transaction)
            actual = {key: reading.get(key) for key in expected}
            # The payer is checked on its own: a right split outcome must not hide a wrong
            # payer ("Carol covered dinner 90" is "no split" whoever the model says paid).
            payer_wrong = 'payer_name' in expected and reading.get('payer_name') != expected['payer_name']
            # Exact-amount cases must show the menu advice as well as refusing a guessed split.
            warning_wrong = 'warnings' in expected and reading['warnings'] != expected['warnings']
            # The amount is checked too: a split reading must not cost the price (#120).
            amount_wrong = transaction.get("amount") != case["amount"]
            if (not transaction or transaction.get("_count") != 1 or reading['outcome'] != case['outcome']
                    or payer_wrong or warning_wrong or amount_wrong):
                wrong.append(f"{reading['outcome']} amount={transaction.get('amount')} "
                             f"payer={reading.get('payer_name')} expenses={transaction.get('_count')}")
            elif actual != expected:
                info.append(str(actual))
        all_right = all_right and not wrong
        mark = "ok  " if not wrong else "FAIL"
        print(f"{mark} {runs - len(wrong)}/{runs}  {case['message']!r}  (want {case['outcome']})")
        for answer in sorted(set(wrong)):
            print(f"           got {answer}  x{wrong.count(answer)}")
        for answer in sorted(set(info)):
            print(f"           info: raw fields {answer}  x{info.count(answer)}")
    start = (len(CASES) + len(SPLIT_CASES)) * runs
    for i, (message, want) in enumerate(MULTI_CASES):
        got = [t.get("_count") for t in results[start + i * runs:start + (i + 1) * runs]]
        wrong = [n for n in got if n != want]
        all_right = all_right and not wrong
        mark = "ok  " if not wrong else "FAIL"
        print(f"{mark} {runs - len(wrong)}/{runs}  {message!r}  (want {want} expenses, got {got})")
    return 0 if all_right else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=1, help="runs per message (default 1)")
    parser.add_argument("--only", help="run only the messages containing this text (ignoring case)")
    args = parser.parse_args()
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY isn't set - add it to .env.")
    _only(args.only)
    if not (CASES or SPLIT_CASES or MULTI_CASES):
        sys.exit(f"No message contains {args.only!r}.")
    sys.exit(asyncio.run(main(args.runs)))
