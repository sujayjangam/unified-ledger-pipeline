# Unified Ledger Pipeline

A household expense ledger that turns voice notes and typed messages into structured, confirmed
transactions in a multi-currency Postgres ledger.

## About

A household expense ledger with two ingestion paths into one database:

1. A Telegram bot that accepts voice notes *or* plain text messages, transcribes voice notes
   (OpenAI Whisper), extracts structured transaction data (GPT-4o-mini + Pydantic), and asks for
   confirmation before saving.

## How it works

**Telegram bot.** The bot is split into a transport layer and a logic layer so the same
business logic can run under two different runners:
- `app/bot_core.py` — the factory (`get_application()`), command/message handlers, and the
  `is_authorized()` allowlist gatekeeper.
- `app/bot_polling.py` — local dev runner (long-poll loop, no ngrok/webhook needed).
- `app/bot_webhook.py` — production runner, wrapping the same app in FastAPI behind a
  `POST /webhook` endpoint (needed because Cloud Run sleeps idle containers, so polling isn't
  viable there).

There are two ways to log an expense, and they converge on one pipeline. A voice note is
downloaded and transcribed via Whisper; a plain text message is used as-is. From that point both
are just raw text, handled by `process_expense_text` in `app/bot_core.py`: GPT-4o-mini with
structured output extracts amount, currency, category, payment method, transaction type, and
date, and the raw input is kept verbatim as the transaction description. The extracted
transaction is held pending until the user confirms it via an inline button, then written to the
ledger. Any other message type (photo, video, sticker, document, location) gets a reply saying
it isn't supported.

**REST API.** `app/main.py` exposes an independent `GET/POST /transactions` CRUD surface with its
own Pydantic model, for programmatic entry outside of Telegram. This will be deployed in the future.

**Storage.** Money is always stored as integer cents, never floats. The ledger runs on Neon
Postgres via a pooled SQLAlchemy Core engine, with Alembic owning schema migrations. Backed up
every 6 hours to GCS with 30-day rolling retention — see
[`docs/BACKUP_RESTORE.md`](docs/BACKUP_RESTORE.md).

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the reasoning behind these decisions.

## Tech stack

- **Language:** Python
- **Bot framework:** python-telegram-bot
- **API framework:** FastAPI + Uvicorn
- **Voice transcription:** OpenAI Whisper
- **Structured extraction:** OpenAI GPT-4o-mini with Pydantic-typed structured output
- **Database:** Neon Postgres, via SQLAlchemy Core (raw `text()` queries with named binds)
- **Migrations:** Alembic
- **Deployment:** Docker on Google Cloud Run

## Getting started

Run everything from the repo root (imports use the `app.` prefix; there are no `__init__.py`
files, so packages rely on Python's implicit namespace packages).

```bash
pip install -r requirements.txt

# Initialize the DB (Alembic creates the `transactions` table)
alembic upgrade head

# Run the Telegram bot locally (blocking long-poll loop, no ngrok/webhook needed).
# Polling deletes the registered webhook of whichever token it runs with, so for testing
# use the runner below instead - it drives a separate test bot. See docs/LOCAL_TESTING.md.
python -m app.bot_polling

# Run against a separate test bot, reading .env.local over .env
python -m app.bot_local

# Run the production-style webhook server locally
uvicorn app.bot_webhook:app_fastapi --reload --port 8080

# CLI expense entry
python -m app.add_expense --date 2026-07-29 --desc "Lunch" --amount 12.50 --cat Food

# View the ledger in a terminal table
python -m app.view_ledger
```

Docker (Cloud Run deployment target): `Dockerfile` installs `requirements.txt` and runs
`uvicorn app.bot_webhook:app_fastapi --host 0.0.0.0 --port 8080`.

### Required environment (`.env`, loaded via `python-dotenv`)

- `TELEGRAM_BOT_TOKEN`
- `ALLOWED_TG_IDS` — JSON object mapping Telegram user ID (string) → display name. Acts as the
  bot's allowlist.
- `ACCOUNT_OWNERS` — JSON object mapping a person's name → list of their payment
  accounts/cards, used to reverse-lookup `account_owner` from the extracted payment method.
- `OPENAI_API_KEY`
- `DATABASE_URL` — Postgres connection string (`postgresql+psycopg://...`)
- `WEBHOOK_URL` — optional, only used by `bot_webhook.py` to register the Telegram webhook.
- `ALLOWED_ACCOUNTS` — optional, feeds the extraction prompt's list of valid payment methods.

## Project status

Phase 0 (foundation & ownership) is in progress; the Postgres migration is code-complete and
verified live in production. See [`ROADMAP.md`](ROADMAP.md) for the current phase, what's
blocking, and links to the GitHub issues tracking active work.

## Docs

- [`docs/decisions/`](docs/decisions/) — numbered Architecture Decision Records: why each
  choice was made, what was rejected, and what it cost
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — index into the decision records, grouped by area
- [`docs/SCHEMA.md`](docs/SCHEMA.md) — canonical schema reference
- [`docs/BACKUP_RESTORE.md`](docs/BACKUP_RESTORE.md) — backup cadence/retention and the restore
  procedure
- [`docs/LOCAL_TESTING.md`](docs/LOCAL_TESTING.md) — running the bot locally against a separate
  test bot, without disturbing the deployed one
- [`ROADMAP.md`](ROADMAP.md) — current phase, status, and plan
