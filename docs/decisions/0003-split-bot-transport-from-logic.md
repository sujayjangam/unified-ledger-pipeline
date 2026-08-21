# ADR-0003: Split the bot into a transport layer and a logic layer

**Status:** Accepted  
**Date:** 2026-07  
**Code:** `app/bot_core.py`, `app/bot_polling.py`, `app/bot_webhook.py`

## Context

Local development wants long polling (no public URL needed). Production on Cloud Run cannot use polling at all, because idle containers are put to sleep. Both need identical business logic.

## Decision

`bot_core.py` holds all handlers and a `get_application()` factory that builds and configures the bot but never starts a network loop. `bot_polling.py` runs it with `run_polling()` for local dev; `bot_webhook.py` wraps the same application in FastAPI behind `POST /webhook`, managing PTB init/shutdown through a `lifespan` context manager.

## Alternatives considered

A single entrypoint with a mode flag: fewer files, but the network concern leaks into the logic and the handlers become hard to exercise without a live connection.

## Consequences

The handlers are testable without touching Telegram, which is what makes the offline test suite possible at all. Cost: two runners to keep in sync, and one real trap — `run_polling()` deletes the bot's registered webhook, so running the local runner against the production token takes production down. See [ADR-0019](0019-separate-bot-token-for-local-testing.md).
