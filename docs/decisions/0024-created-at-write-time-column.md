# ADR-0024: Record write time in its own column, backfilled to midnight SGT

**Status:** Accepted
**Date:** 2026-09-10
**Issues:** [#9](https://github.com/sujayjangam/unified-ledger-pipeline/issues/9) (parent), [#10](https://github.com/sujayjangam/unified-ledger-pipeline/issues/10)-[#14](https://github.com/sujayjangam/unified-ledger-pipeline/issues/14)
**Code:** `alembic/versions/0002_add_created_at.py`, `app/services/ledger_queries.py::get_recent_entries`, `app/view_ledger.py`

## Context

`transactions.date` is a *business* date: `YYYY-MM-DD` text for when a spend happened, inferred
from the message. Nothing recorded when a row was *written*. That caused two separate problems:

- **Ordering.** `/recent` ordered by `date DESC, transaction_id DESC`. `transaction_id` is a random
  UUID, so entries made on the same day came back in arbitrary order ([#9](https://github.com/sujayjangam/unified-ledger-pipeline/issues/9)).
- **Nothing time-windowed was possible.** The duplicate-entry warning
  ([#58](https://github.com/sujayjangam/unified-ledger-pipeline/issues/58)) needs "saved in the
  last 5 minutes", and `date` has no time of day.

Backdated entries ([#15](https://github.com/sujayjangam/unified-ledger-pipeline/issues/15)) will
also make `date` stop matching the day an entry was sent, so `date` can't stand in for write time
even at day granularity.

## Decision

Add `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, set only by the Postgres default. Rows that
already exist are backfilled to midnight Singapore time on their own `date`:
`(date || ' 00:00:00+08')::timestamptz`.

Ordering follows the business-vs-write distinction:

- `/recent` orders by `created_at DESC`, meaning what was most recently *logged*.
- The ledger view orders by `date DESC, created_at DESC`: date order, with entry order breaking
  ties within a day.

## Alternatives considered

**Turn `date` into a timestamp.** Rejected. `date` means when the spend happened. Loading write
time into it is exactly the conflation #9 is about, and #15 needs the two to differ. Every period
query also compares `date` as text ranges, per the baseline migration's docstring.

**Backfill with the migration-run time** (what `DEFAULT now()` does on its own). Rejected. All 47
existing rows would share one identical instant, so they'd come back in arbitrary order among
themselves.

**Leave existing rows NULL.** Rejected. It's the most honest option — "we don't know when these
were written" — but it contradicts #10's NOT NULL requirement, and every query ordering on the
column would need `NULLS LAST` forever.

**Set `created_at` in application code**, e.g. passing `get_sgt_now()` from `add_expense`.
Rejected. There are three writers (the bot, the CLI, the REST API), and each would have to
remember to do it. A fourth writer that forgot would silently fail the NOT NULL constraint, or
worse, set its own clock's idea of now. The database default is one source of truth that no
writer can forget.

## Consequences

**Bought:**

- `/recent` is reliably chronological for every row written from now on.
- Time-windowed queries are possible. The duplicate-entry warning (#58) is built on this.
- #15 can change what `date` means without losing when an entry was actually made.

**Cost:**

- Rows from before this migration have a made-up time of day, always exactly `00:00:00+08`.
  Anything reading `created_at` must treat that value as "time unknown", not "logged at
  midnight". Their true order within a day is lost for good.
- **Production is not migrated automatically.** The `Dockerfile` only starts the server, so every
  migration is a manual `alembic upgrade head`. It must run *before* any code that depends on the
  new column deploys, and merging to `main` deploys.
- `alembic/env.py` reads `.env`, which is production, and never `.env.local`, so that command
  targets the live ledger by default. This change adds a printed host line and a
  `docs/LOCAL_TESTING.md` section. It does not add a hard guard.
