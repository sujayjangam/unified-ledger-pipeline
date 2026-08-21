# ADR-0004: Store money as integer cents, never floats

**Status:** Accepted  
**Date:** 2026-07  
**Code:** `app/add_expense.py`, `docs/SCHEMA.md`

## Context

Floating point cannot represent most decimal fractions exactly, so repeated arithmetic on float currency drifts. A ledger that is intended to reconcile against real bank statements cannot tolerate that.

## Decision

The `amount` column is an integer number of cents. Conversion to and from dollars happens only at the display/API boundary.

## Alternatives considered

`NUMERIC`/`DECIMAL` would also be exact and is the more conventional choice for money in Postgres.

## Consequences

Exact arithmetic everywhere, and every writer must remember to convert. The conversion is centralised in `add_expense` (`int(round(float(amount_dollars) * 100))`). Aggregates in `app/services/ledger_queries.py` sum cents and divide only when formatting.
