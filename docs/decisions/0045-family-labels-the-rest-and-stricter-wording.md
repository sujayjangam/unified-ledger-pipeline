# ADR-0045: Family labels, "the rest", and stricter wording over a model double-check

**Status:** Accepted
**Date:** 2026-11-17
**Code:** `app/services/splits.py::resolve_split`, `app/services/splits.py::_reread`,
`app/services/splits.py::_rest_count`, `app/services/help_topics.py`, `app/bot_core.py::help_command`

## Context

A real split-by-nights message gave no split at all. Names swapped, it read: "Carol paid 2717.84
for airbnb for 6 nights. Reds and Blues only staying 5 nights. Rest all stay 6 nights." The
model read it well, but Python lost it in three places:

- "Reds" and "Blues" are trip family labels, not anyone's name, so they matched nobody.
- The model gave Carol the bill's 6 nights as her own count (7 of 10 runs).
- The model left out the rest's 6 (6 of 10 runs), so everyone else had no count.

Fixing those showed two cases that still went wrong silently. A payer who "stayed 4 nights",
said away from "Carol paid", lost the 4. A missing count could be filled from someone else's
number in a later sentence.

## Decision

- **A name that matches no person is tried as a trip family label.** A person always wins.
- **"X paid … for 6 nights" gives X no count.** The 6 is the bill's length. "Her 4 nights", or
  naming X again with a count, still counts. X still shares.
- **Python reads the rest's count from its own sentence**, and that beats the model's. It's
  used only beside someone else's count, and skipped when that sentence has two numbers. With a
  trip on, "the rest" means everyone else on the trip.
- **A loosely read count gets "⚠️ Check {name}'s {unit}."** on the card, never silence. The
  split itself is unchanged, and the line never blocks a save.
- **Stricter wording, taught by `/help`**, rather than a second model call. `/help` is buttons
  with one or two examples per topic. Every example is a case in `scripts/check_extraction.py`,
  and a test fails if one isn't.

## Alternatives considered

- **Ask the model to double-check the meaning**, given the trip's names and labels. It would
  cost about $0.0001 more per message (measured: the call is ~3,530 tokens in, ~150 out, about
  $0.0006 today). It was deferred, not rejected. It needs a new answer shape, comparison logic
  and its own measurements, and it sends every trip member's name to OpenAI on every trip
  message.
- **Change the counts instead of warning.** For example, keep the payer's "stayed 4". Each
  rule like that is another guess about wording. A warning plus `/help` is safer for now.

## Consequences

- The real message was right in 29 of 30 model runs. The one miss was the model reading no
  split at all, because the message has no split word. "Split by nights" up front fixed that
  (8 of 8), so `/help` says to start with it.
- The split menu still can't change counts, so the fix for a "⚠️ Check" line is to send the
  entry again.
- One silent case is left. If the model drops every named count but keeps the rest's count,
  everyone ends up at the same count, which becomes an even split with no warning.
- The help text and the model check must change together.
