# ADR-0044: Trip balances worked out from the splits, and totals stay "what was paid"

**Status:** Accepted
**Date:** 2026-11-17
**Plan:** `docs/superpowers/plans/2026-11-16-splits.md` (step 4 of group-trip splitting in `ROADMAP.md`)
**Code:** `app/services/balances.py`, `app/services/ledger_queries.py::get_trip_split_entries`, `app/services/trips.py::member_trip`, `app/bot_core.py::trip_bals_command`, `app/bot_core.py::handle_bals_button`

## Context

Steps 1 to 3 record, for each entry, who paid (`paid_by_participant_id`, ADR-0037) and each
person's share in whole cents (`transaction_splits`, ADR-0039). Nothing yet says who owes whom.
A group trip needs that answer, per family, in a form that can be forwarded to people who don't
use the bot.

Three things were open:

- **Which entries.** `ROADMAP.md` planned one `/balances` command. A look at a real finished
  trip's hand-kept sheet (2026-11-17) showed two different needs: people met only on a trip
  settle that trip, while close family keep a running tab where trip costs and everyday costs
  mix from month to month.
- **Where it is shown.** Telegram has no tables, and a message is capped at 4,096 characters. A
  per-entry breakdown for ten people does not fit well.
- **The totals.** The splits plan said `/today`, `/week`, `/month` and the category views would
  change in this step to count only the household's shares of a split entry (ADR-0039,
  Consequences).

## Decision

- **`/trip_bals` shows one trip.** Only entries tagged to that trip (`transactions.trip_id`)
  that have rows in `transaction_splits`. The running tab between close family is a separate,
  later view (`/bals`), not built here.
- **Which trip is inferred, then corrected by a tap.** The sender's active trip is shown straight
  away, with an "Other trip" button. With trip mode off, the reply is a list of the sender's
  trips as buttons, paged like `/trip`. Nothing is typed after the command. A trip id from a
  button is loaded only through `member_trip`, which checks membership in the query.
- **The maths is three pure functions** in `app/services/balances.py`, with no database, model
  or Telegram:
  - `net_balances`: for each entry the payer is owed every share and each sharer owes their own.
    Plus means "is owed", minus means "owes". An entry with no split rows moves nothing.
  - `by_family`: a family's balance is its members' balances added up, using the trip's
    `trip_members.family` labels. A person with no family row on the trip is their own unit, so
    someone who settles alone is given a family label of their own in the seed file. No
    "settles alone" column and no migration.
  - `suggest_payments`: the unit that owes the most pays the unit that is owed the most, as much
    as both allow, repeated until nothing is owed. Ties go to the unit that sorts first.
- **Currencies are kept apart.** Balances use `amount` and `currency`, never `base_amount`.
  There is no FX conversion.
- **Nobody is guessed.** A split entry whose payer is not recorded falls back as `docs/SCHEMA.md`
  says (the account's owner, else the sender). If that still gives nobody, the entry is left
  out and the reply says how many were, with a ⚠️ line.
- **A failed read is never an empty answer.** `get_trip_split_entries` returns `None` when the
  query fails and an empty list when the trip has no split entries; the reply says which.
- **Plain text.** The reply is sent with no `parse_mode`, so a name can't break it and it reads
  the same when forwarded. Balances are written in words ("Lee family owes 30.00"), not signs.
- **The totals do not change.** `/today`, `/week`, `/month` and the category views keep counting
  an entry in full when a household member paid it. Money paid for someone else is the payer's
  spending until it is paid back. This replaces the plan recorded in ADR-0039's Consequences.

## Alternatives considered

- **One `/balances` over every split entry.** Rejected: families are stored per trip, so there
  is no grouping to roll an everyday entry up by, and people met on one trip would see
  unrelated bills.
- **A "settles alone" flag on `trip_members`.** Rejected: a family label of one says the same
  thing with no migration. A person who settles only some bills themselves is a settlement
  (step 5): it lowers their balance, and so their family's.
- **The true minimum number of payments.** Finding it is a hard search problem. The simple rule
  gives at most one payment fewer than the number of units, and the same answer every time,
  which matters more when the list is forwarded and questioned.
- **A read-only web page behind a secret link, or a full web app.** Deferred. A shared Google
  Sheet, written by the bot and view-only for everyone else, is the planned place for the full
  table and the per-entry breakdown; a web app stays in Phase 4.
- **Counting only the household's shares in the totals.** Rejected by the user on 2026-11-17:
  the totals answer "what did we pay out", and a shared bill the household paid is money out.

## Consequences

- The balances are only ever sums and differences of stored whole cents, so nothing is rounded
  and every currency adds up to zero. `suggest_payments` refuses balances that don't.
- No migration, and no change to any write path. The command only reads.
- "Why do I owe this much?" is not answered in Telegram: there is no per-entry breakdown until
  the Google Sheet is built.
- Until settlement (step 5) ships, a balance never goes down when someone pays. Until `/recent`
  gets delete (step 6), a wrong entry stays in the balances.
- A pre-trip cost logged with trip mode off has no trip tag and is missing from `/trip_bals`
  until the tag is added by hand; reading the trip from the message text is planned next.
- When someone pays the household back, the household's totals do not go down, because a
  settlement is not spending. The totals are cash paid out, not the household's own share.
- A one-word family label is shown as "Tan family"; a longer label is shown as stored.
