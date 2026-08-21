# ADR-0008: Keep the raw input as the description, not an LLM summary

**Status:** Accepted  
**Date:** 2026-07  
**Code:** `app/bot_core.py::process_expense_text`, `app/services/extraction.py`

## Context

Early versions asked the model to write a short description of the expense.

## Decision

Store the raw input verbatim as `description` — the Whisper transcript for a voice note, the message body for a typed entry. The extraction schema has no description field at all.

## Alternatives considered

An LLM-generated summary: tidier rows, but lossy and non-deterministic.

## Consequences

Summaries drifted into other languages (Malay and Indonesian in practice), and a summary discards the exact words spoken, which are the best evidence available when reconciling an entry against a statement line months later. Keeping raw text also makes future keyword-based auto-categorisation possible, since the description is a real sample of how the user talks about that merchant.
