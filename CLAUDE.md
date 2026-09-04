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
than editing an accepted one. `ARCHITECTURE.md` is now just an index into them.
`docs/SCHEMA.md` is the canonical schema reference — read it before changing table columns.

## Keeping the docs current

`README.md`, `ROADMAP.md`, and this file (`CLAUDE.md`) drift out of sync with the code if they
aren't actively checked, in two different directions — and both have bitten this project already:

- **Staleness**: a past session left this file describing the pre-Postgres SQLite schema (`data/
  ledger.db`, `CREATE TABLE IF NOT EXISTS`, the `account_desc` drift) for a full migration cycle
  after the Neon Postgres cutover shipped, because nothing prompted a re-check of `CLAUDE.md`
  itself when the DB layer changed.
- **Aspirational drift**: a separate past session copied `ROADMAP.md`'s forward-looking "About"
  blurb (reconciliation pipeline, eval harness) into `README.md` before that work existed.
  Forward-looking language belongs in `ROADMAP.md` only, and moves into `README.md` once the
  corresponding phase actually ships — never before.

So: every new build, feature, or bug fix should include a check of whether `README.md`,
`ROADMAP.md`, and `CLAUDE.md` each need a corresponding update — a new capability worth mentioning,
a claim that's now inaccurate, a command that changed, or a description of internals (schema,
architecture, env vars) that no longer matches the code. `README.md` specifically should only ever
describe what is actually shipped, never planned/in-progress work.

The `doc-checker` subagent (`.claude/agents/doc-checker.md`) automates the README-vs-code-vs-roadmap
half of this check, plus scanning `CLAUDE.md`/`ROADMAP.md` for non-engineering content that
shouldn't be in either (both files are checked into a public repo). Run it —
`@agent-doc-checker run the check` — before committing doc changes or when picking work back up
after a gap.

## Commands

Run everything from the repo root (imports use the `app.` prefix; there are no `__init__.py`
files, so packages rely on Python's implicit namespace packages).

```bash
pip install -r requirements.txt

# Initialize the DB (Alembic creates the `transactions` table in Postgres)
alembic upgrade head

# Run the Telegram bot locally (blocking long-poll loop, no ngrok/webhook needed)
# NOTE: this uses TELEGRAM_BOT_TOKEN from .env - i.e. the PRODUCTION bot - and polling
# deletes that bot's registered webhook. Use app.bot_local below for testing instead.
python -m app.bot_polling

# Run against the separate test bot (reads .env.local over .env) - see docs/LOCAL_TESTING.md
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

`tests/` holds pure-logic tests (money conversion, period boundaries, handler routing, extraction
schema parsing, payment-default inference, `is_authorized`) with no network calls and no database —
test-only dependencies live in `requirements-dev.txt`, kept out of `requirements.txt` so that file
still means "what production needs." `main` is protected by a repository ruleset requiring this
suite (plus lint and an import smoke check) to pass before merge — see
[ADR-0021](docs/decisions/0021-rulesets-over-classic-branch-protection.md). Migrations-against-a-
real-Postgres-container and the `ON CONFLICT (idempotency_key)` path are not yet covered; see
`ROADMAP.md`'s §3a checklist. The old `test_queries.py` — a one-off ad hoc script that ran
`ALTER TABLE ... ADD COLUMN account_desc` directly against the pre-Postgres SQLite file — is gone;
that schema change is now codified in `alembic/versions/0001_create_transactions_table.py` instead.

Docker (Cloud Run deployment target): `Dockerfile` installs `requirements.txt` and runs
`uvicorn app.bot_webhook:app_fastapi --host 0.0.0.0 --port 8080`.

### Required environment (`.env`, loaded via `python-dotenv`)
- `TELEGRAM_BOT_TOKEN`
- `ALLOWED_TG_IDS` — JSON object mapping Telegram user ID (string) → display name. Acts as the
  bot's allowlist; anyone not in this map is rejected by `is_authorized()`.
- `ACCOUNT_OWNERS` — JSON object mapping a person's name → list of their payment accounts/cards,
  first entry is that person's default. Used to reverse-lookup `account_owner` from whatever
  payment method was extracted from the voice note, and flattened by
  `extraction.py::build_allowed_accounts` into the list of valid payment methods the extraction
  prompt is allowed to choose from. It is the **only** place accounts are defined — see the
  Architecture note below for why that matters.
- `PRIMARY_ACCOUNT_OWNER` — must be one of the keys in `ACCOUNT_OWNERS`. Whoever funds shared
  transfers (e.g. a YouTrip top-up) regardless of who sent the message. Configurable rather than
  hardcoded so the codebase doesn't bake in one household's real name.
- `OPENAI_API_KEY`
- `DATABASE_URL` — Postgres connection string (`postgresql+psycopg://...`; note the `+psycopg`
  scheme — this project uses `psycopg` v3, a plain `postgresql://` URL makes SQLAlchemy default to
  the uninstalled `psycopg2` dialect and fail).
- `WEBHOOK_URL` — optional, only used by `bot_webhook.py` to register the Telegram webhook.

`.env.local` (gitignored via the `.env.*` rule, which exists because git reads `.env` as a literal
filename rather than a prefix) is the local-testing overlay: it holds only `TELEGRAM_BOT_TOKEN` for
a second BotFather bot and a Neon-branch `DATABASE_URL`. `app/bot_local.py` loads it with
`override=True` *before* importing `bot_core`, whose own `load_dotenv()` defaults to
`override=False` and so fills in the remaining keys from `.env` without disturbing the overrides —
one copy of each secret on disk. See `docs/LOCAL_TESTING.md`.

## Architecture

### Bot: transport vs. logic split
The Telegram bot is deliberately split so the same business logic can run under two different
transports:
- `app/bot_core.py` — the factory (`get_application()`), all command/message handlers, and the
  `is_authorized()` gatekeeper. Builds and configures the bot but never starts a network loop.
- `app/bot_polling.py` — local dev runner; imports the app from `bot_core` and calls
  `run_polling()`.
- `app/bot_webhook.py` — production runner; wraps the same `bot_core` app in FastAPI, exposing a
  `POST /webhook` endpoint and managing PTB init/shutdown via a `lifespan` context manager (needed
  because Cloud Run sleeps idle containers, so polling isn't viable there).

### Ingestion pipeline (`bot_core.py`)

Two entry points converge on one shared function. **Put new extraction/inference logic in
`process_expense_text`, not in a handler** — anything added to a handler only works for that one
input type.

- `handle_voice` (`filters.VOICE`) — downloads the voice file to a temp `.ogg` (closed immediately
  after creation to avoid a Windows file-lock before Telegram writes to it), transcribes it via
  `app/services/transcription.py::transcribe_audio` → OpenAI Whisper (`language="en"` is pinned
  deliberately — omitting it caused the model to randomly switch transcription languages), then
  hands the transcript to `process_expense_text`. The `try/finally` temp-file cleanup lives here
  and is voice-specific.
- `handle_text` (`filters.TEXT & ~filters.COMMAND`) — no transcription step; the message body goes
  straight to `process_expense_text`. `~filters.COMMAND` is what keeps `/recent`, `/today` etc. on
  their `CommandHandler`s.
- `handle_unsupported` (`~filters.VOICE & ~filters.TEXT`) — photos, video, stickers, documents and
  locations. Before this existed they matched no handler at all, so PTB dropped them silently and
  the user got no reply, which is indistinguishable from the bot being down. A photo *with* a
  caption lands here too: `filters.TEXT` matches `message.text`, and a captioned photo carries
  `caption`, not `text`.

`process_expense_text(update, context, raw_text, status_msg)` is the shared pipeline. `status_msg`
is a parameter rather than created inside because each transport shows a different message while it
works (voice echoes the transcript back, text has nothing to echo); every branch inside then *edits*
that one message rather than sending new ones, so a user is left with exactly one message per entry
attempt. It does:

0. The list of payment methods the model may choose from is built **per call**, by
   `extraction.py::build_allowed_accounts` flattening `ACCOUNT_OWNERS` (de-duplicated
   case-insensitively, plus `Cash`, which belongs to nobody so never appears there). It is
   deliberately not a module constant: a Pydantic `Field` description is evaluated once at import
   time, and this module is imported in paths with no `.env` loaded (CI's import smoke check,
   `tests/test_extraction.py`), so the list goes into the system prompt instead. This replaced a
   separate `ALLOWED_ACCOUNTS` env var that was never actually set — the prompt silently ran on a
   hardcoded fallback that omitted one household member's card and named exactly one bank account,
   so transfer-shaped messages were routinely attributed to the wrong `account_owner`. Never
   reintroduce a second source of truth for this list.
1. `app/services/extraction.py::extract_transactions` → GPT-4o-mini with structured output
   (`response_format=TransactionList`, a Pydantic model) to pull amount, currency, category,
   payment method, transaction type, and date out of the raw text. The raw input itself
   is kept as `description` rather than an LLM-generated summary — earlier versions summarized and
   that drifted into non-English languages.
2. V1 intentionally rejects input containing more than one detected expense (asks the user to
   resend one at a time) — `TransactionList` already supports multiple, this is just a product
   guardrail, not a technical limit.
3. If `amount` came back `None`, the entry is abandoned with a prompt to try again — the extraction
   prompt is deliberately told never to guess an amount, so a missing one means the input genuinely
   didn't contain a price. This is the branch a non-expense message ("hello") lands on.
4. Payment method / account owner inference (`app/bot_core.py::apply_payment_defaults`, a
   standalone function so it can be unit-tested without a Telegram `Update`): if category is
   `YouTrip top-up` the payment method is forced to `PRIMARY_ACCOUNT_OWNER`'s default account and
   type is set to `Transfer`; otherwise a missing payment method falls back to `YouTrip` for
   non-SGD amounts or the sender's default account (index 0 in `ACCOUNT_OWNERS`) for SGD.
   `account_owner` is then derived by reverse-matching the payment method against
   `ACCOUNT_OWNERS` (case-insensitive), except `Cash`, which is always attributed to
   the sender.
5. The pending transaction is stashed in `context.user_data['pending_transaction']` and only
   written to the DB after the user taps the inline "Confirm" button (`handle_button_click`),
   which calls `app/add_expense.py::add_expense`.

### Storage
- `app/database.py` owns a lazily-created, pooled SQLAlchemy Core engine (`get_engine()` /
  `get_connection()`) reading `DATABASE_URL`. The ledger runs on Neon Postgres; the engine is not
  created at import time so loading this module (Alembic, `--help`, etc.) never hard-fails on a
  missing `.env`. Money is always stored as **integer cents**, never floats, per `docs/SCHEMA.md`
  — conversions to/from dollars happen only at the display/API boundary.
- Alembic (`alembic/versions/`) owns the schema, not `database.py` — there is no `CREATE TABLE` in
  application code. `0001_create_transactions_table.py` is the baseline and already includes
  `account_desc`; the old SQLite-era schema drift (that column existing only via a manual
  `ALTER TABLE` in the now-deleted `test_queries.py`, undocumented in `database.py`) is resolved.
  Schema changes go through a new Alembic revision (hand-written — this project uses Core, not the
  ORM, so there's no metadata for `--autogenerate` to diff against).
- The `transactions` table has an `idempotency_key` column with an `ON CONFLICT (idempotency_key)
  DO NOTHING` upsert in `app/add_expense.py`. `bot_core.py` generates this key when the confirm
  button is built, so a double-tap on "Confirm" (slow connection, impatient re-tap) can't insert
  the same transaction twice. This is separate from — and already solves a narrower case than —
  the still-open webhook `update_id` dedupe below; don't conflate the two when reading the "Still
  outstanding" list in `ROADMAP.md`.
- Deduping duplicate Telegram webhook deliveries (`update_id`) is deliberately deferred rather than
  persisted as its own table/column: it's currently handled only in memory (`_seen_update_ids` in
  `bot_webhook.py`), which doesn't survive a restart. The original reasoning was that persisting it
  is low-value while voice-only ingestion keeps transaction volume low. **That reasoning is now on
  a clock**: as of 2026-08-20 the capture-friction work (backdated dates, edit/delete, text
  ingestion) is early Phase 0 and exists specifically to raise capture volume, so persisting the
  dedupe is scheduled in the same phase as the work that invalidates the deferral. See
  `ROADMAP.md`'s Phase 0 checklist for the full reasoning.
- A stray, empty `ledger.db` SQLite file sits at the repo root (untracked, harmless leftover from
  before the Postgres migration) — the real ledger is always the Postgres database at
  `DATABASE_URL`.
- `app/services/ledger_queries.py` holds the read-side aggregate queries backing the bot's
  `/recent`, `/today`, `/week`, `/month`, `/cat_today`, `/cat_week`, `/cat_month` commands. All
  currency-related aggregation is grouped by currency (multi-currency ledger, no FX conversion is
  performed anywhere in this codebase yet).
- `app/services/utils.py::get_sgt_now()` is the canonical "now" for period boundaries — always use
  it instead of `datetime.now()` so week/month cutoffs stay anchored to Singapore time regardless
  of where the process runs (e.g. UTC on Cloud Run).

### Backups
Neon's free-tier point-in-time recovery only covers the last 6 hours (capped at 1GB of changes),
so `.github/workflows/backup.yml` runs a separate, independent backup every 6 hours: `pg_dump -Fc`
(custom format, chosen over plain SQL for TOC-based inspection and selective/parallel restore as
the schema grows past one table) uploaded to `gs://unified-ledger-pg-backups-458614017842/`, with
a 30-day rolling retention enforced by a GCS Object Lifecycle rule (not application code). Auth is
Workload Identity Federation — no long-lived GCP credential is stored in GitHub, deliberately,
since this repo is public. This is a GitHub Actions workflow rather than a GCP-side Cloud
Scheduler job specifically so the schedule stays versioned and reviewable in the repo, avoiding
the kind of invisible-GCP-config blind spot that caused the `DATABASE_URL` gap (see
`ROADMAP.md`'s "What happened today (2026-08-05)"). See `docs/BACKUP_RESTORE.md` for the restore
procedure — a `pg_dump` backup is a full-database snapshot, not a per-transaction undo tool, and
production is never restored into directly.

### REST API (`app/main.py`)
Independent of the bot — a minimal FastAPI CRUD surface (`GET/POST /transactions`) using its own
Pydantic `Transaction` model. Defaults `account_owner` to `"Shared"` when unspecified, specifically
to avoid leaking a real owner's name when the caller doesn't provide one.

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
