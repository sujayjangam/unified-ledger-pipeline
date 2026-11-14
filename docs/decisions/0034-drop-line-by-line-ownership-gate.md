# ADR-0034: Drop the line-by-line ownership gate before commit

**Status:** Accepted
**Date:** 2026-11-14
**Amends:** [ADR-0029](0029-close-phase-0-on-date-parsing.md) (only its "standing rule" on the
ownership pass; the rest of ADR-0029 stands)

## Context

ADR-0029 made the ownership pass through AI-assisted code a standing rule, and `ROADMAP.md`
turned it into a gate on every commit: nothing gets committed that couldn't be explained, line by
line, without the assistant in the room.

On 2026-11-14 the project took on a fixed deadline: group-trip splitting, pulled forward from
Phase 3 (see `ROADMAP.md`, Phase 1). With roughly 10-15 hours a week, the feature list doesn't fit
under a per-commit, line-by-line review. The owner's view is that the gate doesn't match how
AI-assisted engineering work is done now, at the pace it moves.

## Decision

Drop the per-commit gate. Code no longer has to be explainable line by line, without the
assistant, before it is committed.

The goal behind it stays: understand the codebase deliberately, especially the parts named in
`ROADMAP.md` as weak (async/await, Pydantic schemas, FastAPI's `lifespan`). What the gate guarded is
still covered by other means: the CI gate (`pytest`, `ruff`, the import check), the rules in
`CLAUDE.md`, and an independent review (Codex) for deeper changes.

## Alternatives considered

- **Keep the gate and cut scope.** Fewer trip features would ship. Rejected: the owner wants the
  full travel minimum working before the trip.
- **Suspend the gate for the trip only.** Rejected by the owner as not reflecting how they intend
  to work afterwards either.

## Consequences

- More code can be committed per hour, and some of it will be code the owner can't yet explain
  unaided. Where that matters (money, authorisation, migrations), review before merge is the
  safeguard.
- `ROADMAP.md`'s ownership constraint now cites this ADR as well as ADR-0029.
