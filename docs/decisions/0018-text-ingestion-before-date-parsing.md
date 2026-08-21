# ADR-0018: Ship text ingestion before backdated date parsing, and treat LLM cost as a non-constraint

**Status:** Accepted  
**Date:** 2026-08-21  
**Issues:** [#16](https://github.com/sujayjangam/unified-ledger-pipeline/issues/16), [#15](https://github.com/sujayjangam/unified-ledger-pipeline/issues/15)  
**Code:** `app/bot_core.py::process_expense_text`, `app/bot_core.py::handle_text`

## Context

Both backdated dates ([#15](https://github.com/sujayjangam/unified-ledger-pipeline/issues/15)) and text input ([#16](https://github.com/sujayjangam/unified-ledger-pipeline/issues/16)) were open capture-friction work, with #15 listed first on leverage grounds. Separately, there was a concern that routing typed entries through the LLM would be expensive.

## Decision

Do text ingestion first. Do not build a deterministic parse tier to avoid LLM cost.

## Alternatives considered

Doing #15 first as listed; building a rule-based parser in front of the LLM as a cost measure.

## Consequences

Text input is strictly cheaper to build — a refactor plus one handler registration, with no schema change, migration, dependency or prompt change — *and* it makes #15 far cheaper to verify, since typing a test phrase takes seconds where recording a voice note per case takes minutes. Leverage and ease are different axes; #15 keeps its leverage ranking and is next. On cost: extraction is roughly $0.0001 per entry (~450 in / ~60 out tokens on gpt-4o-mini) against ~$0.001 for a ten-second Whisper note, so **a typed entry that always calls the LLM is about 10x cheaper than the voice note it replaces**. Cost therefore does not justify a deterministic tier; latency and testability do, and that work sits in Phase 2 as auto-categorisation rules.
