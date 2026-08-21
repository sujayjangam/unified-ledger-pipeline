# ADR-0002: Use the OpenAI API for transcription, not a local Whisper model

**Status:** Accepted  
**Date:** 2026-07  
**Code:** `app/services/transcription.py`

## Context

Voice notes have to become text before anything else can happen.

## Decision

Call the OpenAI Whisper API. Keep the call behind `transcribe_audio` so the implementation can be swapped without touching the bot.

## Alternatives considered

Running Whisper locally: maximum privacy, but needs PyTorch and FFmpeg in the image and meaningful RAM — a poor fit for a Cloud Run container sized for a bot.

## Consequences

Small image, fast cold starts, and a per-minute cost (~$0.006/min). That cost turns out to dominate the pipeline: it is roughly 10x the structured-extraction call, which is the basis of [ADR-0018](0018-text-ingestion-before-date-parsing.md).
