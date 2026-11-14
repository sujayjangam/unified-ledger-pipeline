# ADR-0035: Trips: one active trip in the database, entries tagged when logged, families per trip

**Status:** Accepted; the one-shared-switch part superseded by [ADR-0036](0036-trip-switch-per-person.md)
**Date:** 2026-11-14
**Code:** `alembic/versions/0006_add_trips.py`, `app/services/trips.py`, `app/bot_core.py::trip_command`, `app/bot_core.py::process_expense_text`, `scripts/seed_directory.py`

## Context

Group-trip splitting was pulled forward into Phase 1 for a real trip with a fixed date (see
`ROADMAP.md`). Its first step is knowing which entries belong to the trip. Two people log, both
on the same trip. Some trip costs, such as bookings, are paid weeks before it starts. Later steps
split each entry between trip members and settle up by family.

## Decision

- **A `trips` table with an `active` flag, at most one active row**, enforced by a partial
  unique index (`uq_trips_one_active`). `/trip on <name>` and `/trip off` switch it. It is one
  setting for everyone who logs, stored in Postgres, so both people tag to the same trip and it
  survives a restart or redeploy.
- **An entry is tagged by when it is logged, not by its date.** `process_expense_text` reads
  the active trip for every message, like the directory, and copies its id and name onto the
  pending entry. A booking logged before the trip, while the trip is on, counts as the trip's. The
  card shows the trip, with a toggle button ("🏠 Not trip" / "🧳 Tag to ...") that changes only
  the pending entry. A tap never reads the database.
- **If the active trip can't be read, the card still appears, untagged, with a ⚠️ line.** The
  directory fails closed because it is the allowlist. The trip is only a label: a missing tag can
  be fixed in SQL afterwards, but a bot that refuses entries mid-trip means nothing gets logged.
- **Family is stored per trip**, on `trip_members`, not on `participants`. It is the unit that
  settles within a trip, and a person's grouping can differ between trips.
- **Trips come from the gitignored seed file.** The seed script never makes a trip active.
- **The CLI and REST API never tag a trip.** They have no active-trip check, and their entries
  aren't trip spending today.

## Alternatives considered

- **Tag by date range** (a trip has start and end dates; an entry dated inside them is the
  trip's). Rejected: pre-trip bookings would fall outside it, and an everyday household expense
  logged during the trip dates would be pulled in.
- **Active trip per sender, in memory.** Rejected: the two people could disagree without
  noticing, and a restart or redeploy would silently switch tagging off.
- **Family as a column on `participants`.** Rejected: it fixes one grouping for every trip.
- **Fail closed on a trip-read error.** Rejected for the reason above: losing entries is worse
  than an untagged one.

## Consequences

- Forgetting `/trip off` after the trip tags everyday spending to it. The card shows the trip
  line on every entry, which is the reminder, and "Not trip" fixes a single entry.
- Turning one trip on turns any other off, in one transaction. Two people switching at the same
  moment can make one of the switches fail; they see "Nothing changed" and can retry.
- The summary commands (`/today`, `/week`, `/month`, `cat_*`) don't look at trips yet. Keeping
  other people's payments and shared bills out of household totals is part of the splitting
  steps.
- The bot code reads the new table, so migration 0006 must be applied before this code deploys.
  Without it, `/trip` fails and every card shows the ⚠️ line.
