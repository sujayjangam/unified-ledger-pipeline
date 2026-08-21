# ADR-0011: Dedupe saves with an idempotency key; defer persisting the webhook update_id

**Status:** Accepted  
**Date:** 2026-07-30  
**Issues:** [#1](https://github.com/sujayjangam/unified-ledger-pipeline/issues/1)  
**PRs:** [#3](https://github.com/sujayjangam/unified-ledger-pipeline/pull/3)  
**Code:** `app/add_expense.py`, `app/bot_webhook.py`

## Context

Tapping Confirm twice on a slow connection inserted the same transaction twice. Separately, Telegram retries webhook deliveries, which can replay the same update.

## Decision

Generate one `idempotency_key` when the confirm prompt is built — not per save attempt — and insert with `ON CONFLICT (idempotency_key) DO NOTHING`. Duplicate webhook deliveries are deduped separately, in memory only, via `_seen_update_ids`.

## Alternatives considered

Persisting seen `update_id`s in Postgres as well; a database-level uniqueness constraint on transaction content.

## Consequences

The idempotency key solves the double-tap case durably, at the database. The in-memory `update_id` cache does not survive a Cloud Run restart or a second instance, which is a real gap, deliberately accepted while voice-only ingestion kept volume low. **That premise is now expiring** — the Phase 0 capture work exists specifically to raise volume — so persisting the dedupe is scheduled in the same phase. Note the two mechanisms are not interchangeable and solve different races.
