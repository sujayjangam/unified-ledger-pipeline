# ADR-0041: The split menu, and people menus that offer every participant when no trip is on

**Status:** Accepted
**Date:** 2026-11-16
**Plan:** `docs/superpowers/plans/2026-11-16-splits.md` (PR 2)
**Code:** `app/services/splits.py`, `app/bot_core.py::handle_split_button`, `::_payer_tap`, `::process_expense_text`

## Context

ADR-0039 has the model read a split and Python work out the shares. A misread split, or one the
message never gave, could only be fixed by Cancel and resend. The card needs a menu to set who
shares an entry.

ADR-0038 has the "Change who paid" menu offer the people the message was read against: the
sender's trip members while a trip is on, otherwise the household. A friend added for a past trip
who pays an everyday bill can't be picked, and after "Not trip" the menu still offers the trip.

## Decision

- **The card gets a split button**: "👥 Add split", or "👥 Change split" when the card already
  has a split. None on a YouTrip top-up, a transfer, or an amount that won't convert to cents.
- **The menu changes the pending entry only, a tap at a time.** Each tap ticks or unticks a
  person or a whole family and redraws the card with the new even shares. Nobody ticked is a
  personal entry. "Done" closes the menu; "Remove split" unticks everyone and closes it. While the
  menu is open there is no Confirm button.
- **What the menu opens with.** What the message gave, if anything. With the entry tagged to a
  trip and no split read, the first opening ticks every trip member and applies that split at
  once, so the ticks and the card never disagree. Later openings never tick anyone by themselves.
- **Which list a menu shows is decided when it is drawn, from the card as it is now.** Tagged to
  a trip: that trip's members, by family. Not tagged (no trip on, or after "Not trip"): every
  participant, household first, then everyone else A-Z. People already ticked, or the payer shown
  now, are always in the list. This applies to the split menu and the payer menu alike, and
  replaces ADR-0038's "otherwise the household" for the payer menu.
- **Reading the payer from the text does not widen.** `read_payer` and the model's second reading
  (ADR-0040) still look at the trip's members or the household. More candidates would mean more
  one-word name clashes, and one tap in the menu covers the rare outside payer.
- **A tap never reads the database.** Both lists are copied onto the pending entry when the entry
  is logged, from the directory `authorize()` already loaded.
- **Warnings.** The first tap that changes the ticks clears the reading warnings ("Couldn't
  match 'Zed'"): the menu is now the answer. The uneven-shares warning stays until Confirm, worded
  for an even split once anyone is ticked, because an even split is still not what was asked.

## Consequences

- An outside payer picked from the wider menu goes on "Paid by someone else" through
  `change_payer`, as today.
- With no trip on there are no families in the menu, only people, until a household grouping
  exists (splits PR 2b).
- Ten people is ten buttons plus family rows: about eight rows on a phone, still one screen.
