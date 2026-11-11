# ADR-0032: Keep participants and accounts in Postgres tables, read on every message, and fail closed

**Status:** Accepted
**Date:** 2026-11-11
**Issues:** [#53](https://github.com/sujayjangam/unified-ledger-pipeline/issues/53)
**Code:** `alembic/versions/0004_add_participants_and_accounts.py`, `app/services/participants.py`, `app/bot_core.py::authorize`, `scripts/seed_directory.py`
**Spec:** `docs/superpowers/specs/2026-11-11-participants-accounts-design.md`

## Context

Three env vars described the household: `ALLOWED_TG_IDS` (who may use the bot), `ACCOUNT_OWNERS`
(each person's cards, the first one their default) and `PRIMARY_ACCOUNT_OWNER` (who pays for a
YouTrip top-up). They had three problems:

- **Changing them meant a redeploy.** A new card, or a card cancelled and replaced, needed Secret
  Manager edits and a new Cloud Run revision before the bot knew about it.
- **Rows held names, not references.** A transaction stored its card and owner as free text
  (`account_desc`, `account_owner`). A renamed card split one account into two spellings, and
  nothing in the database could say which rows belonged to which card.
- **The data model couldn't grow.** Splitting a bill, or a trip with people who aren't in the
  household, needs people and accounts that other rows can point at.

## Decision

Two tables, created by migration 0004 and filled from a gitignored seed file by
`scripts/seed_directory.py` (this repo is public, and the rows are real names and cards):

- **`participants`**: `display_name`, an optional `telegram_user_id` (set means "may use the
  bot"), and `default_account_id`.
- **`accounts`**: `name`, an optional `owner_participant_id` (NULL means shared, like Cash or the
  wallet), `funded_by_account_id` (on the YouTrip wallet only: the card a top-up comes from) and
  `closed_on`.

`transactions` gains `account_id` and `entered_by_participant_id`, foreign keys to those tables.

The rules, all in `app/services/participants.py`:

- **Ids are identity columns** (`GENERATED ALWAYS`): a new row always gets a new id, and one is
  never reused, so a row's link can't silently point at a different card later.
- **A closed card keeps its real name.** Its rows are real spending. `closed_on` marks it closed;
  at most one *open* account may have a given name (ignoring case), so a replacement card can
  reuse the name. A new entry against a closed card gets a warning and a "Save anyway" button
  rather than a refusal, because a refund can arrive after a card is cancelled.
- **The top-up card is a property of the wallet**, not a person: `funded_by_account_id`. With no
  open wallet, or no funding card, a top-up is refused rather than guessed.
- **The default card is a property of the person**: `default_account_id`.
- **Read on every message, not cached.** Two small SELECTs next to an OpenAI call that takes
  seconds. A card added in the Neon console works on the next message.
- **Fail closed.** If the tables can't be read, or fail validation (someone who uses the bot with
  no open default card, a wallet funded by a closed card), `authorize()` lets nobody in and says
  "Couldn't reach the ledger". The allowlist lives in those tables, so letting everyone through
  whenever it can't be checked would make it worthless.
- **A button tap never reads the tables.** The pending entry already holds both ids, so what the
  user confirmed is what is saved.

The rollout is expand, then contract: 0004 adds the columns, the code writes both the old text
columns and the new ids, and the old columns are dropped only once nothing reads them.

## Alternatives considered

- **Keep the env vars, add an ids-only table.** Two sources of truth for one list, which is the
  bug that once made the prompt run on a hardcoded fallback (see `CLAUDE.md`, ingestion step 0).
- **Mark a closed card by renaming it ("Card A (closed)").** Old rows would then show a name the
  card never had, and the name could no longer be matched to statement lines.
- **An `aliases` table for old spellings.** Dropped: the few old rows with odd spellings each get
  a one-off override in the seed file instead.
- **Cache the directory at start-up.** Faster by a few milliseconds, but a new card would need a
  restart, which is the problem this replaces.
- **Fail open (let the env-var allowlist through if the database is down).** It would keep the
  env vars alive as a second source of truth, and a database outage would stop saves anyway.

## Consequences

- Cards and people change with a row edit, not a deploy.
- Every message costs one extra round trip to Neon before the model is called. If Neon is down,
  the bot is down for everyone, where before it would have failed only at Confirm.
- `account_owner` on old rows was guessed from the card name; `account_id` is exact from now on,
  and the backfill asks a person about any row it can't match with certainty.
- A participant without a Telegram ID can exist, which is the starting point for splitting with
  people outside the household. Trips and groups are a separate, later piece of work.
