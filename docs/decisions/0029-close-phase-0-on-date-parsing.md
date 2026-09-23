# ADR-0029: Close Phase 0 when backdated dates ship, and move the rest of its capture work to later phases

**Status:** Accepted  
**Date:** 2026-09-23  
**Supersedes:** the Phase 0 scope set by [ADR-0017](0017-extend-phase-0-for-capture-friction.md) — the extension itself stands; this record sets where it ends  
**Issues:** [#15](https://github.com/sujayjangam/unified-ledger-pipeline/issues/15), [#22](https://github.com/sujayjangam/unified-ledger-pipeline/issues/22), [#66](https://github.com/sujayjangam/unified-ledger-pipeline/issues/66), [#27](https://github.com/sujayjangam/unified-ledger-pipeline/issues/27), [#17](https://github.com/sujayjangam/unified-ledger-pipeline/issues/17)  
**Code:** Phase 0 and Phase 1 of `ROADMAP.md`

## Context

ADR-0017 extended Phase 0 because reconciliation built on "thin, unfixable, mis-dated data would
measure the wrong thing". Five weeks later, one of those three problems is solved and two are
still open:

- **Thin: solved.** Text ingestion ([#16](https://github.com/sujayjangam/unified-ledger-pipeline/issues/16))
  shipped on 2026-08-21, and typed text is now the main way entries come in.
- **Mis-dated: still open.** Every entry, voice or typed, gets today's date whatever the user says
  ([#15](https://github.com/sujayjangam/unified-ledger-pipeline/issues/15)).
- **Unfixable: still open.** No `UPDATE` or `DELETE` statement exists in `app/`
  ([#22](https://github.com/sujayjangam/unified-ledger-pipeline/issues/22)).

Meanwhile the Phase 0 checklist had grown to about twelve open items. Nothing said which of them
had to be done before Phase 1, so the phase had no finish line.

## Decision

Phase 0 ends when #15 ships. Every other open Phase 0 item moves to the phase whose work actually
needs it.

**To Phase 1**, because the reconciliation pipeline needs them:

- Structured logging, alongside the pipeline-failure alert that has to report through it.
- Acting on `needs_review`, because the matcher is what produces cases to review.
- Persisting the webhook `update_id` dedupe.
- `base_amount` correctness, because statements are the first source of converted amounts.
- Migrations tested from scratch, because the staging table is the next migration.
- Pinned dependencies ([#27](https://github.com/sujayjangam/unified-ledger-pipeline/issues/27)),
  because the PDF parser adds new ones.
- Correcting saved rows ([#22](https://github.com/sujayjangam/unified-ledger-pipeline/issues/22),
  including its first step, [#66](https://github.com/sujayjangam/unified-ledger-pipeline/issues/66)).
  This goes after the matcher, because the mismatches the matcher flags are the rows that need
  correcting.

**To Phase 4:** missing-amount recovery, and dropping the one-expense-per-message limit
([ADR-0010](0010-one-expense-per-message.md)). Both are capture conveniences that already have a
workaround: send the message again.

**To Phase 5:** the demo recording, which belongs with the packaging refresh, and deciding whether
to keep the unused REST API ([#17](https://github.com/sujayjangam/unified-ledger-pipeline/issues/17)).
The routing and end-to-end tests that were waiting on the finished capture paths move with the
capture items they cover.

**Becomes a standing rule, not a checkbox:** the ownership pass through AI-assisted code. It can't
ever be "finished", so it never belonged on a checklist.

## Alternatives considered

- **Finish every open Phase 0 item first.** This would delay the reconciliation engine by several
  more weeks, for improvements Phase 1 doesn't depend on.
- **Open Phase 1 now and leave #15 open.** Mis-dated rows are permanent while saved rows can't be
  edited. They are also the rows that the matcher's date window and the Phase 2 golden set will be
  built on.

## Consequences

- **Phase 1 starts after one more issue, not twelve.**
- **Capture stays rough for longer.** Until #22 and #66 land late in Phase 1, the only way to fix a
  wrong extraction is still to cancel and resend.
- **Phase 1 gets bigger.** Reliability items (logging, `update_id` persistence, pinned
  dependencies) now land alongside the pipeline that needs them, not as standalone work.
