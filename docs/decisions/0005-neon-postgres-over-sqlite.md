# ADR-0005: Move the ledger from SQLite to Neon Postgres

**Status:** Accepted  
**Date:** 2026-08-03  
**Issues:** [#2](https://github.com/sujayjangam/unified-ledger-pipeline/issues/2)  
**PRs:** [#6](https://github.com/sujayjangam/unified-ledger-pipeline/pull/6)  
**Code:** `app/database.py`, `alembic/versions/`

## Context

The ledger started as a SQLite file. Cloud Run containers have ephemeral filesystems, so a file-based database cannot be the production store for a service that scales to zero, and the reconciliation phases need concurrent access and real migrations.

## Decision

Move to Neon Postgres, accessed through a pooled SQLAlchemy Core engine created lazily in `get_engine()`. Every query is `text()` with named binds.

## Alternatives considered

Cloud SQL (heavier, not free-tier friendly); keeping SQLite with a mounted volume (defeats scale-to-zero).

## Consequences

All 21 existing rows migrated with checksums matching the pre-migration file exactly (`SUM(amount)` = 100087720). The engine is not created at import time, so loading the module without a `.env` does not hard-fail. Neon's free tier auto-suspends when idle, which is why the pool uses `pool_pre_ping=True`; its point-in-time recovery is only 6 hours, which is what makes [ADR-0012](0012-github-actions-over-cloud-scheduler.md) mandatory rather than optional.
