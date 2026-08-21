# ADR-0019: Use a separate bot token and database branch for local testing

**Status:** Accepted  
**Date:** 2026-08-21  
**Code:** `app/bot_local.py`, `.gitignore`, `docs/LOCAL_TESTING.md`

## Context

The obvious way to test a change before shipping is to run `python -m app.bot_polling` locally. With one bot token and one `DATABASE_URL`, that is not safe.

## Decision

Local testing uses a second bot token from BotFather and, where writes are involved, a Neon branch as `DATABASE_URL`. The production token is never used with the local runner.

## Alternatives considered

Testing against production and cleaning up afterwards; testing only in production after merge, which is the status quo.

## Consequences

Two independent hazards make this necessary. First, `run_polling()` **deletes the bot's registered webhook** — PTB's `_start_polling` passes `webhook_url=""` into `_bootstrap`, which deletes it. Production then goes dark and does not self-heal, because `bot_webhook.py` re-registers only on container startup and Cloud Run will not start a container with no updates arriving. Second, a single `DATABASE_URL` means local test entries are written into the real ledger — and there is currently no delete path in the product, so those rows are permanent.

Implemented as `app/bot_local.py`, which loads a gitignored `.env.local` with `override=True` before importing `bot_core`; because `load_dotenv()` defaults to `override=False`, `.env` still supplies every key `.env.local` omits, so the test overlay holds only the token and database URL rather than a second copy of every secret. Two startup guards enforce the decision rather than leaving it to discipline: the run aborts if the token matches `.env`'s, and aborts if `DATABASE_URL` is not overridden unless `ALLOW_PROD_DB=1` is set explicitly for that run. `.gitignore` gained `.env.*` (with `!.env.example`) at the same time — the pre-existing `.env` rule did not cover it, because git reads that as a literal filename rather than a prefix.
