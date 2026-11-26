# ADR-0062: A top-up or transfer counts only when the message says so

**Status:** Accepted
**Date:** 2026-11-26
**Code:** `app/services/participants.py::says_top_up`, `::says_transfer`, `::drop_unsaid_transfer`;
`app/bot_core.py::process_expense_text`

## Problem

The model read "Changed 500sgd to AUD for Carol" (cash from a money changer) as a YouTrip
top-up. A top-up card is a Transfer on the wallet's funding card, with no split, payer or card
buttons, so nothing on the card could fix it. A plain Transfer reading hides the split the
same way.

## Decision

```mermaid
flowchart LR
    M["Model: YouTrip top-up"] --> Q{"Top-up word<br/>or the wallet's name?"}
    Q -- yes --> T["Top-up, as before"]
    Q -- no --> E["Expense, category Other"]
    N["Model: Transfer"] --> R{"Transfer word?"}
    R -- yes --> X["Transfer, as before"]
    R -- no --> F["Expense, its own category"]
```

- Whether the words are there is something Python answers exactly (as in
  [ADR-0031](0031-last-week-from-message-text.md)), so it runs before `resolve_payment`.
- Top-up words: "top up" (any spacing or hyphen), "tops/topped/topping up", "topup", "YouTrip".
- Transfer words: any top-up word, "transfer…", "move/moved/moving", "send/sent", "withdrew…",
  "ATM", "deposit…", "credit card", "card bill", "cc bill". A bare "bill" is an expense.
- It only downgrades. Python never makes a top-up or transfer the model didn't read.

## Rejected

- A prompt rule: the prompt holds only what Python can't read
  ([ADR-0049](0049-prompt-holds-only-what-python-cant-read.md)), and it would need a paid run.
- "Change card" on top-up cards: still no split, and the reading would stay wrong.
