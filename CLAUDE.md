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

A local-only `doc-checker` subagent (`.claude/agents/doc-checker.md`, gitignored, so it is not in
this repo or a fresh clone) automates the README-vs-code-vs-roadmap half of this check, and scans
`CLAUDE.md`/`ROADMAP.md` for non-engineering content (both are in a public repo). Where it exists,
run it — `@agent-doc-checker run the check` — before committing doc changes or when picking work
back up after a gap. Without it, do the same check by hand.

## Commands

Run everything from the repo root (imports use the `app.` prefix; there are no `__init__.py`
files, so packages rely on Python's implicit namespace packages).

```bash
pip install -r requirements.txt

# Initialize / migrate the DB (Alembic owns every table: `transactions`, `participants`, `accounts`).
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
python -m app.add_expense --date 2026-07-29 --desc "Lunch" --amount 12.50 --cat Food --account "Card A"

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

### Required environment (`.env`, loaded via `python-dotenv`)

`.env.example` shows the format of each one.

- `TELEGRAM_BOT_TOKEN`
- `OPENAI_API_KEY`
- `DATABASE_URL` — must use the `postgresql+psycopg://` scheme. This project uses `psycopg` v3; a
  plain `postgresql://` URL makes SQLAlchemy look for the uninstalled `psycopg2`
  ([ADR-0006](docs/decisions/0006-psycopg3-url-scheme.md)).
- `WEBHOOK_URL` — optional, only used by `bot_webhook.py` to register the Telegram webhook.
- `WEBHOOK_SECRET_TOKEN` — required whenever `WEBHOOK_URL` is set; `bot_webhook.py` refuses to
  start without a well-formed one. `/webhook` rejects any delivery that doesn't carry it back,
  *before* parsing the body or recording the `update_id` — that is what makes the sender ID
  `authorize()` trusts actually trustworthy. From Secret Manager in production
  ([ADR-0025](docs/decisions/0025-webhook-secret-token.md)).

`.env.local` (gitignored by the `.env.*` rule) is the local-testing overlay: only a test bot's
`TELEGRAM_BOT_TOKEN` and a Neon-branch `DATABASE_URL`. `app/bot_local.py` loads it with
`override=True` *before* importing `bot_core`, whose own `load_dotenv()` then fills in the rest
from `.env` — one copy of each secret on disk. See `docs/LOCAL_TESTING.md` and
[ADR-0019](docs/decisions/0019-separate-bot-token-for-local-testing.md).

Who may use the bot, and their cards, are **not** env vars: they are rows in the `participants`
and `accounts` tables (see "Participants and accounts" below). The three env vars that used to
hold them (`ALLOWED_TG_IDS`, `ACCOUNT_OWNERS`, `PRIMARY_ACCOUNT_OWNER`) are gone; never bring a
second copy of that list back.

## Architecture

### Bot: transport vs. logic split

The same business logic runs under two transports
([ADR-0003](docs/decisions/0003-split-bot-transport-from-logic.md)):
- `app/bot_core.py` — the factory (`get_application()`), all command/message handlers, and the
  `authorize()` gatekeeper. Builds and configures the bot but never starts a network loop.
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

0. The handler's `authorize()` has already loaded the `Directory` (participants and accounts)
   and passes it in. The account names the model may choose from are
   `directory.account_names()`, passed to `extract_transactions` — open accounts first, then
   closed ones, so a refund on a cancelled card can still be named. Never reintroduce a second
   source of truth for this list: a separate, never-set `ALLOWED_ACCOUNTS` var once made the
   prompt run on a hardcoded fallback and attribute entries to the wrong owner.
1. `extraction.py::extract_transactions` → GPT-4o-mini structured output (`TransactionList`). The
   raw input is kept as `description`, never an LLM summary
   ([ADR-0008](docs/decisions/0008-raw-transcript-as-description.md)).
2. More than one expense in a message is rejected — a product guardrail, not a technical limit
   ([ADR-0010](docs/decisions/0010-one-expense-per-message.md)).
3. No amount → the entry is abandoned with a prompt to retry. The prompt tells the model never to
   guess an amount, so this is where a non-expense message ("hello") lands.
4. `dates.py::apply_date_resolution` sets `date`. The model returns only a `date_reference` (what
   was said: nothing, today, N days ago, a weekday, a day and month); `resolve_date_reference`
   does the calendar maths in plain Python, counting back from the one SGT `today` that
   `process_expense_text` also passed to the prompt. Never let the model calculate a date again:
   that maths can't be tested. "Tuesday last week" vs "last Tuesday" is read from the message
   text by `mentions_last_week`, not the model, which couldn't tell them apart. A reference that
   can't be resolved falls back to today. It also sets `date_warning` (`dates.py::date_warning`):
   text for a ⚠️ line the card shows under the date when the date is in the future, more than 60
   days back, or couldn't be resolved. The warning never blocks the save and is never written to
   the database ([ADR-0030](docs/decisions/0030-llm-reads-dates-python-resolves.md),
   [ADR-0031](docs/decisions/0031-last-week-from-message-text.md)).
5. `participants.py::resolve_payment` (plain data in, so it can be unit-tested): a YouTrip
   top-up is paid from the wallet's `funded_by_account_id` card and becomes a `Transfer` — with no
   open wallet or funding card it raises `WalletNotSetUp` and the bot refuses the entry, never
   guesses. Otherwise a named account is matched (case-insensitive, closed ones included); no
   name, or one that matches nothing, becomes the `YouTrip` wallet for non-SGD or the sender's
   `default_account_id` for SGD. It sets `account_id`, the card's real name, `account_owner`
   (whose card paid; a shared account like Cash → the sender) and `closed_account`. A closed card
   gets a ⚠️ line and "⚠️ Save anyway" / "❌ Cancel", like a duplicate. The sender is
   `entered_by_participant_id`, not `account_owner`.
6. Duplicate check: `ledger_queries.py::find_recent_duplicate` looks for the same amount (cents)
   and currency saved by anyone in the last `DUPLICATE_WINDOW_MINUTES` (5), windowed on
   `created_at`. A match turns the card into a warning with "⚠️ Save anyway" / "❌ Cancel" (same
   `confirm_save` path). It runs before the card is shown, never inside the insert, and a failed
   check gives a normal card — it must never block a save
   ([ADR-0027](docs/decisions/0027-duplicate-warning-before-the-card.md)).
7. The pending entry is stashed in `context.user_data['pending_cards']`, keyed by the card's
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
- A button tap never loads the directory: the pending entry already holds `account_id` and
  `entered_by_participant_id`, so what was confirmed is what is saved.

### Participants and accounts (`app/services/participants.py`)

People and the accounts they pay from are rows in `participants` and `accounts` (migration 0004,
[ADR-0032](docs/decisions/0032-participants-and-accounts-in-postgres.md)); `docs/SCHEMA.md` has
the columns.
- `load_directory(conn)` reads both tables into a frozen `Directory` and runs
  `validate_directory`; any problem (a bot user with no open default card, a wallet funded by a
  closed card) raises `DirectoryError`.
- It is read on **every message** by `bot_core.py::authorize()`, never cached, so a row edit
  works on the next message. `_load_directory()` is the seam tests monkeypatch.
- **Fail closed.** If the directory can't be loaded, `authorize()` lets nobody in and replies
  "Couldn't reach the ledger". The allowlist is those rows: never fall back to letting a message
  through.
- A closed account keeps its real name and id, with `closed_on` set. At most one *open* account
  may have a name (ignoring case); a replacement card may reuse a closed card's name.
- Ids are `GENERATED ALWAYS AS IDENTITY`: never insert or reuse one.
- The real rows are never committed (this repo is public): `scripts/seed_directory.py` copies
  them from the gitignored `directory_seed.local.json`. Tests use the fictional `directory`
  fixture in `tests/conftest.py`.

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
- `entered_by_participant_id` is the **sender, as a participant id**. Store the id, never a
  name — names are looked up from the directory when shown. NULL means "not recorded" (old rows,
  CLI/REST API rows). Tests use fictional IDs only
  ([ADR-0033](docs/decisions/0033-entered-by-participant-id.md), superseding ADR-0026).
- **Dual-write (#53, in progress).** The bot, CLI and REST API write both the new id columns
  (`account_id`, `entered_by_participant_id`) and the old text ones (`account_desc`,
  `account_owner`, `entered_by`), so reverting a deploy loses nothing. A later PR stops writing the
  text columns and a migration then drops them — see `ROADMAP.md`. Until then, keep writing both.
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
`Transaction` model. POST takes a required `account` name, which must be an **open** account
(unknown or closed → 422 "Unknown or closed account"; there is nobody to ask "are you sure?"). A
shared account is written with `account_owner` `"Shared"`, never a person's name. The CLI's
`--account` follows the same rules.

### `scripts/`

`scripts/check_extraction.py` sends a fixed list of messages through the real model and reports
how often each one extracted the right amount and date. The test suite can't check this, because
it never calls the model. Run `python -m scripts.check_extraction` (it uses the OpenAI key from
`.env`, never the database) after any change to the prompt or the schema descriptions in
`extraction.py`, and add a message to its list whenever a real one is misread
([ADR-0031](docs/decisions/0031-last-week-from-message-text.md)).

`scripts/seed_directory.py` fills `participants` and `accounts` from the gitignored
`directory_seed.local.json` (format: `directory_seed.example.json`) and links old transactions to
them, matching each row's `account_desc` and its date against `closed_on`. It prints the database
host; a plain run is a dry run that rolls everything back, `--write` commits and asks about any
closed-card or shared-name row. Safe to rerun. Point `DATABASE_URL` at a Neon branch first.

`scripts/migrate_to_postgres.py` is the one-off SQLite → Postgres copy, run on 2026-08-01. Its
source file no longer exists, so it can't run again. It is kept on purpose as a reference — don't
delete it.

## Workers (Codex, agy)

Claude writes the brief, sorts the findings, runs the gate and commits. Workers do the heavy
reading and writing.

| Job | Worker |
|---|---|
| Deep code review, writing code from a brief | Codex |
| Doc cold reads, mechanical cross-checks, bulk reading and writing | agy, with its Claude models only: `claude-opus-4-6-thinking` for reviews and careful reads, `claude-sonnet-4-6` for mechanical work. Skip its Gemini models (2 of 4 claims were wrong in a 2026-11-12 trial). When agy is out of quota, Claude does the job. agy is extra capacity, not a second opinion; Codex is the second set of eyes. |

- Write the brief to `.work/briefs/<task>.md` (`.work/` is gitignored). Name the files and line
  ranges; don't paste their content. Say what the result must look like.
- Pipe the brief in on stdin (the repo path has a space, and PowerShell 5.1 mangles quoted
  arguments). Write output to `.work/out/`. Read only the worker's final message and
  `git diff --stat`.
  - Codex: `Get-Content .work\briefs\X.md -Raw | codex exec --sandbox read-only -o .work\out\X.txt -`
    (`--sandbox workspace-write` for code tasks). For a review of finished work,
    `codex exec review --uncommitted` or `codex exec review --base main`.
  - agy: `agy --model <model> --mode plan "--print=Read the file .work/briefs/X.md and do what it says. Use only file read and search tools, never shell commands." > .work\out\X.txt`
  - agy's `-p`/`--print` takes the prompt as its value and doesn't read stdin, so the prompt points
    at the brief file. Headless mode auto-denies shell tools; reviews don't need them.
  - agy only sees the files you name. Check its claims against code outside that scope before
    accepting them.
- Gate, run by Claude after any worker writes code — the same three checks CI requires: `pytest`,
  `ruff check .`, and the import smoke check in `.github/workflows/ci.yml`.
- Run workers in the background, and tell the user before starting anything longer than about a
  minute.
- Fences: workers only see this repo. No `--add-dir`, no `--dangerously-skip-permissions`, no
  content from other folders on this machine. Workers never read `.env`, `.env.local`,
  `directory_seed.local.json`, `claude-plan-*.md` or `Test PDF_CSV/`; never run `alembic`,
  `scripts/seed_directory.py`, the bot or anything else that opens a database connection; and never
  commit, push, deploy, or create issues or PRs.
- Delegate only when briefing is cheaper than doing it. Small fixes stay with Claude.

## GitHub issue conventions

- **Ask before filing.** Don't create a GitHub issue proactively without the user asking for it in
the current conversation — offer to file one, or note it as a candidate, but wait for a go-ahead.
- **Default assignee: `sujayjangam`, always** (`gh issue create --assignee sujayjangam ...`).
- The rest (problem-statement parents, sub-issues, default assignee, labels, milestones,
superseding) lives in the `github-issues` skill, `.claude/skills/github-issues/SKILL.md` — read it
before filing or reorganising issues.
