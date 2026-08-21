# ADR-0014: Take pg_dump custom format (-Fc) over plain SQL, and pin the client version

**Status:** Accepted  
**Date:** 2026-08-12  
**Issues:** [#7](https://github.com/sujayjangam/unified-ledger-pipeline/issues/7)  
**PRs:** [#18](https://github.com/sujayjangam/unified-ledger-pipeline/pull/18), [#21](https://github.com/sujayjangam/unified-ledger-pipeline/pull/21)  
**Code:** `.github/workflows/backup.yml`, `docs/BACKUP_RESTORE.md`

## Context

`pg_dump` can emit either a plain SQL script or its custom binary format.

## Decision

Use `-Fc`. Pin the client to `postgresql-client-18` from PGDG, matching Neon's confirmed server version, rather than an unversioned 'latest' package.

## Alternatives considered

Plain SQL: human-readable and restorable with `psql` alone.

## Consequences

Custom format allows TOC inspection (`pg_restore -l`) and selective or parallel restore, which starts to matter once the schema grows past one table — Phase 1's staging table and Phase 3's splits tables are committed work, not speculation. The version pin exists so behaviour does not drift silently as PGDG publishes new majors; **if the Neon project is upgraded to a new Postgres major, this pin must be updated in the same change, or backups will start failing with a client-older-than-server error.** Restore-verified on 2026-08-13 against a Neon branch.
