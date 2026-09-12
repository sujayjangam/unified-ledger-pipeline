# ADR-0027: Warn about a likely duplicate before the card is shown, matching on amount and currency

**Status:** Accepted
**Date:** 2026-09-12
**Issues:** [#58](https://github.com/sujayjangam/unified-ledger-pipeline/issues/58), under [#57](https://github.com/sujayjangam/unified-ledger-pipeline/issues/57)
**Code:** `app/services/ledger_queries.py::find_recent_duplicate`, `app/bot_core.py::_find_duplicate_of`, `::_duplicate_banner`, `::_duplicate_keyboard`, `::process_expense_text`, `::handle_button_click`

## Context

A household user logged an expense, believed it hadn't saved, and sent it again. Both copies
were saved ([#57](https://github.com/sujayjangam/unified-ledger-pipeline/issues/57)).

The `idempotency_key` can't catch this. It stops one card from being saved twice, but a re-sent
message gets a new card with a new key, so to the database it's a different transaction.
[#29](https://github.com/sujayjangam/unified-ledger-pipeline/issues/29) removed the false error
that caused this particular re-send. Two cases remain: someone re-sends because they aren't sure
an entry went through, and both household members log the same shared bill.

A duplicate that gets through is permanent, because saved rows can't be edited or deleted
([#22](https://github.com/sujayjangam/unified-ledger-pipeline/issues/22)). It inflates every total
that includes it.

A local experiment refused any insert whose `date` and `description` matched an existing row. It
showed two problems with checking at the insert:

- It refused genuine same-day repeats, such as a second coffee later that day.
- The bot still said "✅ Saved to Ledger!" for an entry it had refused, because `add_expense`
  returns `True` either way.

## Decision

Check for a likely duplicate in `process_expense_text`, after extraction and **before the
confirmation card is shown**, and warn rather than block.

- **A match** is an entry with the same amount (in cents) and currency, saved in the last 5
  minutes (`DUPLICATE_WINDOW_MINUTES`) by anyone in the household.
- **The window uses `created_at`**, the write time. `date` has no time of day
  ([ADR-0024](0024-created-at-write-time-column.md)).
- **A flagged card** shows a banner above the normal summary. It says who logged the earlier
  entry and how long ago, and quotes that entry's wording. The name comes from looking up
  `entered_by` in `ALLOWED_TG_IDS` ([ADR-0026](0026-entered-by-telegram-user-id.md)). It reads
  "You" for the same sender, and names nobody when the earlier row has no sender recorded.
- **"⚠️ Save anyway"** sends the same `confirm_save` as the normal Confirm button. It goes through
  the same path: the progress label, the double-tap guard, and the entry's own `idempotency_key`.
- **"❌ Cancel"** on a flagged card says "Duplicate transaction cancelled."
- **The flag is stored on the pending entry** (`suspected_duplicate`), because a button tap carries
  only the card's `message_id`. That's how a failed save puts the warning buttons back, not the
  normal ones.
- **A failed check gives a normal card.** The query returns `None` on any error, so a broken
  check never stops an entry being saved.
- **`add_expense` is unchanged**, and there's no migration.

## Alternatives considered

**Check inside the insert.** This was the local experiment: `WHERE NOT EXISTS` on `date` +
`description`. Rejected. It runs after Confirm, when the card already says "⏳ Adding to
ledger...", so the user can't decide any more. It also fails silently, and it blocks genuine
repeats.

**Match on the description too.** Rejected. `description` is the raw input
([ADR-0008](0008-raw-transcript-as-description.md)), and Whisper words the same spoken sentence
differently each time. A description match would rarely fire on voice notes.

**Block instead of warn.** Rejected. Two separate SGD 5.00 coffees within five minutes is
plausible, and nothing in the data can tell them apart from a re-send. A false warning costs one
tap. A missed duplicate is permanent.

**Only check the sender's own entries.** Rejected. Two people logging the same shared bill is one
of the two cases this exists for.

**Check again when Confirm is tapped.** The check only sees saved rows, so two cards for the same
spend that are both still unconfirmed don't see each other. Re-running the check on Confirm, and
turning the card into a warning if a match was saved in the meantime, would catch that. It costs
one extra read per Confirm. Rejected for now: each household member usually logs what they
themselves paid for, so two unconfirmed cards for one bill are unlikely at this scale.

## Consequences

**Bought:**

- The warning reaches the user while nothing has been saved, and says who logged the earlier
  entry.
- It covers both remaining cases: a re-send by the same person, and the same bill logged by two
  people.
- Overriding a false warning is one tap on "Save anyway", with no new save path to maintain.
- `add_expense` and its CLI and REST API callers are untouched.

**Cost:**

- **Every card now waits for one database read,** not just the Confirm tap. Neon's free tier
  suspends its compute after about 5 minutes idle, so after a quiet spell the wake-up delay moves
  from the Confirm tap to before the card appears.
- **False warnings:** any two entries with the same amount and currency within 5 minutes, even
  genuinely separate ones.
- **Misses:**
  - two cards that are both unconfirmed (see above);
  - a re-send more than 5 minutes later;
  - an extraction that reads the amount differently the second time.
- **The earlier entry's wording is shown with Markdown formatting,** like the card's existing
  Notes line. A description containing `*` or `_` can break the card's formatting there too.
