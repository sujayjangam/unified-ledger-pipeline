# ADR-0023: Keep confirmation-card state in process memory, keyed by message id

**Status:** Accepted
**Date:** 2026-09-10
**Issues:** [#29](https://github.com/sujayjangam/unified-ledger-pipeline/issues/29), [#55](https://github.com/sujayjangam/unified-ledger-pipeline/issues/55)
**Code:** `app/bot_core.py::_remember_pending_card`, `::_take_pending_card`, `::_mark_card_resolved`, `::_card_outcome`

## Context

A household user tapped **✅ Confirm** on an expense card, saw nothing change, tapped again, and
the card was replaced with "⚠️ Session expired or data lost." The expense had in fact been saved,
exactly once.

Two separate defects produced that:

1. Nothing on the card changed between the tap and the end of the blocking database write, so the
   button read as dead and re-tapping was the natural response ([#55](https://github.com/sujayjangam/unified-ledger-pipeline/issues/55)).
2. The handler stored the awaiting-confirmation transaction in a single
   `context.user_data['pending_transaction']` slot and popped it unconditionally. After the first
   tap the slot was empty, so the second tap fell into an `else` branch that could not distinguish
   "saved a second ago" from "genuinely gone" — and guessed wrong every time
   ([#29](https://github.com/sujayjangam/unified-ledger-pipeline/issues/29)).

Fixing (2) requires the handler to remember, after a save, that a particular card is *finished*.
That memory has to live somewhere, and the choice of where is the decision recorded here.

The single-slot design had a second, unreported consequence: leaving one card unanswered and
sending another expense overwrote the slot, so tapping the older card's Confirm saved the *newer*
transaction. Any fix therefore had to key state per card, not per user.

## Decision

Two bounded dicts in `context.user_data`, both keyed by the `message_id` of the card the buttons
are attached to:

- `pending_cards` — `message_id → transaction`, replacing the single `pending_transaction` slot.
- `resolved_cards` — `message_id → 'saved' | 'cancelled'`.

Both are capped at `_CARD_CACHE_MAXLEN` (20) with oldest-first eviction, in the same shape and for
the same reason as the `_seen_update_ids` cache in `app/bot_webhook.py`: `user_data` lives for the
whole life of the process and nothing else prunes it.

State is therefore **process memory only** — the bot configures no PTB persistence — and is lost
on a Cloud Run cold start.

## Alternatives considered

**Look the answer up in Postgres.** Embed the `idempotency_key` in the button's `callback_data`
(`confirm_save:<uuid>` is 49 of Telegram's 64 available bytes) and, on a tap with no pending entry,
query `transactions` for that key: a hit means "already saved", a miss means genuinely lost. This
survives restarts, which the chosen approach does not.

Rejected because the durability it buys is mostly illusory here. The *pending transaction itself*
lives in the same process memory, so after a cold start there is nothing to save either way — the
card is genuinely dead and "please send it again" is the correct message, which is exactly what the
in-memory version already produces. The DB lookup would only change the wording of an
already-correct outcome, in exchange for a new read query, a database round trip on every tap, and
a `callback_data` format to keep in sync.

Making pending transactions themselves survive a restart is a real and larger question — it would
mean persisting them — and is deliberately not attempted here.

**Return `was_duplicate` from `add_expense`.** `app/add_expense.py` already computes whether the
`ON CONFLICT (idempotency_key)` clause suppressed the insert, but only `print`s it and returns a
bare `True` either way, so the caller cannot tell a fresh insert from a duplicate. Surfacing it
would let the handler detect a re-save after the fact.

Rejected as the wrong layer: it changes a signature shared with the CLI and `app/main.py` to
report on a call that, with `resolved_cards` in place, no longer happens. Detecting the second save
is worse than not making it. The gap is noted in `ROADMAP.md` rather than closed here.

## Consequences

**Bought:**

- A double tap leaves the `✅ Saved to Ledger!` card intact and answers with a toast. Combined with
  swapping Confirm/Cancel for a non-actionable "⏳ Adding to ledger..." button *before* the blocking
  write, the second tap is now both unlikely and harmless.
- "This entry is no longer available" appears only when it is true.
- Each card is independently confirmable. This closes the older-card-saves-newer-transaction bug,
  and means the open Phase 0 item "drop the one-expense-per-voice-note guardrail" needs no change
  to `handle_button_click`: one message will simply fan out into several cards in the same dict.
- A failed save restores the entry and its buttons rather than discarding it, keeping the original
  `idempotency_key` so a retry cannot write a second row.

**Cost:**

- After a Cloud Run cold start, a card that *was* saved reports "no longer available" rather than
  "already saved". Confirming it writes nothing (the pending entry is gone too), so this is
  misleading wording in a rare case, not a data risk.
- Both caches are bounded, so the 21st-oldest card in a single session degrades to the same
  message. At household volume this is unreachable in practice.
- `user_data` now holds two dicts where it held one value; the bound has to be maintained by the
  helpers rather than by the language.
