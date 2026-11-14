# CLAUDE.md

Rules for working in this repo that the code can't tell you. Read the code for how things work;
read this for what must not be broken. The ADRs in `docs/decisions/` (index:
`docs/decisions/README.md`) say *why* each rule exists — read the relevant one before re-opening a
settled choice, and add a new record rather than editing an accepted one. `docs/SCHEMA.md` is the
canonical schema reference — read it before changing table columns.

## Start here

`ROADMAP.md` "Current status" says what phase we're in and what's next. Read it first, and update
it before ending a session that made progress. Don't start implementation work unless the user asks
for it in the current conversation, even if the roadmap lists it as next.

## Response style

Keep responses concise: lead with the answer, short paragraphs, plain prose over heavy formatting.
Expand detail only when the task genuinely needs it.

## What this is

A household expense ledger in Neon Postgres, fed by a Telegram bot (voice or text → Whisper →
GPT-4o-mini extraction → confirm card → save), a small FastAPI REST API (`app/main.py`), and a CLI.

## Keeping the docs current

This repo is public. No personal details, real names, real account names or job-search content in
any committed file; real participant and account rows live only in gitignored files.

Every build, feature or fix includes a check of whether `README.md`, `ROADMAP.md` and this file
need updating. `README.md` only ever describes what has shipped; forward-looking language belongs in
`ROADMAP.md` and moves into `README.md` once it ships, never before. Where the local-only
`doc-checker` subagent exists (`.claude/agents/doc-checker.md`, gitignored), run it before
committing doc changes or when picking work back up after a gap; otherwise check by hand.

## Commands and environment

- The environment is the micromamba env `ledger-env` (`micromamba run -n ledger-env <cmd>`).
- Run everything from the repo root: imports use the `app.` prefix with no `__init__.py` files.
- Gate before merge (CI requires all three, `main` is protected by a ruleset —
  [ADR-0021](docs/decisions/0021-rulesets-over-classic-branch-protection.md)): `pytest`,
  `ruff check .`, and the import smoke check in `.github/workflows/ci.yml`.
- **`alembic upgrade head` targets production.** `alembic/env.py` reads `DATABASE_URL` from `.env`,
  never `.env.local`. To migrate a test branch, set `DATABASE_URL` in the shell and check the host
  `alembic current` prints. See `docs/LOCAL_TESTING.md`.
- **Never poll the production bot token** — polling deletes its registered webhook. Local runs use
  `python -m app.bot_local`, which loads `.env.local` (a separate test bot and a Neon branch) and
  refuses the production token ([ADR-0019](docs/decisions/0019-separate-bot-token-for-local-testing.md)).
- `tests/` never touches the network or a database: an autouse fixture in `tests/conftest.py`
  makes any connection fail, because locally `.env` is production. Stub queries and Telegram
  objects (see `tests/test_button_callback.py`). Test-only dependencies go in
  `requirements-dev.txt`, never `requirements.txt`.
- After any change to the prompt or schema descriptions in `app/services/extraction.py`, run
  `python -m scripts.check_extraction` (real model, no database), and add any real message the
  model misread to its list ([ADR-0031](docs/decisions/0031-last-week-from-message-text.md)).
- `scripts/migrate_to_postgres.py` can't run again but is kept on purpose as a reference. Don't
  delete it.

Env vars (`.env.example` shows the format):
- `DATABASE_URL` must use `postgresql+psycopg://` — psycopg v3; a plain `postgresql://` makes
  SQLAlchemy look for the uninstalled psycopg2 ([ADR-0006](docs/decisions/0006-psycopg3-url-scheme.md)).
- `WEBHOOK_SECRET_TOKEN` is required whenever `WEBHOOK_URL` is set. `/webhook` rejects a delivery
  without it *before* parsing the body or recording the `update_id`; that is what makes the sender
  ID `authorize()` trusts trustworthy ([ADR-0025](docs/decisions/0025-webhook-secret-token.md)).
- Who may use the bot, and their cards, are rows in `participants` and `accounts`, never env vars.
  The old `ALLOWED_TG_IDS`, `ACCOUNT_OWNERS` and `PRIMARY_ACCOUNT_OWNER` are gone; never bring a
  second copy of that list back.

## Bot rules

- `app/bot_core.py` holds all logic; `bot_local.py` (polling) and `bot_webhook.py` (production,
  Cloud Run) are transports only ([ADR-0003](docs/decisions/0003-split-bot-transport-from-logic.md)).
- **Put new extraction or inference logic in `process_expense_text`**, never in a handler —
  anything in a handler only works for that one input type.
- The model may choose only from `directory.account_names()` (closed accounts included, so a refund
  on a cancelled card can still be named). Never add a second source for that
  list: a separate, never-set `ALLOWED_ACCOUNTS` var once ran the prompt on a hardcoded fallback and
  attributed entries to the wrong owner.
- `description` is the raw input, never an LLM summary
  ([ADR-0008](docs/decisions/0008-raw-transcript-as-description.md)). Whisper's `language="en"` is
  pinned ([ADR-0009](docs/decisions/0009-pin-whisper-language-en.md)). One expense per message is a
  product guardrail ([ADR-0010](docs/decisions/0010-one-expense-per-message.md)).
- The model never guesses an amount, and never calculates a date. It returns a `date_reference`;
  `app/services/dates.py` does the calendar maths in tested Python, counting from one SGT `today`.
  "Last week" is read from the message text, not the model
  ([ADR-0030](docs/decisions/0030-llm-reads-dates-python-resolves.md),
  [ADR-0031](docs/decisions/0031-last-week-from-message-text.md)). The date warning never blocks a
  save and is never stored.
- `resolve_payment` never guesses: no open wallet or funding card for a YouTrip top-up → refuse the
  entry (`WalletNotSetUp`).
- The duplicate check runs before the card is shown, never inside the insert, and a failed check
  gives a normal card — it must never block a save
  ([ADR-0027](docs/decisions/0027-duplicate-warning-before-the-card.md)).
- A pending entry lives in `context.user_data`, keyed by the card's `message_id`, and is written
  only on Confirm ([ADR-0023](docs/decisions/0023-in-memory-confirm-card-state.md)).
- In `handle_button_click`:
  - A callback query can be answered exactly once, so decide what to say before `answer()`.
  - On confirm, swap in the non-actionable "⏳ Adding to ledger..." button *before* the blocking
    save, or the button looks dead and gets tapped again.
  - A tap on a card already in `resolved_cards` gets a toast only. Never `edit_message_text` it
    ([#29](https://github.com/sujayjangam/unified-ledger-pipeline/issues/29)).
  - A failed save puts the entry back with its original `idempotency_key` and buttons.
  - A tap never loads the directory: what was confirmed is what is saved.

## Participants and accounts

([ADR-0032](docs/decisions/0032-participants-and-accounts-in-postgres.md))
- `authorize()` loads the directory on **every message**, never cached, so a row edit takes effect
  on the next message.
- **Fail closed.** If the directory can't be loaded, nobody gets in. Never fall back to letting a
  message through.
- Ids are `GENERATED ALWAYS AS IDENTITY`: never insert or reuse one. A closed account keeps its
  name and id with `closed_on` set; at most one *open* account may have a given name (ignoring
  case).
- Real rows are never committed (this repo is public). `scripts/seed_directory.py` copies them from
  the gitignored `directory_seed.local.json`; a plain run is a dry run, `--write` commits. Point
  `DATABASE_URL` at a Neon branch first. Tests use the fictional `directory` fixture.
- The REST API and CLI accept only an **open** account (unknown or closed → 422 from the API, exit
  1 from the CLI): there is nobody to ask "are you sure?".

## Storage

- The engine (`app/database.py`) is created lazily, never at import time, so importing any module
  works without a `.env` — the CI import check depends on it.
- **Money is integer cents.** In only through `dollars_to_cents`, out only through `format_cents`
  (both in `app/add_expense.py`). Never format a float amount yourself, and never split an amount
  by rounding each share — splits hand out whole cents
  ([ADR-0004](docs/decisions/0004-money-as-integer-cents.md),
  [ADR-0028](docs/decisions/0028-exact-cent-conversion-half-up.md)).
- Alembic owns the schema; no `CREATE TABLE` in application code. Revisions are hand-written
  ([ADR-0007](docs/decisions/0007-alembic-hand-written-migrations.md)).
- **Production is never migrated automatically**, and a merge to `main` auto-deploys. Run
  `alembic upgrade head` by hand *before* merging code that depends on a migration. Additive
  migrations are safe to apply first: no query uses `SELECT *` and every writer names its columns.
- `created_at` is the **write** time (Postgres default only); `date` is the **business** date.
  Never substitute one for the other: "within the last N minutes" uses `created_at`. An exact
  `00:00:00+08` on an old row means "time unknown"
  ([ADR-0024](docs/decisions/0024-created-at-write-time-column.md)).
- `entered_by_participant_id` stores the sender's id, never a name. NULL means "not recorded".
  Tests use fictional ids only ([ADR-0033](docs/decisions/0033-entered-by-participant-id.md)).
- **Old text columns (#53, in progress).** The bot, CLI and REST API write only the id columns
  (`account_id`, `entered_by_participant_id`). The old text ones (`account_desc`,
  `account_owner`, `entered_by`) are no longer written; they still hold older rows' values until
  migration 0005 drops them — see `ROADMAP.md`. Never write or read them in new code.
- `ON CONFLICT (idempotency_key) DO NOTHING` stops a double-tapped Confirm inserting twice
  ([ADR-0011](docs/decisions/0011-idempotency-key-over-update-id.md)). Duplicate webhook
  deliveries (`update_id`) are a different problem, deduped only in memory today.
- `get_sgt_now()` (`app/services/utils.py`) is the canonical "now" — never `datetime.now()`, or
  cutoffs drift to UTC on Cloud Run. Aggregates are grouped by currency; there is no FX conversion.
- Each ledger query catches its own errors. For `find_recent_duplicate` that is load-bearing:
  `None` means "show a normal card".
- Backups: `pg_dump` to GCS every 6 hours (`.github/workflows/backup.yml`); Neon's point-in-time
  recovery covers only 6 hours. Production is never restored into directly — see
  `docs/BACKUP_RESTORE.md`.

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
- After any worker writes code, Claude runs the gate (see "Commands and environment").
- Run workers in the background, and tell the user before starting anything longer than about a
  minute.
- Fences: workers only see this repo. No `--add-dir`, no `--dangerously-skip-permissions`, no
  content from other folders on this machine. Workers never read `.env`, `.env.local`,
  `directory_seed.local.json`, `claude-plan-*.md` or `Test PDF_CSV/`; never run `alembic`,
  `scripts/seed_directory.py`, the bot or anything else that opens a database connection; and never
  commit, push, deploy, or create issues or PRs.
- Delegate only when briefing is cheaper than doing it. Small fixes stay with Claude.

## GitHub issue conventions

- **Ask before filing.** Don't create a GitHub issue without the user asking for it in the current
  conversation — offer to file one, or note it as a candidate, but wait for a go-ahead.
- **Default assignee: `sujayjangam`, always** (`gh issue create --assignee sujayjangam ...`).
- The rest (parent issues, sub-issues, labels, milestones, superseding) lives in the
  `github-issues` skill, `.claude/skills/github-issues/SKILL.md` — read it before filing or
  reorganising issues.
