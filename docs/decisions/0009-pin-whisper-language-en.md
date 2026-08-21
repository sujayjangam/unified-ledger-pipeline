# ADR-0009: Pin Whisper to language="en"

**Status:** Accepted  
**Date:** 2026-07  
**Code:** `app/services/transcription.py`

## Context

With no language specified, Whisper auto-detects per request.

## Decision

Pass `language="en"` explicitly on every transcription call.

## Alternatives considered

Leaving auto-detection on, and correcting downstream.

## Consequences

Auto-detection switched languages unpredictably between requests on the same speaker, producing transcripts the extraction step then mishandled. Pinning removes that variance. The cost is real: genuinely non-English speech will be transcribed badly rather than detected, which matters for a household that mixes languages.
