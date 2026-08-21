# ADR-0017: Extend Phase 0 for capture friction rather than opening Phase 1

**Status:** Accepted  
**Date:** 2026-08-20  
**Issues:** [#15](https://github.com/sujayjangam/unified-ledger-pipeline/issues/15), [#9](https://github.com/sujayjangam/unified-ledger-pipeline/issues/9), [#16](https://github.com/sujayjangam/unified-ledger-pipeline/issues/16), [#22](https://github.com/sujayjangam/unified-ledger-pipeline/issues/22)  
**Code:** Phase 0 of `ROADMAP.md`

## Context

Phase 0's original scope was nearly complete and Phase 1 (reconciliation) was next. But real usage was sitting well below what the system was built for.

## Decision

Extend Phase 0. Capture reliability comes first: backdated dates, business-date-vs-ingestion-timestamp ordering, an edit path, and text ingestion. The case-study README, architecture diagram and demo recording also move here from Phase 5, and the pytest suite is sequenced immediately before them.

## Alternatives considered

Opening Phase 1 on schedule and treating capture friction as polish to revisit later.

## Consequences

**Capture friction, not capacity or reliability, is the binding constraint on data quality.** Voice was the only input path, nothing could be corrected once saved, and every entry was stamped with today's date regardless of what was said — which forces logging at the moment of spend, exactly when a voice note is least usable. Reconciliation built on thin, unfixable, mis-dated data would measure the wrong thing. Documentation moved earlier because the decision log had grown large enough that writing it while the reasoning is fresh is cheaper and more accurate than reconstructing it later; the test suite precedes it because the suite proves the claim the README makes.
