# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Start here: check ROADMAP.md first

This project is under active, phased development. `ROADMAP.md` has a "Current status" section
that says exactly what phase we're in and what's next — read that before doing anything else, and
before re-reading source files to reconstruct context. Update it before ending a session that made
progress. Don't start implementation work unless the user asks for it in the current conversation,
even if the roadmap lists it as next.

## Response style

Keep responses concise and easy to read: lead with the answer, use short paragraphs, and skip
sprawling heading/section trees for simple questions. Prefer plain prose over heavy formatting;
expand detail only when the task genuinely needs it.

## What this is

A household expense ledger with two ingestion paths into one Postgres database:
1. A Telegram bot that accepts voice notes or plain text messages, transcribes voice notes (OpenAI
   Whisper), extracts structured transaction data (GPT-4o-mini + Pydantic), and asks for
   confirmation before saving.
2. A small FastAPI REST API (`app/main.py`) for programmatic entry.

Design decisions live in `docs/decisions/` as numbered ADRs (`docs/decisions/README.md` is the
index) — read the relevant one before re-opening a settled choice, and add a new record rather
than editing an accepted one. This file says *what* the rules are; the ADRs say *why*.
`docs/SCHEMA.md` is the canonical schema reference — read it before changing table columns.

## Keeping the docs current

`README.md`, `ROADMAP.md` and this file drift from the code unless they're checked, in two
directions, and both have happened here:

- **Staleness**: this file once described the pre-Postgres SQLite schema for a whole migration
  cycle after the Neon cutover shipped.
- **Aspirational drift**: `ROADMAP.md`'s forward-looking "About" blurb was once copied into
  `README.md` before that work existed. Forward-looking language belongs in `ROADMAP.md` only, and
  moves into `README.md` once the phase ships — never before.

So every build, feature or bug fix includes a check of whether `README.md`, `ROADMAP.md` and
`CLAUDE.md` need updating: a new capability, a claim that's now wrong, a changed command, or
internals (schema, architecture, env vars) that no longer match the code. `README.md` only ever
describes what has shipped.

The `doc-checker` subagent (`.claude/agents/doc-checker.md`) automates the README-vs-code-vs-roadmap
half of this check, and scans `CLAUDE.md`/`ROADMAP.md` for non-engineering content (both are in a
public repo). Run it — `@agent-doc-checker run the check` — before committing doc changes or when
picking work back up after a gap.

## Commands

Run everything from the repo root (imports use the `app.` prefix; there are no `__init__.py`
files, so packages rely on Python's implicit namespace packages).

```bash
pip install -r requirements.txt

# Initialize / migrate the DB (Alembic owns the `transactions` table in Postgres).
# CAUTION: this targets DATABASE_URL from .env - i.e. PRODUCTION. alembic/env.py never reads
# .env.local; to migrate a test branch, set DATABASE_URL in the shell first and run
# `alembic current` to check the printed host. See docs/LOCAL_TESTING.md.
alembic upgrade head

# Run the bot locally against the separate test bot (reads .env.local over .env) - see
# docs/LOCAL_TESTING.md. There is deliberately no runner that polls the production token:
# polling deletes that bot's registered webhook.
python -m app.bot_local

# Run the production-style webhook server locally
uvicorn app.bot_webhook:app_fastapi --reload --port 8080

# Run the standalone REST API
uvicorn app.main:app --reload

# CLI expense entry
python -m app.add_expense --date 2026-07-29 --desc "Lunch" --amount 12.50 --cat Food

# View the ledger in a terminal table
python -m app.view_ledger

# Run the test suite (also runs in CI on every pull request)
pip install -r requirements-dev.txt
pytest
```

`tests/` is pure logic, with no network calls and no database — enforced, not just intended: an
autouse fixture in `tests/conftest.py` makes any attempt to open a connection fail, because
locally `.env`'s `DATABASE_URL` is production. Stub queries and Telegram objects instead
(`tests/test_button_callback.py` and `tests/test_duplicate_warning.py` show the pattern).
Test-only dependencies live in `requirements-dev.txt`, so `requirements.txt` still means "what
production needs". Not covered yet: migrations against a real Postgres, and the
`ON CONFLICT (idempotency_key)` path — see the test-suite checklist in `ROADMAP.md`. `main` is
protected by a repository ruleset requiring the suite, lint and an import smoke check to pass
before merge ([ADR-0021](docs/decisions/0021-rulesets-over-classic-branch-protection.md)).

Docker (Cloud Run deployment target): `Dockerfile` installs `requirements.txt` and runs
`uvicorn app.bot_webhook:app_fastapi --host 0.0.0.0 --port 8080`.

### Required environment (`.env`, loaded via `python-dotenv`)

`.env.example` shows the format of each one.

- `TELEGRAM_BOT_TOKEN`
- `ALLOWED_TG_IDS` — JSON object, Telegram user ID (string) → display name. The allowlist
  `is_authorized()` checks.
- `ACCOUNT_OWNERS` — JSON object, person → list of their accounts/cards, first entry is their
  default. The **only** place accounts are defined (see step 0 of the ingestion pipeline below).
- `PRIMARY_ACCOUNT_OWNER` — one of the keys in `ACCOUNT_OWNERS`; funds shared transfers (e.g. a
  YouTrip top-up) whoever sent the message. Configurable so no real name is hardcoded.
- `OPENAI_API_KEY`
- `DATABASE_URL` — must use the `postgresql+psycopg://` scheme. This project uses `psycopg` v3; a
  plain `postgresql://` URL makes SQLAlchemy look for the uninstalled `psycopg2`
  ([ADR-0006](docs/decisions/0006-psycopg3-url-scheme.md)).
- `WEBHOOK_URL` — optional, only used by `bot_webhook.py` to register the Telegram webhook.
- `WEBHOOK_SECRET_TOKEN` — required whenever `WEBHOOK_URL` is set; `bot_webhook.py` refuses to
  start without a well-formed one. `/webhook` rejects any delivery that doesn't carry it back,
  *before* parsing the body or recording the `update_id` — that is what makes the sender ID
  `is_authorized()` trusts actually trustworthy. From Secret Manager in production
  ([ADR-0025](docs/decisions/0025-webhook-secret-token.md)).

`.env.local` (gitignored by the `.env.*` rule) is the local-testing overlay: only a test bot's
`TELEGRAM_BOT_TOKEN` and a Neon-branch `DATABASE_URL`. `app/bot_local.py` loads it with
`override=True` *before* importing `bot_core`, whose own `load_dotenv()` then fills in the rest
from `.env` — one copy of each secret on disk. See `docs/LOCAL_TESTING.md` and
[ADR-0019](docs/decisions/0019-separate-bot-token-for-local-testing.md).

## Architecture

### Bot: transport vs. logic split

The same business logic runs under two transports
([ADR-0003](docs/decisions/0003-split-bot-transport-from-logic.md)):
- `app/bot_core.py` — the factory (`get_application()`), all command/message handlers, and the
  `is_authorized()` gatekeeper. Builds and configures the bot but never starts a network loop.
- `app/bot_local.py` — local runner; loads `.env.local` over `.env`, refuses to start with the
  production bot token, then calls `run_polling()` against a separate test bot.
- `app/bot_webhook.py` — production runner; wraps the same app in FastAPI behind `POST /webhook`,
  managing PTB init/shutdown in a `lifespan` context manager (Cloud Run sleeps idle containers, so
  polling isn't viable there).

### Ingestion pipeline (`bot_core.py`)

**Put new extraction/inference logic in `process_expense_text`, not in a handler** — anything added
to a handler only works for that one input type.

- `handle_voice` downloads the voice note, transcribes it with Whisper (`language="en"` is pinned —
  [ADR-0009](docs/decisions/0009-pin-whisper-language-en.md)), and passes the transcript on.
- `handle_text` passes the message body straight on. `~filters.COMMAND` keeps `/recent` etc. on
  their own handlers.
- `handle_unsupported` replies to everything else (photos, stickers, documents), which PTB would
  otherwise drop silently. A captioned photo lands here too: it carries `caption`, not `text`.

`process_expense_text` edits one status message throughout, so each attempt leaves exactly one
message. In order:

0. The payment methods the model may choose from are built **per call** by
   `extraction.py::build_allowed_accounts`, flattening `ACCOUNT_OWNERS` plus `Cash`. Never
   reintroduce a second source of truth for this list: a separate, never-set `ALLOWED_ACCOUNTS`
   var once made the prompt run on a hardcoded fallback and attribute entries to the wrong owner.
1. `extraction.py::extract_transactions` → GPT-4o-mini structured output (`TransactionList`). The
   raw input is kept as `description`, never an LLM summary
   ([ADR-0008](docs/decisions/0008-raw-transcript-as-description.md)).
2. More than one expense in a message is rejected — a product guardrail, not a technical limit
   ([ADR-0010](docs/decisions/0010-one-expense-per-message.md)).
3. No amount → the entry is abandoned with a prompt to retry. The prompt tells the model never to
   guess an amount, so this is where a non-expense message ("hello") lands.
4. `apply_payment_defaults` (standalone so it can be unit-tested): a YouTrip top-up is forced to
   `PRIMARY_ACCOUNT_OWNER`'s default account and type `Transfer`; otherwise a missing payment
   method becomes `YouTrip` for non-SGD or the sender's default account for SGD. `account_owner`
   is then reverse-matched from the payment method (case-insensitive; `Cash` → the sender). It
   means *whose card paid*, not who sent the message — the sender is `entered_by`.
5. Duplicate check: `ledger_queries.py::find_recent_duplicate` looks for the same amount (cents)
   and currency saved by anyone in the last `DUPLICATE_WINDOW_MINUTES` (5), windowed on
   `created_at`. A match turns the card into a warning with "⚠️ Save anyway" / "❌ Cancel" (same
   `confirm_save` path). It runs before the card is shown, never inside the insert, and a failed
   check gives a normal card — it must never block a save
   ([ADR-0027](docs/decisions/0027-duplicate-warning-before-the-card.md)).
6. The pending entry is stashed in `context.user_data['pending_cards']`, keyed by the card's
   `message_id`, and written only when Confirm is tapped
   ([ADR-0023](docs/decisions/0023-in-memory-confirm-card-state.md)).

`handle_button_click` rules:
- **A callback query can be answered exactly once**, so every branch decides what to say before
  calling `answer()`.
- On a valid confirm, answer and swap in the non-actionable `⏳ Adding to ledger...` button
  *before* the blocking `add_expense` call, or the button looks dead and gets tapped again.
- A tap on a card already in `context.user_data['resolved_cards']` gets a toast and nothing else.
  Never `edit_message_text` it — that is the bug
  [#29](https://github.com/sujayjangam/unified-ledger-pipeline/issues/29) fixed.
- A failed save puts the entry back with its original `idempotency_key` and buttons, so a
  transient Neon failure costs a retry, not the entry.

### Storage

- `app/database.py` owns a lazily created, pooled SQLAlchemy Core engine (`get_engine()` /
  `get_connection()`). It isn't created at import time, so importing never fails on a missing
  `.env`.
- **Money is integer cents.** The only way in is `app/add_expense.py::dollars_to_cents`, used by
  the bot, CLI and REST API; the only way out for display is `format_cents`. Never format a float
  amount yourself, and never split an amount by rounding each share — splits hand out whole cents
  ([ADR-0004](docs/decisions/0004-money-as-integer-cents.md),
  [ADR-0028](docs/decisions/0028-exact-cent-conversion-half-up.md)).
- Alembic (`alembic/versions/`) owns the schema; there is no `CREATE TABLE` in application code.
  Revisions are hand-written, since Core has no metadata for `--autogenerate`
  ([ADR-0007](docs/decisions/0007-alembic-hand-written-migrations.md)).
- `created_at` is the **write** time, set only by the Postgres default. `date` is the **business**
  date. Never substitute one for the other: anything meaning "within the last N minutes" uses
  `created_at`. An exact `00:00:00+08` on an old row means "time unknown"
  ([ADR-0024](docs/decisions/0024-created-at-write-time-column.md)).
- `entered_by` is the **sender's Telegram user ID**, as the string `ALLOWED_TG_IDS` keys on. Store
  the ID, never a name — names are looked up when shown. NULL means "not recorded" (old rows,
  CLI/REST API rows). Tests use fictional IDs only
  ([ADR-0026](docs/decisions/0026-entered-by-telegram-user-id.md)).
- **Production is never migrated automatically.** A merge to `main` auto-deploys, so run
  `alembic upgrade head` by hand *before* merging code that depends on a migration. Additive
  migrations are safe to apply first: no query uses `SELECT *` and every writer names its columns.
- `idempotency_key` with `ON CONFLICT (idempotency_key) DO NOTHING` in `add_expense` stops a
  double-tapped Confirm inserting twice ([ADR-0011](docs/decisions/0011-idempotency-key-over-update-id.md)).
  That is a different problem from duplicate webhook deliveries (`update_id`), which are deduped
  only in memory (`_seen_update_ids` in `bot_webhook.py`) and scheduled to be persisted in
  Phase 1 — see `ROADMAP.md`.
- `app/services/ledger_queries.py` backs `/recent`, `/today`, `/week`, `/month` and the `/cat_*`
  commands, plus `find_recent_duplicate`. Aggregates are grouped by currency (no FX conversion
  anywhere). Each query catches its own errors; for the duplicate check that's load-bearing, since
  `None` means "show a normal card".
- `app/services/utils.py::get_sgt_now()` is the canonical "now" for period boundaries — never
  `datetime.now()`, so cutoffs stay in Singapore time on a UTC Cloud Run host.

### Backups

`.github/workflows/backup.yml` runs `pg_dump -Fc` every 6 hours to GCS, with 30-day retention by a
bucket lifecycle rule and keyless auth through Workload Identity Federation (this repo is public).
Neon's free-tier point-in-time recovery only covers 6 hours, so this is the real backup. It is a
full snapshot, not a per-transaction undo, and production is never restored into directly — see
`docs/BACKUP_RESTORE.md` and ADRs
[0012](docs/decisions/0012-github-actions-over-cloud-scheduler.md)-[0014](docs/decisions/0014-pg-dump-custom-format.md).

### REST API (`app/main.py`)

Independent of the bot — a minimal FastAPI surface (`GET/POST /transactions`) with its own Pydantic
`Transaction` model. Defaults `account_owner` to `"Shared"`, so a caller that omits it never leaks
a real owner's name.

### `scripts/`

`scripts/migrate_to_postgres.py` is the one-off SQLite → Postgres copy, run on 2026-08-01. Its
source file no longer exists, so it can't run again. It is kept on purpose as a reference — don't
delete it.

## GitHub issue conventions

- **Parent issues are problem statements, not task descriptions.** Describe the user-facing
symptom/impact ("no way to tell which transaction occurred first"), not the fix ("add a
created_at column"). Sub-issues (`gh issue create --parent <#>`) break the fix into independently
completable, independently verifiable steps. See #2 (parent) → #4 (sub-issue), and #9 (parent) →
#10-#14 (sub-issues) as the reference pattern.
- **Default assignee: `sujayjangam`, always** (`gh issue create --assignee sujayjangam ...`).
Every issue in this repo should be assigned by default — don't wait to be asked per issue.
- **Ask before filing.** Don't create a GitHub issue proactively without the user asking for it in
the current conversation — offer to file one, or note it as a candidate, but wait for a go-ahead.
- **Supersede, don't abandon.** If an issue gets re-scoped into a new one, close the old one with
`gh issue close <old> --duplicate-of <new>` plus a comment explaining why, rather than leaving both
open or silently dropping one (see #8 → closed as duplicate of #9).
- **`ROADMAP.md` should not duplicate issue bodies.** Once something has a filed issue, this file
should link the issue number with a one-line status, not restate its repro steps/sub-tasks/
verification criteria — those live in the issue. `ROADMAP.md` stays the narrative/phase-level
layer: what phase we're in, what's currently blocking, and pointers to the issues that track the
detail.
- **Hierarchy for decomposition, labels for themes.** Parent/sub-issue means "these are the steps
that complete this one deliverable" — the parent closes when its children do (#9 → #10-#14). A
cross-cutting theme spanning independently-shippable issues is a *label*, never a grandparent
issue: GitHub allows 8 levels of nesting but an issue can have only **one** parent ever, so
spending that slot on a theme is unrecoverable, and a theme parent never closes.
- **Label set (added 2026-08-20).** Type reuses GitHub's defaults (`bug`, `enhancement`,
`documentation`). Area is four labels: `area:capture` (entry, ingestion paths, input UX),
`area:reconciliation` (statement parsing, matching, review queue), `area:data-integrity` (schema
correctness, wrong/missing values, doc-code drift), `area:infra` (deploy, backups, CI, secrets).
Keep the set small — large label sets rot. **Area labels go on parents and standalone issues only,
not on sub-issues**, so that filtering by area returns deliverables rather than their internal
steps.
- **Phases are milestones, not labels** (`Phase 0: Foundation & ownership`, `Phase 1:
Reconciliation engine`). An issue belongs to exactly one phase, which is what a milestone models,
and it gives a completion bar for free. Do not also create phase labels — that pair goes stale.
Sub-issues *do* get the milestone even though they don't get area labels, so the progress bar
counts real units of work. Later-phase milestones get created when they have issues to hold, not
in advance.
- **Reading the issue list without sub-issue noise:** `no:parent-issue` shows only top-level
issues, and `has:sub-issue` shows only true parents. Both work in the GitHub UI search box and via
`gh issue list --search "no:parent-issue"`. The default list view interleaves parents and children
and is much harder to read.
