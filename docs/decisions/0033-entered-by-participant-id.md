# ADR-0033: Record the sender as a participant id, not a Telegram user ID

**Status:** Accepted
**Date:** 2026-11-11
**Supersedes:** [ADR-0026](0026-entered-by-telegram-user-id.md)
**Issues:** [#53](https://github.com/sujayjangam/unified-ledger-pipeline/issues/53)
**Code:** `alembic/versions/0004_add_participants_and_accounts.py`, `app/bot_core.py::process_expense_text`, `app/bot_core.py::_duplicate_banner`, `scripts/seed_directory.py::_backfill_entered_by`

## Context

ADR-0026 recorded the sender as their Telegram user ID in `entered_by TEXT`, and looked the name
up in the `ALLOWED_TG_IDS` env var when it was shown. That env var is gone
([ADR-0032](0032-participants-and-accounts-in-postgres.md)): people are rows in `participants`,
each with an id and an optional Telegram ID.

## Decision

Add `transactions.entered_by_participant_id`, a foreign key to `participants.id`. The bot writes
the sender's participant id there, and the duplicate warning names the earlier sender by looking
that id up in the participants table.

`scripts/seed_directory.py` fills it on old rows by matching `entered_by` to
`participants.telegram_user_id`. ADR-0026's rule for rows with no sender still holds: NULL means
"not recorded" (rows from before `entered_by` existed, and CLI or REST API rows), and nothing is
guessed.

`entered_by` keeps being written alongside the new column until the old text columns are dropped
in a later migration, so reverting a deploy loses nothing.

## Alternatives considered

- **Keep the Telegram ID as the key.** It would work only for people who use the bot. A
  participant added for splitting (no Telegram account) could never be a sender, and the ledger
  would have two ways to refer to one person.
- **Store the display name.** Rejected for the reason ADR-0026 gives: names change, ids don't.

## Consequences

- The ledger refers to people one way, by participant id, whatever they use to send entries.
- A person's Telegram ID can change (a new account) by editing one row, without touching their
  past transactions.
- Until the old column is dropped, each bot row stores the sender twice.
