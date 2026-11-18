# ADR-0042: Uneven shares by a count per person

**Status:** Accepted (edited 2026-11-18)
**Date:** 2026-11-16
**Plan:** `docs/superpowers/plans/2026-11-16-splits.md` (PR 3)
**Code:** `app/services/extraction.py::SplitReference`, `app/services/splits.py`, `app/bot_core.py::_split_lines`

> **Edited 2026-11-18.** This record first said uneven shares were "counts only". That was
> wrong. Counts are one kind of uneven share, meant for things like hotel nights. Exact amounts
> per person ("Alice 45.50, Bob 74.50") are another kind, and they'll be typed into a reply
> prompt ([#112](https://github.com/sujayjangam/unified-ledger-pipeline/issues/112)). Accepted
> records aren't normally edited, but this one set a limit nobody meant to set.

## The problem

Splits were always even (ADR-0039). The trip's accommodation wasn't: on a 5-night booking, one
family stayed only 4 nights and should pay less.

## What we decided

**Share by count.** Each person pays amount × their count ÷ the total count, in whole cents
through `share_out`. Until #112 ships, a fixed amount ("Carol's share is 50") or a percentage
gets a warning and no split.

**The model reads the counts; Python checks them.** We don't trust the model with the tricky
parts, because in testing it got them wrong often:
- **Every count must be in the message.** If the number isn't in the text, it isn't used. If
  anyone ends up without a count, the card warns and saves no split. It never falls back to an
  even split.
- **Python decides what "except" means.** "Everyone except Carol's family, who stayed 4" means
  they share at 4. "Everyone except Carol's family" means they're left out. The model dropped
  plain exclusions in 3 of 5 runs when it made this call.
- **Python fills gaps from the text.** In "Carol and Erin 4", Erin gets the 4 (the model missed
  it 9 times in 10). The sender only shares if the message names them.
- **Python spots what isn't a count.** "Carol's share is 50" is an amount, not a count, so it
  gets the warning. If everyone has the same count, it's just an even split.
- **"Everyone" has to be in the text.** The model's guess doesn't count on its own.

**The split menu keeps the counts.** Unticking someone keeps everyone else's count. Someone newly
ticked gets the default count, and the card says so with a ⚠️ line.

**The card shows the counts**, for example "👥 Split by nights between 10:" then " • Alice: 5,
241.97".

**Only cents are saved.** No migration. The counts are still in `description`, which keeps the
message word for word.

## What else we looked at

- **Two entries for one booking.** No: one card charge should stay one entry, so it matches the
  bank statement.
- **A weight column in the database.** It needed a production migration during the deploy
  freeze, and the message already holds the counts.
- **Any menu tap makes the split even.** Simpler, but adding a forgotten person would quietly
  throw away everyone's nights.

## What follows

- A booking with different stays is one message and one entry.
- A made-up count is caught. A count on the wrong person isn't, but the card shows every count
  before Confirm.
- There's no way to change a count in the menu yet; you resend the message.
