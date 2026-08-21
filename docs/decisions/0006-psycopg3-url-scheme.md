# ADR-0006: Use the postgresql+psycopg:// URL scheme, not postgresql://

**Status:** Accepted  
**Date:** 2026-08-05  
**Issues:** [#4](https://github.com/sujayjangam/unified-ledger-pipeline/issues/4)  
**Code:** `app/database.py`, `.env.example`

## Context

After the Postgres migration deployed, the first real confirm in production failed with `No module named 'psycopg2'` — despite `psycopg2` appearing nowhere in the code or requirements.

## Decision

`DATABASE_URL` must use the `postgresql+psycopg://` scheme.

## Alternatives considered

Installing `psycopg2-binary` alongside `psycopg` would also have made the error go away, while shipping two drivers for one database.

## Consequences

`requirements.txt` installs `psycopg` (version 3), but SQLAlchemy resolves a bare `postgresql://` URL to its default dialect, which is `psycopg2`. The `+psycopg` suffix names the dialect explicitly. Anyone writing a new connection string — local `.env`, Secret Manager, CI — has to know this, so it is documented in `README.md`, `CLAUDE.md` and `.env.example`. A related trap surfaced the same day: deleting and recreating a Secret Manager secret silently drops its IAM bindings, even when the name is unchanged.
