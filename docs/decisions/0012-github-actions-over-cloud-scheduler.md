# ADR-0012: Run the scheduled backup from GitHub Actions, not Cloud Scheduler

**Status:** Accepted  
**Date:** 2026-08-12  
**Issues:** [#7](https://github.com/sujayjangam/unified-ledger-pipeline/issues/7)  
**PRs:** [#18](https://github.com/sujayjangam/unified-ledger-pipeline/pull/18)  
**Code:** `.github/workflows/backup.yml`, `docs/BACKUP_RESTORE.md`

## Context

Neon's free-tier point-in-time recovery covers only the last 6 hours, capped at 1GB of changes. That protects against an immediate mistake, not against account or provider loss, and this data is intended to live for years. An independent backup is required.

## Decision

A GitHub Actions workflow on a `cron` schedule, every 6 hours, dumping to `gs://unified-ledger-pg-backups-458614017842/` with 30-day retention enforced by a GCS Object Lifecycle rule rather than application code.

## Alternatives considered

Cloud Scheduler triggering a Cloud Run Job — the more idiomatic GCP answer.

## Consequences

The schedule stays versioned and reviewable in the repository. That was the deciding factor: this project had already lost time to invisible GCP-side configuration (a Cloud Build trigger nobody could see from the repo, plus a missing `DATABASE_URL` secret), and a GCP-only backup job would have reproduced exactly that blind spot. Cost: backups now depend on GitHub Actions availability as well as GCP. Failure alerting does not exist yet.
