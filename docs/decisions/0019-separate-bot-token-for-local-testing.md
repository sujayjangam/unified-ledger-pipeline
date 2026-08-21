# ADR-0019: Use a separate bot token and database branch for local testing

**Status:** Accepted  
**Date:** 2026-08-21  
**Code:** `app/bot_polling.py`, `.env`

## Context

The obvious way to test a change before shipping is to run `python -m app.bot_polling` locally. With one bot token and one `DATABASE_URL`, that is not safe.

## Decision

Local testing uses a second bot token from BotFather and, where writes are involved, a Neon branch as `DATABASE_URL`. The production token is never used with the local runner.

## Alternatives considered

Testing against production and cleaning up afterwards; testing only in production after merge, which is the status quo.

## Consequences

Two independent hazards make this necessary. First, `run_polling()` **deletes the bot's registered webhook** — PTB's `_start_polling` passes `webhook_url=""` into `_bootstrap`, which deletes it. Production then goes dark and does not self-heal, because `bot_webhook.py` re-registers only on container startup and Cloud Run will not start a container with no updates arriving. Second, a single `DATABASE_URL` means local test entries are written into the real ledger — and there is currently no delete path in the product, so those rows are permanent. Note `.gitignore` covers `.env` but not `.env.*`, so any additional env file must be added before it is created.
