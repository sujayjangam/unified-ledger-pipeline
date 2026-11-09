---
name: github-issues
description: This repo's GitHub issue conventions - parent issues as problem statements, sub-issues, default assignee, the area label set, phases as milestones, superseding issues. Use when filing, re-scoping, labelling or organising issues, or when reading the issue list.
---

# GitHub issue conventions

Moved out of `CLAUDE.md` so it loads only when issues are being worked on. The one rule that must
always apply — **ask before filing** — stays in `CLAUDE.md`.

- **Parent issues are problem statements, not task descriptions.** Describe the user-facing
symptom/impact ("no way to tell which transaction occurred first"), not the fix ("add a
created_at column"). Sub-issues (`gh issue create --parent <#>`) break the fix into independently
completable, independently verifiable steps. See #2 (parent) → #4 (sub-issue), and #9 (parent) →
#10-#14 (sub-issues) as the reference pattern.
- **Default assignee: `sujayjangam`, always** (`gh issue create --assignee sujayjangam ...`).
Every issue in this repo should be assigned by default — don't wait to be asked per issue.
- **Ask before filing.** Don't create a GitHub issue proactively without the user asking for it in
the current conversation — offer to file one, or note it as a candidate, but wait for a go-ahead.
- **Supersede, don't abandon.** If an issue gets re-scoped into a new one, close the old one with
`gh issue close <old> --duplicate-of <new>` plus a comment explaining why, rather than leaving both
open or silently dropping one (see #8 → closed as duplicate of #9).
- **`ROADMAP.md` should not duplicate issue bodies.** Once something has a filed issue, `ROADMAP.md`
should link the issue number with a one-line status, not restate its repro steps/sub-tasks/
verification criteria — those live in the issue. `ROADMAP.md` stays the narrative/phase-level
layer: what phase we're in, what's currently blocking, and pointers to the issues that track the
detail.
- **Hierarchy for decomposition, labels for themes.** Parent/sub-issue means "these are the steps
that complete this one deliverable" — the parent closes when its children do (#9 → #10-#14). A
cross-cutting theme spanning independently-shippable issues is a *label*, never a grandparent
issue: GitHub allows 8 levels of nesting but an issue can have only **one** parent ever, so
spending that slot on a theme is unrecoverable, and a theme parent never closes.
- **Label set (added 2026-08-20).** Type reuses GitHub's defaults (`bug`, `enhancement`,
`documentation`). Area is four labels: `area:capture` (entry, ingestion paths, input UX),
`area:reconciliation` (statement parsing, matching, review queue), `area:data-integrity` (schema
correctness, wrong/missing values, doc-code drift), `area:infra` (deploy, backups, CI, secrets).
Keep the set small — large label sets rot. **Area labels go on parents and standalone issues only,
not on sub-issues**, so that filtering by area returns deliverables rather than their internal
steps.
- **Phases are milestones, not labels** (`Phase 0: Foundation & ownership`, `Phase 1:
Reconciliation engine`). An issue belongs to exactly one phase, which is what a milestone models,
and it gives a completion bar for free. Do not also create phase labels — that pair goes stale.
Sub-issues *do* get the milestone even though they don't get area labels, so the progress bar
counts real units of work. Later-phase milestones get created when they have issues to hold, not
in advance.
- **Reading the issue list without sub-issue noise:** `no:parent-issue` shows only top-level
issues, and `has:sub-issue` shows only true parents. Both work in the GitHub UI search box and via
`gh issue list --search "no:parent-issue"`. The default list view interleaves parents and children
and is much harder to read.
