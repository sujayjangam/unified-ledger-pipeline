# Participants and accounts as Postgres tables — design

Issue: [#53](https://github.com/sujayjangam/unified-ledger-pipeline/issues/53) (Phase 1, first item).
Status: draft for review, 2026-11-11.

## Goal

Move the people and payment accounts the bot knows about out of the `ALLOWED_TG_IDS`,
`ACCOUNT_OWNERS` and `PRIMARY_ACCOUNT_OWNER` env vars and into Postgres, and link every
transaction to them by id. Success means:

- Adding or closing a card is one row change in the Neon SQL editor, picked up on the next
  message, with no redeploy.
- A payment on a shared account resolves "whose card paid" to the sender; a payment on a
  single-owner card resolves to that card's owner, whoever sent it.
- Every transaction points at the account that paid (`account_id`) and, where known, the
  participant who entered it (`entered_by_participant_id`). The text columns that duplicate those
  facts are dropped.
- Accounts have stable ids, so Phase 1's statement lines can attach to an account row.

## Constraints (from #53, not reopened)

- No real names or card labels in the repo. Seed data comes from a gitignored local file via a
  script, never from an Alembic migration.
- `tests/` stays database-free. Only the loader touches Postgres.
- The env vars are **removed**, not kept as a fallback.
- No cache in the first cut: the directory is loaded per message.

## Out of scope

- **Trips / groups.** A trip is a group of participants with optional start and end dates;
  expenses tagged to it are split and settled within it, and an expense dated outside the trip
  gets a non-blocking ⚠️ line on the card. The household becomes a group with no end date, so
  splitting is written once for both. This is Phase 3 work, possibly moved earlier; this design
  must not block it (see "Not blocking groups" below).
- **CLI and REST API features.** Both are unused. They get only the change needed to keep
  inserting once `account_id` is required (see "CLI and REST API").
- A bot command for adding cards or people.

## Data model

### `participants` — one row per person

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGINT GENERATED ALWAYS AS IDENTITY` PK | Never reused, never set by hand |
| `display_name` | `TEXT NOT NULL UNIQUE` | Never written into the repo |
| `telegram_user_id` | `TEXT UNIQUE`, nullable | Same string form `ALLOWED_TG_IDS` keys used. NULL = tracked but can't use the bot |
| `default_account_id` | FK → `accounts.id`, nullable at DB level | Used for an SGD payment with no card named. Required by the loader for anyone with a Telegram ID |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |

The allowlist is "has a `telegram_user_id`".

### `accounts` — one row per card, wallet or cash

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGINT GENERATED ALWAYS AS IDENTITY` PK | Never reused, never set by hand |
| `name` | `TEXT NOT NULL` | What the extraction prompt shows and matches on |
| `owner_participant_id` | FK → `participants.id`, nullable | NULL = shared (Cash, a joint transfer method) → resolves to the sender |
| `funded_by_account_id` | FK → `accounts.id`, nullable | Set on wallets such as YouTrip: the card that tops it up |
| `closed_on` | `DATE`, nullable | NULL = open. Closed accounts keep their real name and owner, and are left out of the prompt |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |

Database rules:

- **Unique open names, ignoring case:** a partial unique index on `lower(name) WHERE closed_on IS
  NULL`. Owner lookup ignores case, so two open "card a" / "Card A" accounts would be ambiguous.
  A closed account may share a name with an open one (a replacement card).
- **No deletes:** foreign keys use the default `RESTRICT`, so a participant or account that
  anything points at can't be deleted. Closing is the only way to retire an account.
- `participants.default_account_id` and `accounts.owner_participant_id` reference each other, so
  seeding inserts participants first, then accounts, then sets each default.

### `transactions` changes

Added (migration 0004, nullable):

- `account_id` FK → `accounts.id`. Made `NOT NULL` in migration 0005, after the backfill.
- `entered_by_participant_id` FK → `participants.id`. Stays nullable: rows from before
  `entered_by` existed, and CLI/REST API rows, have no sender.

Dropped (migration 0005): `account_desc` (the card name), `account_owner`, `entered_by`.

### Derived, no longer stored

- **Card name:** `accounts.name` via `account_id`.
- **Whose card paid:** the account's owner, or, when the account is shared, the participant in
  `entered_by_participant_id`. Shared account with no recorded sender → "not recorded".
- Dropping `account_owner` loses its old value on shared-account rows. That is intended: those
  values came from the first-match rule and were guesses (migration 0003's notes found 16 of 47
  rows naming no person at all).

## Rollout

Production auto-deploys on merge and is never migrated automatically. Each step is one sub-issue
of #53.

1. **Migration 0004, additive.** Create both tables; add the two nullable columns. Applied to
   production by hand *before* its PR merges. Running code doesn't notice.
2. **Seed and backfill script** (`scripts/`, dry run by default, `--write` to apply):
   - Reads a gitignored local file to insert participants and accounts.
   - Backfills `account_id` by matching `account_desc` to `accounts.name`, ignoring case. When a
     name matches more than one account, picks the one open on the transaction's date (not yet
     closed on that date). If that still leaves more than one, stops and asks.
   - Backfills `entered_by_participant_id` by matching `entered_by` to `telegram_user_id`.
   - Prints every unmatched `account_desc` (and every row with none) for a decision: map it to an
     existing account (a typo or invented name), or add it as a closed account with its real name,
     owner and `closed_on`. Writes nothing until rerun with `--write`.
   - Only fills NULLs, so rerunning is safe.
   - Run against a Neon branch first, then production.
3. **Code switch-over PR.** The bot reads the directory from the database and writes the two new
   id columns, *and still writes* `account_desc`, `account_owner` and `entered_by`, so reverting
   the deploy never breaks saving. After it's live: rerun the backfill to catch rows saved in the
   gap, then remove the three env vars from Cloud Run, `.env.example` and the docs.
4. **Stop writing the old columns**, in a small PR, once step 3 has been confirmed in real use.
5. **Migration 0005, the contraction.** `account_id` `NOT NULL`; drop the three old columns.
   Applied *after* step 4 merges, because dropping a column live code still writes breaks every
   save. This is the one migration here that runs after its code instead of before. Its
   downgrade recreates the three columns empty; the dropped values come back only from a backup,
   so take a fresh `pg_dump` immediately before applying it.

Two new ADRs: one for the participant/account tables, and one superseding ADR-0026 (`entered_by`
as a Telegram ID → participant id). ADR-0026 stays as written.

## Code

### `app/services/participants.py`

- A plain `Directory` object holding participants and accounts.
- `load_directory(conn)` — the only function that touches Postgres. It validates before returning:
  every participant with a Telegram ID has an open default account, and every
  `funded_by_account_id` points at an open account. A failed check raises, with a log line naming
  the bad row.
- Pure functions taking a `Directory`, replacing the env-var readers:
  - authorization by Telegram ID (replaces `is_authorized`'s dict lookup);
  - the extraction prompt's payment-method list: open accounts only (replaces
    `extraction.py::build_allowed_accounts`; Cash is now an ordinary shared account row);
  - `apply_payment_defaults`: YouTrip top-up → the wallet's `funded_by_account_id`, type
    `Transfer`; otherwise a missing card → YouTrip for non-SGD, the sender's `default_account_id`
    for SGD;
  - whose card paid, per "Derived, no longer stored".
- Loaded fresh for each message. No module-level globals parsed at import.

### Failure handling

- **The allowlist fails closed.** If the directory can't be loaded (Neon down, or validation
  failed), nobody is authorized; the bot replies "Couldn't reach the ledger, try again in a
  minute" and doesn't call the model.
- The duplicate check is unchanged (amount and currency only).

### Display

`/recent`, `/today`, `/week`, `/month` and the `/cat_*` commands get the card name and whose card
paid by joining `accounts` and `participants` on the id columns.

### CLI and REST API

The smallest change that keeps them inserting once `account_id` is `NOT NULL`: each takes an
account name, matched against open accounts (ignoring case); an unknown name is an error. No other
changes.

### `scripts/check_extraction.py`

Must never touch the database, so it builds a fictional `Directory` in code.

## Testing

- Tests that monkeypatch `ACCOUNT_OWNERS` / `ALLOWED_TG_IDS` / `PRIMARY_ACCOUNT_OWNER` switch to
  building a fictional `Directory` in memory.
- New pure tests: shared account → sender; single-owner card → owner whoever sends; closed
  accounts left out of the prompt; top-up funded by `funded_by_account_id`; loader validation
  failures; the backfill's name matching, case handling and closed-date rule.
- `tests/test_migrations.py` covers 0004 and 0005 in the revision chain.
- By hand, on a throwaway Neon branch: `alembic upgrade head` / `downgrade -1` for both
  migrations, the loader, and the backfill dry run and `--write`.

## Verification (from #53)

1. The three env vars no longer appear in `.env.example`, `README.md`, `CLAUDE.md`, or the deployed
   service's env.
2. Inserting one account row in the Neon SQL editor makes it appear in the extraction prompt on
   the next message.
3. A shared-account payment from each person resolves to that sender; a single-owner card resolves
   to its owner regardless of sender.
4. `pytest` green with no database in CI.
5. Both migrations' upgrade and downgrade verified on a throwaway Neon branch.

## Not blocking groups

Phase 3 trips add `groups`, `group_members` and `transactions.group_id` on top of these tables
without changing them:

- Trip-only people are participants with no `telegram_user_id`.
- Naming is `participants` / `Directory`, not "household".
- `display_name` is unique for now. Two trip companions sharing a name would need that relaxed,
  which is a decision for the groups design.
