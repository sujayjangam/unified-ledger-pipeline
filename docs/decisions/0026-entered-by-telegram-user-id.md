# ADR-0026: Record the sender as their Telegram user ID, and leave existing rows unrecorded

**Status:** Accepted
**Date:** 2026-09-11
**Issues:** [#60](https://github.com/sujayjangam/unified-ledger-pipeline/issues/60)
**Code:** `alembic/versions/0003_add_entered_by.py`, `app/bot_core.py::process_expense_text`, `app/add_expense.py::add_expense`

## Context

`process_expense_text` always knows who sent a message. It looks the sender up in `ALLOWED_TG_IDS`
to pick payment defaults, and then throws the answer away. The row stores nothing about the
sender, and `source` only ever says "Telegram Bot".

The only person column, `account_owner`, records whose card paid. It is inferred from the payment
method by first match, and it isn't the sender. A read-only check of production on 2026-09-10 (47
rows) found:

- 16 rows that name no person at all: 8 `Unknown`, 6 empty, and 2 holding a card name.
- 8 rows paid with a method both household members hold. 7 of them went to the same member.
- One member named on 30 rows, the other on 1, even though both log expenses regularly.

Because the sender is discarded, a wrong attribution can never be corrected later. The
duplicate-entry warning ([#58](https://github.com/sujayjangam/unified-ledger-pipeline/issues/58))
also needs to say *who* logged the earlier entry.

## Decision

Add a nullable `entered_by TEXT` column holding the sender's Telegram user ID
(`str(update.effective_user.id)`), in the same string form `ALLOWED_TG_IDS` uses for its keys.

- **Display names are never stored.** Wherever a name is shown, it's looked up from the ID.
- **Existing rows stay NULL,** meaning "sender not recorded".
- **CLI and REST API rows are NULL too,** because no Telegram user entered them.

## Alternatives considered

**The Telegram username.** Rejected. It's easier to find than an ID, but Telegram documents it as
optional and removable, and a username someone gives up can later be claimed by someone else. It
identifies an account at one moment, not over time.

**The display name from `ALLOWED_TG_IDS`.** Rejected. It's readable directly in the database, but
it's whatever the mapping says today. Rename someone and their old rows keep the old spelling.
When [#53](https://github.com/sujayjangam/unified-ledger-pipeline/issues/53) moves household
members into Postgres, rows would then have to be matched by name.

**Store both an ID and a name.** Rejected. That's two columns for one fact, and the name column
can drift away from the mapping.

**Backfill existing rows from `account_owner`.** Rejected. 16 of the 47 rows can't be mapped to
anyone, and the rest are skewed by the first-match rule. A guess stored in `entered_by` would look
exactly like a real value, in the one column meant to be trustworthy. It also wouldn't help the
duplicate warning, which only looks back five minutes and never sees a row written before this
change.

## Consequences

**Bought:**

- Every row the bot writes from now on can be traced to its sender.
- Once the attribution rule is fixed, `account_owner` can be re-derived for new rows.
- The duplicate warning can name who logged the earlier entry.
- Renaming someone in `ALLOWED_TG_IDS` changes every past row's displayed name without touching
  the data.

**Cost:**

- **Telegram IDs now also live in the database and its backups,** not only in `.env` and Secret
  Manager. Both are private.
  [ADR-0025](0025-webhook-secret-token.md) made knowing an ID useless to anyone who isn't
  Telegram: an update has to carry the webhook secret to reach the bot.
- **Rows written before this change will never have a sender recorded.**
- **When #53 lands,** `entered_by` should point at the new household-members table's internal ID,
  so the Telegram ID is stored once per person rather than on every row. That's a small, exact
  migration from ID to person row.
