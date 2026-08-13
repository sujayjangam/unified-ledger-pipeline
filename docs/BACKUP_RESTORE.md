# Backup & restore

## Overview

- **Where**: `gs://unified-ledger-pg-backups-458614017842/backups/`
- **Cadence**: every 6 hours (`.github/workflows/backup.yml`, cron `0 */6 * * *`, plus
  `workflow_dispatch` for manual runs)
- **Retention**: 30 days, rolling — enforced by a GCS Object Lifecycle rule on the bucket, not by
  any code in this repo
- **Format**: `pg_dump -Fc` (custom format) — a **full-database point-in-time snapshot**, not a
  per-transaction undo log. There is no built-in way to "restore just this one row into
  production." Restoring means reconstructing an entire database as it existed at dump time,
  either into a scratch environment for inspection, or (destructively) over an existing one.

This backup exists because Neon's free-tier point-in-time recovery only covers the last 6 hours
(capped at 1GB of changes) and doesn't protect against a Neon account/provider-level failure — see
`ROADMAP.md`'s Constraints section.

## Listing backups

```bash
gcloud storage ls gs://unified-ledger-pg-backups-458614017842/backups/
```

## Downloading one

```bash
gcloud storage cp gs://unified-ledger-pg-backups-458614017842/backups/20260812T060000Z.dump ./backup.dump
```

## Inspecting a dump without restoring anything

`pg_restore -l` reads the custom-format table of contents without touching any database — useful
for confirming a dump contains what you expect before committing to a restore:

```bash
pg_restore -l backup.dump
```

## Restore target: Neon branch, not local Postgres

**Use a Neon branch as the primary restore-drill / data-recovery target.** Reasoning:

- Instantly provisions a version-matched Postgres endpoint with zero local install/Docker
  dependency.
- Fully isolated from production — a branch is separate compute/storage, and the app only ever
  points at `main`'s `DATABASE_URL`, so nothing can accidentally write through to prod.
- Trivial teardown: delete the branch when done, no lingering local state.

A local Docker Postgres instance (`docker run -e POSTGRES_PASSWORD=x -p 5432:5432 postgres:17`) is
the right fallback specifically when you want zero possibility of touching the Neon project at all
(e.g., Neon free-tier branch-count limits, or a fully offline check).

**Important nuance**: a fresh Neon branch is a **copy-on-write copy of live data**, not an empty
database. Branching from `main` alone does not prove a backup file works — it just gives you a
scratch Postgres that already has data in it. To actually prove the `.dump` file can reconstitute
the schema and data on its own, the branch must be **emptied first**, then restored into.

## Restore steps (Neon branch path)

**Do steps (c)-(e) from one place, in one continuous session — don't split them between the Neon
console SQL Editor and a local shell.** A 2026-08-13 restore drill split "empty the branch" (run in
the console) from "restore and verify" (run locally via `psql`/`pg_restore` against the pooled
`-pooler` endpoint) and got a false failure: the local session reported
`relation "transactions" does not exist` right after a clean, zero-exit-code `pg_restore`, while the
console SQL Editor still showed the branch's original pre-drop data, as if the `DROP SCHEMA` had
never taken effect there. Root cause was never fully isolated — but redoing the whole drill from a
single local `psql`/`pg_restore` session against the **direct/unpooled** endpoint, with the console
untouched after copying the connection string, resolved it cleanly on the first try. Treat the
console SQL Editor as a viewer for spot-checks, not as one leg of a multi-step operation that also
involves a local shell.

```bash
# a. Create a branch (Neon console, or `neonctl branches create --name restore-drill-YYYYMMDD`)

# b. Grab its connection string from the Neon console (direct/unpooled endpoint, same convention
#    as prod's DATABASE_URL — postgresql+psycopg-style host, no PgBouncer suffix). Use this same
#    $BRANCH_DATABASE_URL for every command below, in the same shell session.

# c. Empty it — this is the step that makes the drill meaningful:
psql "$BRANCH_DATABASE_URL" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
psql "$BRANCH_DATABASE_URL" -c "\dt"   # confirm: "Did not find any relations"

# d. Restore:
pg_restore --no-owner --no-privileges --clean --if-exists -d "$BRANCH_DATABASE_URL" backup.dump

# e. Verify — group by currency, not a flat SUM(amount): this is a multi-currency ledger with no FX
#    conversion anywhere in the codebase, so an un-grouped sum mixes currencies and proves nothing.
#    Compare against the same query run against prod at/near dump time; any gap should match rows
#    recorded strictly after the backup ran, nothing else:
psql "$BRANCH_DATABASE_URL" -c "SELECT currency, COUNT(*), SUM(amount) FROM transactions GROUP BY currency ORDER BY currency;"

# f. Delete the branch when done (console, or `neonctl branches delete restore-drill-YYYYMMDD`)
```

## Manual row-level reconciliation back to production

`pg_dump -Fc` backups are full-database snapshots. **Production must never have a backup
`pg_restore`'d directly on top of it** — that would discard every write made since the backup was
taken. The supported pattern:

1. Restore into a scratch Neon branch (steps above).
2. `SELECT` the specific `transaction_id`(s) you actually need from the scratch branch.
3. Manually re-insert or patch just those rows into production via `psql` or
   `app/add_expense.py`.

Treat the restored branch as a read-only reference for extracting what you need — never as a
direct restore target for prod.

## Selective restore as the schema grows

`-Fc`'s table-of-contents support means that once the schema grows past a single table (Phase 1's
staging table, Phase 3's `participants`/`transaction_splits`), a specific table can be pulled out
of a full-database dump without restoring everything else:

```bash
pg_restore -l backup.dump > toc.txt
# edit toc.txt down to just the entries you want
pg_restore -L toc.txt -d "$BRANCH_DATABASE_URL" backup.dump
```
