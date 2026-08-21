# ADR-0020: Split the test-suite phase, and land CI scaffolding before the rest of capture reliability

**Status:** Accepted  
**Date:** 2026-08-21  
**Issues:** [#31](https://github.com/sujayjangam/unified-ledger-pipeline/issues/31), [#27](https://github.com/sujayjangam/unified-ledger-pipeline/issues/27)  
**Code:** `.github/workflows/` (not yet written), `ROADMAP.md` §3a/§3b/§3c

## Context

`ROADMAP.md` sequenced the whole test-suite section after capture reliability, reasoning that the suite's job is to prove every expense-recording path works, so it should run against the finished capture paths rather than the ones being replaced. Meanwhile every change is verified by one person running the bot by hand once, `main` is unprotected, and deployment is a GCP-side Cloud Build trigger invisible from this repo — the same blind spot [ADR-0012](0012-github-actions-over-cloud-scheduler.md) was written about.

## Decision

Split the section. The CI scaffolding and tests over code the capture work doesn't touch (§3a, tracked as [#31](https://github.com/sujayjangam/unified-ledger-pipeline/issues/31)) land before the remaining §1 capture items. End-to-end ingestion coverage (§3b) keeps its original place after §1. Test-only dependencies live in `requirements-dev.txt`, not `requirements.txt`.

## Alternatives considered

Keeping the original ordering and accepting an unguarded `main` until capture work completes; moving the entire test suite forward, including the end-to-end ingestion coverage; putting pytest in `requirements.txt` for a single dependency file.

## Consequences

The original reasoning is preserved rather than overridden — it was an argument about end-to-end ingestion tests specifically, and that is exactly the part that stays deferred. What moves forward is the part the argument never reached: a workflow, a lint step, an import smoke check, and pure-logic tests over money conversion, period boundaries, handler routing, extraction schema parsing and the `ON CONFLICT (idempotency_key)` path. None of those get rewritten by [#15](https://github.com/sujayjangam/unified-ledger-pipeline/issues/15), the edit path, or dropping the one-expense guardrail.

The cost is a deliberate deviation from a documented ordering, and a delay to capture work that is the project's binding constraint on data quality ([ADR-0017](0017-extend-phase-0-for-capture-friction.md)). It is accepted because the import check is what stops a merge from silently taking the deployed bot offline, and because #15 is the first change whose regressions are genuinely hard to see by hand — relative date parsing has week-boundary and SGT-versus-UTC edge cases that a manual spot-check misses precisely because it runs on one arbitrary day.

Keeping test tooling out of `requirements.txt` is what lets the clean-install step mean something: that file must keep describing what production needs, or it cannot answer [#27](https://github.com/sujayjangam/unified-ledger-pipeline/issues/27). Branch protection is enabled only after the checks are green — requiring a check that does not exist yet makes `main` unmergeable.

What this buys is narrow and worth stating plainly: a **soft gate** against the failure class that takes the deployed bot offline on startup — a syntax error, a bad import, a missing dependency, an import-time crash — enforced at merge by branch protection ([#34](https://github.com/sujayjangam/unified-ledger-pipeline/issues/34)), since a check that only reports on a pull request gates nothing. It does not catch a handler that throws at runtime, a malformed query, or a broken prompt; a green check must not be read as "the bot works". Coverage of the expense-recording paths is §3b and lands after `area:capture` completes.
