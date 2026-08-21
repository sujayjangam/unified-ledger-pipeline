# ADR-0007: Let Alembic own the schema, with hand-written migrations

**Status:** Accepted  
**Date:** 2026-08-03  
**Issues:** [#2](https://github.com/sujayjangam/unified-ledger-pipeline/issues/2)  
**PRs:** [#6](https://github.com/sujayjangam/unified-ledger-pipeline/pull/6)  
**Code:** `alembic/versions/0001_create_transactions_table.py`

## Context

Before the migration the schema lived in a `CREATE TABLE` inside application code, and one column (`account_desc`) existed in the live database only because of an ad hoc `ALTER TABLE` in a throwaway script. The code and the database disagreed, undetectably.

## Decision

Alembic owns the schema. There is no `CREATE TABLE` in application code. Migrations are written by hand.

## Alternatives considered

`--autogenerate`: unavailable in practice, because this project uses SQLAlchemy Core rather than the ORM, so there is no declarative metadata for Alembic to diff the database against.

## Consequences

The baseline revision records the true live schema including `account_desc`, closing the drift. Every future schema change is a reviewable file. Cost: hand-written migrations are slower to produce and easier to get wrong than generated ones, so they need reading carefully.
