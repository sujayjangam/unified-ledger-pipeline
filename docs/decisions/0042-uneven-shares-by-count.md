# ADR-0042: Uneven shares by a count per person, read by the model and checked in Python

**Status:** Accepted
**Date:** 2026-11-16
**Plan:** `docs/superpowers/plans/2026-11-16-splits.md` (PR 3)
**Code:** `app/services/extraction.py::SplitReference`, `app/services/splits.py`, `app/bot_core.py::_split_lines`

## Context

ADR-0039 splits an entry evenly and, when a message asks for uneven shares, records no split and
shows a warning. The group trip's accommodation needs uneven shares: a 5-night booking where some
members stay only 4 nights. `share_out` already takes a weight per person; what is missing is
reading the counts, keeping them through the split menu, and showing them on the card.

## Decision

- **Counts only, for now.** A share is amount × count ÷ total count, in whole cents through
  `share_out` (leftover cents as in ADR-0039). A fixed amount ("Carol's share is 50") and
  percentages are not built: they keep the "can't do uneven shares yet" warning and no split.
- **The model reads counts, never shares.** Each named person or family may carry a `count`; the
  reference may carry a `default_count` for every sharer not given their own, and a `count_unit`
  as said ("nights"). The model copies a count wherever it is said, including on an "except"
  entry; it is not asked to decide whether that person shares.
- **Python decides what "except" means.** An except entry with a count is a sharer at that
  count ("everyone except Carol's family who only stayed 4" is Carol's family at 4, the rest at
  the default); one without a count is left out. Asking the model to make this choice was
  tried first and made it drop plain exclusions ("everyone except Carol's family" lost Carol's
  family in 3 of 5 runs, 2026-11-16). If the message has an except word and nobody ends up left
  out or counted, the card warns and no split is recorded. In a split by count, an except entry
  with no count is refused when a number follows the except word: the count was probably lost
  (1 of 10 runs), and leaving that family out would be a wrong split that looks right.
  Family wording left in a name ("Carol's family") is stripped in Python and read as a family.
- **Python checks every count against the message.** A count is used only if the number appears
  in the raw message, as digits or a number word, the same way "last week" is read from the text
  (ADR-0031). Every sharer must end up with a count; otherwise a "couldn't read a count" warning
  and no split, never an even split. A family's count applies to each member. A default count
  nobody uses is ignored, and "everyone" or "the rest" listed as a person carries the default.
- **"Everyone" is read from the text.** The model's `everyone` is honoured only when the
  message says everyone (or all, the whole group); with people listed and no such word it is
  read as those people. A message with no split word gets through only when it is a split by
  count, every count checks out and it says everyone ("5 nights, everyone except ..."); every
  other message still needs a split word (ADR-0039).
- **The split menu keeps the counts.** They are kept on the pending entry, and the same row
  builder serves the message and every tap. Unticking keeps everyone else's count. A newly
  ticked person gets the default count (with none said, the most common count read, a tie to the
  larger), with a ⚠️ line naming them and their count. There is no count editor yet.
- **The card shows the count on each line**, under a header naming the unit: "👥 Split by nights
  between 10:", then " • Alice: 5, 241.97".
- **Only cents are stored.** No migration and no `split_type` column: `/balances` needs only the
  shares, and the counts are kept word for word in `description` (ADR-0008).

## Alternatives considered

- **Log the booking as two entries** (4 nights across everyone, 1 across the rest). Rejected in
  ROADMAP: one card charge should stay one entry, so reconciliation against the statement stays
  simple.
- **A weight column on `transaction_splits`.** It would let `/balances` explain a share later,
  but needs a production migration during the trip's deploy freeze. The raw message already
  holds the counts.
- **Any menu tap turns the split even.** Simpler, but a tap to add one forgotten person would
  silently undo the nights.
- **Fixed amounts in the same change.** The trip doesn't need them, and they bring their own
  edge cases (a fixed total above the amount, a fixed amount for a family).

## Consequences

- The trip's accommodation can be logged as one message and one entry.
- A count the model invents is caught when the number isn't in the message; a count it attaches
  to the wrong person is not, and is visible only on the card, which shows every count before
  Confirm.
- A newly ticked person's count is a guess, so the card says so. Changing a count means
  resending the message until a count editor exists.
- Old saved rows and new ones look the same in the database: only cents. Recovering the counts
  later means reading `description`.
