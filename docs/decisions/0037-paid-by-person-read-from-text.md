# ADR-0037: The payer is a person column, read from the message by Python; an owned account decides the payer

**Status:** Accepted
**Date:** 2026-11-15
**Plan:** `docs/superpowers/plans/2026-11-15-paid-by.md`
**Code:** `alembic/versions/0007_add_paid_by.py`, `app/services/participants.py::read_payer`, `::resolve_payment`, `app/bot_core.py::process_expense_text`, `app/services/ledger_queries.py::get_period_summary`, `::get_category_summary`, `scripts/seed_directory.py`

## Context

Each entry records who logged it (`entered_by_participant_id`, ADR-0033) and which account paid
(`account_id`). Neither says who paid. For everyday spending that didn't matter: the account's
owner, or the sender for Cash, was the payer. On a group trip it does. Someone outside the
household pays for dinner, or Bob pays cash while Alice logs it, and settling up later
(`/balances`) needs one payer per entry to read.

Spliit and SplitPro, two open-source bill-splitting apps, both store the payer as a person on the
expense, apart from the account and from who added it, and neither records how that person paid
(their schemas, read 2026-11-15).

Who paid usually sits in the same sentence as who shares: "split with Carol, Bob paid". A model
asked to pick a payer can swap the two, and the error would be silent. ADR-0030 and ADR-0031 met
the same problem with dates and moved the deciding step into tested Python.

## Decision

- **A column:** `transactions.paid_by_participant_id`, a person, NULL meaning "not recorded" (old
  rows, the CLI and the REST API). NULL is read as the account's owner, or the sender for an
  unowned account, which is what it meant before.
- **Python reads the payer from the message** (`read_payer`), never the model. It matches
  "paid by X", "paid for by X", "X paid" and "X pays" against known names: the sender's trip
  members when trip mode is on, otherwise the household. "I paid" and "paid by me" are the
  sender. Two different names, or a payer-shaped phrase with no known name, show
  "⚠️ Couldn't tell who paid" on the card.
- **The payer and the account must agree**, in this order:
  1. A YouTrip top-up is unchanged: the funding card pays, and its owner is the payer.
  2. A payer outside the household puts the entry on one shared account, "Paid by someone
     else", with no owner, whatever account the model picked. If that account doesn't exist the
     entry is refused, like a top-up with no wallet. The model can't pick it: it is left out of
     the account list in the prompt and of the message scan, so only this rule puts an entry
     there.
  3. Otherwise the account is chosen as before, except that a household payer named with no
     account gets their own default. An owned account then decides the payer; an unowned one
     (Cash, the wallet) takes the named payer, or the sender.
- **`participants.is_household`**, with a check that anyone with a Telegram ID is household. The
  seed file sets it.
- **Totals** (`/today`, `/week`, `/month` and the category views) skip an entry whose payer is set
  and outside the household. NULL still counts. Recent entries and the duplicate check keep every
  row.
- **The card** replaces its "Account Owner" line with "Paid by": under rule 3 the payer of an
  owned account is always its owner, so one line says both. Warnings ("Couldn't tell who paid",
  a named payer overruled by the card's owner, an outside payer moved off a household card) are
  card lines only. They are never stored and never block a save.
- A "Paid by" button to correct the payer ships in a second PR.

## Alternatives considered

- **The model picks the payer.** Rejected: it would confuse "split with" and "paid by" silently,
  and the date fields showed that a better prompt doesn't fix this kind of error (ADR-0031).
- **The payer is always the account's owner.** Rejected: Cash and outside payers have no owner,
  and those are the common trip cases.
- **Record how an outside person paid** (a card or cash of theirs). Rejected: nothing reads it,
  and both apps above leave it out.

## Consequences

- One column to read for `/balances`, and the household's totals stop counting other people's
  money.
- A phrasing the matcher misses ("Carol covered dinner") shows the sender as payer. The card says
  so, and until the button ships the fix is Cancel and resend. Phrasings are added only when real
  messages miss.
- The household of a real trip is now data, in the seed file. A wrong flag drops someone's
  spending out of the totals.
- Until splits exist, a shared bill a household member paid still counts in full in the totals.
- One payer per entry. How an outside person paid isn't recorded.
