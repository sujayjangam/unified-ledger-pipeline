# ADR-0015: Match statement lines with deterministic rules before reaching for an LLM

**Status:** Accepted (not yet implemented)  
**Date:** 2026-08  
**Code:** Phase 1-2 of `ROADMAP.md`

## Context

Reconciling parsed statement lines against ledger entries is the core of Phase 1.

## Decision

Deterministic rules first — amount plus a date window — scored against a hand-labelled evaluation set reporting precision and recall. Auto-match only on a *unique* candidate; zero or two-plus candidates route to `needs_review` rather than guessing. Semantic or LLM-based matching is not part of the first cut.

## Alternatives considered

LLM-based semantic matching from day one.

## Consequences

The matcher's behaviour is explainable and measurable, which is the entire point of the Phase 2 harness — an LLM matcher would be hard to evaluate and harder to defend. Prior art: Actual Budget runs a staged match (imported id, then amount + a ±7-day window + payee, then amount + window ignoring payee). Adopt the *staged structure*, but treat the window as a parameter the eval harness tunes rather than a constant to copy.
