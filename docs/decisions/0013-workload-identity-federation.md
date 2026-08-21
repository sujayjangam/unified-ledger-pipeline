# ADR-0013: Authenticate the backup with Workload Identity Federation, not a stored key

**Status:** Accepted  
**Date:** 2026-08-12  
**Issues:** [#7](https://github.com/sujayjangam/unified-ledger-pipeline/issues/7)  
**PRs:** [#18](https://github.com/sujayjangam/unified-ledger-pipeline/pull/18)  
**Code:** `.github/workflows/backup.yml`

## Context

The backup workflow needs to write to GCS and read one secret. **This repository is public.**

## Decision

Workload Identity Federation. A Workload Identity Pool and OIDC provider trust GitHub's token, with an attribute condition restricting it to this exact repository *and* `refs/heads/main`. The `pg-backup-runner` service account holds bucket-scoped `storage.objectAdmin` and secret-scoped `secretmanager.secretAccessor` on `DATABASE_URL` only — not project-wide.

## Alternatives considered

A service-account JSON key stored as a GitHub secret: far simpler to set up, and the common tutorial answer.

## Consequences

No long-lived GCP credential exists anywhere in GitHub, so there is nothing to leak or rotate. Setup is meaningfully more complex, and the attribute condition is easy to get wrong in a way that fails closed. The bucket additionally has uniform bucket-level access and public-access-prevention enforced.
