# ADR-0039: The model reads who shares an entry; Python matches the names and hands out the cents

**Status:** Accepted
**Date:** 2026-11-16
**Plan:** `docs/superpowers/plans/2026-11-16-splits.md` (PR 1)
**Code:** `app/services/splits.py::share_out`, `::resolve_split`, `::asks_for_split`, `::sender_shares`, `app/services/extraction.py::SplitReference`, `app/services/participants.py::match_person`, `app/bot_core.py::process_expense_text`, `app/add_expense.py::add_expense`, `alembic/versions/0008_add_transaction_splits.py`

## Context

A group trip needs each entry to record who shares it and how much each share is, so a later
`/balances` can say who owes whom. The person logging should say it in plain words ("split with
Carol", "split between Alice's and Bob's families") and never do the maths.

Two earlier records shape this one. The model reads dates and Python resolves them, because
maths inside the model can't be tested and a wrong saved row can't be edited (ADR-0030). And
money is whole cents, never a rounded share: SGD 5.55 split two ways by rounding is
2.78 + 2.78 = 5.56 (ADR-0004, ADR-0028).

The payer is read by Python patterns (ADR-0037). That works because "paid by X" is a short,
fixed phrase. A split isn't: people, families, "everyone", "except" and "with" versus "between"
come in too many shapes for patterns.

## Decision

- **The message decides whether an entry is split, not trip mode.** A message that doesn't ask
  for a split is a personal entry, with trip mode on or off.
- **The model returns a `split_reference`: who and how many, never a share.** Its fields are
  `kind` (none, everyone, named), `include_sender`, `people` (each a name as said, marked person
  or family), `except_people`, and `uneven`.
- **"With" includes the sender; "between" doesn't.** "Split with Carol" is the sender and Carol.
  "Split between Carol and Dave" is only those two.
- **Two things are read from the message text by Python, not the model**, as "last week" is
  (ADR-0031). Both are a question of whether a literal word is there, and the model got both
  wrong when measured (below).
  - A split is kept only if the message has a split word: split, share, between, among, divide,
    halves, dutch (`asks_for_split`). Otherwise the entry is personal, whatever the model said.
  - With "between" or "among" in the message, the sender shares only if "me", "myself" or "us"
    comes after it, in the list of sharers ("paid by me" doesn't count). Otherwise "split with"
    or "shared with" means the sender shares. With neither, the model's `include_sender` stands
    (`sender_shares`).
- **Python matches the names** (`match_person`), by the same rule as the payer: a whole display
  name, or one word of it that nobody else on the list shares. The list is the sender's trip
  members while a trip is on, otherwise every participant. A family is everyone with the same
  `trip_members.family` label as the person named; with no trip on there are no families.
- **Nobody is guessed.** A name that doesn't match gets a "⚠️ Couldn't match 'Zed'" line, and
  the people who did match keep their shares. "Everyone" with no trip on gets a warning and no
  split.
- **Python hands out the cents** (`share_out`). Each share is rounded down, then the spare cents
  go one each to the people with the largest remainder; ties go to the lowest participant id. The
  same entry always splits the same way. The payer gets no special treatment: they may not be in
  the split at all.
- **The card shows every share before Confirm**, grouped by family while a trip is on. Split
  warnings are card lines only: never stored, and they never block a save.
- **Shares are rows in `transaction_splits`** (migration 0008): one per person, in whole cents.
  No rows means "not split": the entry is the payer's alone. The shares must add up to the
  entry's amount. Python checks that before the insert, and the rows are written in the same
  database transaction as the entry, only when the entry row was really inserted, so a
  double-tapped Confirm can't write them twice (ADR-0011).
- **Uneven shares wait for PR 3.** Until then a message that asks for them ("by nights",
  "Carol's share is 50") gets a warning and no split, never a wrong even one.
- **Transfers and YouTrip top-ups are never split.**

## Alternatives considered

- **Python patterns for the split too**, as for the payer. Rejected: the phrasings are too many
  (families, "everyone except", "with" against "between"), and each miss would be a silent
  personal entry.
- **The model returns each person's share.** Rejected for the reason dates were (ADR-0030):
  arithmetic in the model can't be unit tested, and shares that don't add up would reach the
  ledger.
- **Trip mode decides: every entry logged on a trip is split between everyone.** Rejected: most
  trip entries are one family's own spending, and each would need a tap to undo.
- **Give leftover cents to whoever paid**, as the roadmap first suggested. Rejected: the payer
  may not be in the split.
- **A database trigger that checks the shares add up.** Rejected for now: one writer exists and
  it checks in Python, inside the same transaction. Worth another look when a second writer
  appears.
- **Keep the `split_ratio` Decimal column.** Rejected: a ratio has to be rounded to give money,
  which is the error whole cents avoid. Nothing writes that column.

## Consequences

- A split costs the person logging a few words and no maths, and a misread split is visible on
  the card before anything is saved.
- Measured 2026-11-16 with `scripts/check_extraction`, 16 split and payer messages, 5 runs
  each, on the model's answer alone: "lunch 60 split between Carol and Dave" counted the sender
  in 2 of 5 runs, and "dinner with Carol 70" came back as a split in 1 of 5. The other 14 were
  right every time, and so were the 17 older amount and date messages. With the two Python
  checks (and the payer check in ADR-0040), all 35 messages were right in 20 of 20 runs.
- A split said with none of the split words ("Carol and I went halfsies") is missed, and the
  entry is personal. Words are added to the list when real messages miss.
- The model can still misread who is named. The card shows it, and the cases are in
  `scripts/check_extraction`.
- Until the split menu ships (PR 2), a wrong split is fixed by Cancel and resend.
- The household's totals still count a split entry in full. Counting only the household's shares
  lands with `/balances`.
- A split is fixed when the entry is logged. The "Not trip" toggle and the "Change who paid"
  button don't change it.
- Ten people's shares make a long card. That is accepted for a trip of that size.
