# ADR-0063: A "Change category" button sets the category on the card

**Status:** Accepted
**Date:** 2026-11-28
**Code:** `app/bot_core.py::handle_category_change`, `::_card_categories`, `::_category_keyboard`

## Problem

The model picks the category from the words alone. "Spent 48.7aud for bunch at Locked In"
(a typo for brunch, at a café the model can't know) came back as "Other". Nothing in Python
can tell a venue name is a restaurant, and the only fix was Cancel and send the message again,
with another model call.

## Decision

- **A "🏷️ Change category" button** on every expense card opens the categories, two per row,
  the current one ✓, with ⬅ Back. A tap changes only `category`: split, payer and card stay.
- **Never the top-up.** "YouTrip top-up" is read from the message's words
  ([ADR-0062](0062-top-up-read-from-message-text.md)) and moves money into the wallet, so the
  menu leaves it out, a top-up card has no button, and a tap on a top-up card changes nothing.
- **Same rules as "Change card"** ([ADR-0057](0057-change-card-button.md)): card lock, answer
  once and last, toast only on resolved cards, never load the directory, redraw from a copy
  and change the pending entry only after the redraw worked. Callback prefix `categ:`
  (`cat:` is the category summary's).

Evidence: `tests/test_change_category.py`.

## Rejected

- **A prompt rule ("cafés and misspelt meals are Food"):** costs a `check_extraction` run and
  still can't know a venue the model has never heard of. It may still be worth adding later.
