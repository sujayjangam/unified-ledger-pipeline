# ADR-0016: Support PDF statement exports rather than cleaner CSV exports

**Status:** Accepted (not yet implemented)  
**Date:** 2026-08  
**Code:** Phase 1 of `ROADMAP.md`

## Context

OCBC, DBS and YouTrip all offer statement exports in several formats.

## Decision

Target PDF exports.

## Alternatives considered

CSV exports, which are trivially parseable.

## Consequences

This is a deliberate choice of the harder path: messy real-world PDFs make a substantially stronger document-extraction story than parsing a clean CSV, and PDFs are what the banks actually give you by default. The parsing strategy is rule-based per bank format first (layouts are fairly consistent within a bank), with LLM-assisted extraction only as a fallback for lines the rules cannot handle — cheaper than always calling the model.
