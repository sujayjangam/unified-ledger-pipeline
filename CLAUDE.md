# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Start here: check ROADMAP.md first

This project is under active, phased development. `ROADMAP.md` has a "Current status" section
that says exactly what phase we're in and what's next — read that before doing anything else, and
before re-reading source files to reconstruct context. Update it before ending a session that made
progress. Don't start implementation work unless the user asks for it in the current conversation,
even if the roadmap lists it as next.

## What this is

A household expense ledger with two ingestion paths into one SQLite database:
1. A Telegram bot that accepts voice notes, transcribes them (OpenAI Whisper), extracts structured
   transaction data (GPT-4o-mini + Pydantic), and asks for confirmation before saving.
2. A small FastAPI REST API (`app/main.py`) for programmatic entry.

See `ARCHITECTURE.md` for the reasoning behind the Cloud Run / OpenAI API decisions, and
`docs/system_flow.md` / `docs/voice_capture_mvp_flow.md` for the data lifecycle and bot UX flow.
`docs/SCHEMA.md` is the canonical schema reference — read it before changing table columns.

Note: `README.md` currently contains stray `requirements.txt` content (an accidental overwrite) —
it is not a useful source of information; use this file and `ARCHITECTURE.md`/`docs/` instead.

## Commands

Run everything from the repo root (imports use the `app.` prefix; there are no `__init__.py`
files, so packages rely on Python's implicit namespace packages).

```bash
pip install -r requirements.txt

# Initialize the DB (creates the `transactions` table at data/ledger.db if missing)
python -m app.database

# Run the Telegram bot locally (blocking long-poll loop, no ngrok/webhook needed)
python -m app.bot_polling

# Run the production-style webhook server locally
uvicorn app.bot_webhook:app_fastapi --reload --port 8080

# Run the standalone REST API
uvicorn app.main:app --reload

# CLI expense entry
python -m app.add_expense --date 2026-07-29 --desc "Lunch" --amount 12.50 --cat Food

# View the ledger in a terminal table
python -m app.view_ledger
```

There is no automated test suite. `test_queries.py` at the repo root is a one-off ad hoc DB
migration script (it ran an `ALTER TABLE ... ADD COLUMN account_desc`), not a pytest suite —
don't expect `pytest` to do anything meaningful here despite the filename.

Docker (Cloud Run deployment target): `Dockerfile` installs `requirements.txt` and runs
`uvicorn app.bot_webhook:app_fastapi --host 0.0.0.0 --port 8080`.

### Required environment (`.env`, loaded via `python-dotenv`)
- `TELEGRAM_BOT_TOKEN`
- `ALLOWED_TG_IDS` — JSON object mapping Telegram user ID (string) → display name. Acts as the
  bot's allowlist; anyone not in this map is rejected by `is_authorized()`.
- `ACCOUNT_OWNERS` — JSON object mapping a person's name → list of their payment accounts/cards,
  first entry is that person's default. Used to reverse-lookup `account_owner` from whatever
  payment method was extracted from the voice note.
- `OPENAI_API_KEY`
- `WEBHOOK_URL` — optional, only used by `bot_webhook.py` to register the Telegram webhook.
- `ALLOWED_ACCOUNTS` — optional, feeds the extraction prompt's list of valid payment methods.

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

### Voice note pipeline (`handle_voice` in `bot_core.py`)
1. Download the voice file to a temp `.ogg` (closed immediately after creation to avoid a Windows
   file-lock before Telegram writes to it).
2. `app/services/transcription.py::transcribe_audio` → OpenAI Whisper (`language="en"` is pinned
   deliberately — omitting it caused the model to randomly switch transcription languages).
3. `app/services/extraction.py::extract_transactions` → GPT-4o-mini with structured output
   (`response_format=TransactionList`, a Pydantic model) to pull amount, currency, category,
   payment method, transaction type, and date out of the raw transcript. The raw transcript itself
   is kept as `description` rather than an LLM-generated summary — earlier versions summarized and
   that drifted into non-English languages.
4. V1 intentionally rejects voice notes containing more than one detected expense (asks the user to
   resend one at a time) — `TransactionList` already supports multiple, this is just a product
   guardrail, not a technical limit.
5. Payment method / account owner inference: if category is `YouTrip top-up` the payment method is
   forced to the primary Sujay account and type is set to `Transfer`; otherwise a missing payment
   method falls back to `YouTrip` for non-SGD amounts or the sender's default account (index 0 in
   `ACCOUNT_OWNERS`) for SGD. `account_owner` is then derived by reverse-matching the payment
   method against `ACCOUNT_OWNERS` (case-insensitive), except `Cash`, which is always attributed to
   the sender.
6. The pending transaction is stashed in `context.user_data['pending_transaction']` and only
   written to the DB after the user taps the inline "Confirm" button (`handle_button_click`),
   which calls `app/add_expense.py::add_expense`.

### Storage
- `app/database.py` owns the SQLite connection (`data/ledger.db`) and `CREATE TABLE IF NOT EXISTS`
  for `transactions`. Money is always stored as **integer cents**, never floats, per
  `docs/SCHEMA.md` — conversions to/from dollars happen only at the display/API boundary.
- There is schema drift to be aware of: `add_expense.py` inserts into an `account_desc` column
  that is **not** in `database.py`'s `CREATE TABLE` statement — it was added later via a manual
  `ALTER TABLE` (see `test_queries.py`) run directly against the live `data/ledger.db` file rather
  than being codified in `database.py`. If you touch the schema, update both places.
- A second, empty `ledger.db` sits at the repo root (stray/unused) — the real database is always
  `data/ledger.db` via `app/database.py::DB_PATH`.
- `app/services/ledger_queries.py` holds the read-side aggregate queries backing the bot's
  `/recent`, `/today`, `/week`, `/month`, `/cat_today`, `/cat_week`, `/cat_month` commands. All
  currency-related aggregation is grouped by currency (multi-currency ledger, no FX conversion is
  performed anywhere in this codebase yet).
- `app/services/utils.py::get_sgt_now()` is the canonical "now" for period boundaries — always use
  it instead of `datetime.now()` so week/month cutoffs stay anchored to Singapore time regardless
  of where the process runs (e.g. UTC on Cloud Run).

### REST API (`app/main.py`)
Independent of the bot — a minimal FastAPI CRUD surface (`GET/POST /transactions`) using its own
Pydantic `Transaction` model. Defaults `account_owner` to `"Shared"` when unspecified, specifically
to avoid leaking a real owner's name when the caller doesn't provide one.
