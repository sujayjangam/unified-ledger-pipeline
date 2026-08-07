# Unified Ledger Pipeline

A household expense ledger that turns voice notes into structured, confirmed transactions in a
multi-currency Postgres ledger — with a REST API for programmatic entry alongside it.

## About

A household expense ledger with two ingestion paths into one database:

1. A Telegram bot that accepts voice notes, transcribes them (OpenAI Whisper), extracts
   structured transaction data (GPT-4o-mini + Pydantic), and asks for confirmation before saving.
2. A small FastAPI REST API (`app/main.py`) for programmatic entry.

## How it works

**Telegram voice bot.** The bot is split into a transport layer and a logic layer so the same
business logic can run under two different runners:
- `app/bot_core.py` — the factory (`get_application()`), command/message handlers, and the
  `is_authorized()` allowlist gatekeeper.
- `app/bot_polling.py` — local dev runner (long-poll loop, no ngrok/webhook needed).
- `app/bot_webhook.py` — production runner, wrapping the same app in FastAPI behind a
  `POST /webhook` endpoint (needed because Cloud Run sleeps idle containers, so polling isn't
  viable there).

A voice note is downloaded, transcribed via Whisper, then passed to GPT-4o-mini with structured
output to extract amount, currency, category, payment method, transaction type, and date. The
raw transcript is kept as the transaction description. The extracted transaction is held pending
until the user confirms it via an inline button, then written to the ledger.

**REST API.** `app/main.py` exposes an independent `GET/POST /transactions` CRUD surface with its
own Pydantic model, for programmatic entry outside of Telegram.

**Storage.** Money is always stored as integer cents, never floats. The ledger runs on Neon
Postgres via a pooled SQLAlchemy Core engine, with Alembic owning schema migrations.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the reasoning behind these decisions, and
[`docs/system_flow.md`](docs/system_flow.md) / [`docs/voice_capture_mvp_flow.md`](docs/voice_capture_mvp_flow.md)
for the full data lifecycle and bot UX flow.

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

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — design decisions and trade-offs
- [`docs/system_flow.md`](docs/system_flow.md) — end-to-end data lifecycle
- [`docs/voice_capture_mvp_flow.md`](docs/voice_capture_mvp_flow.md) — bot UX flow
- [`docs/SCHEMA.md`](docs/SCHEMA.md) — canonical schema reference
- [`ROADMAP.md`](ROADMAP.md) — current phase, status, and plan
