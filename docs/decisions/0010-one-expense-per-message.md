# ADR-0010: Reject input containing more than one expense (V1)

**Status:** Accepted  
**Date:** 2026-07  
**Code:** `app/bot_core.py::process_expense_text`

## Context

A single voice note can easily mention several purchases.

## Decision

If extraction returns more than one transaction, reject the whole message and ask the user to send one expense at a time.

## Alternatives considered

Presenting a confirm card per detected expense, or one card listing all of them.

## Consequences

This is a product guardrail, not a technical limit — `TransactionList` already models multiple transactions, and the extraction call already returns them. It exists because the confirm card and the pending-transaction slot in `context.user_data` both assume exactly one transaction, and getting multi-expense confirmation right is more work than it is worth before capture volume justifies it. Lifting it is tracked in `ROADMAP.md` under Phase 0.
