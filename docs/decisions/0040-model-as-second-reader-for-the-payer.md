# ADR-0040: The model is a second reader for the payer, used only when Python finds nobody

**Status:** Accepted
**Date:** 2026-11-16
**Plan:** `docs/superpowers/plans/2026-11-16-splits.md` (decision 8)
**Code:** `app/services/participants.py::second_reader_payer`, `::mentions_paying`, `::match_person`, `::read_payer`, `app/services/extraction.py::TransactionSchema`, `app/bot_core.py::process_expense_text`

## Context

ADR-0037 has Python read the payer from the message with a few fixed patterns ("paid by X",
"X paid"), and keeps the model out of it so "split with Carol" can never become "Carol paid".
The cost was known then: a phrasing the patterns miss, such as "Carol covered dinner", shows the
sender as the payer with no warning. The "Change who paid" button (ADR-0038) fixes it in two
taps, but only if the person notices.

With splits (ADR-0039) the model already reads people's names from the message. It can also say
who the message says paid.

## Decision

- **The model returns one more field, `payer_name`:** the person the message says paid, copied as
  said, or null. It never picks an account for that person.
- **Python's patterns win.** When `read_payer` finds a payer, the model's answer is ignored.
- **The model's answer is believed only when the message has a paying word** (paid, covered,
  treat, shouted, and a few more; `mentions_paying`). The model reads the grammar; Python checks
  the word is there, as it does for "last week" (ADR-0031).
- **When Python finds nobody**, the message has a paying word, and the model's name matches one known person other than the
  sender (`match_person`, against the same candidates `read_payer` used), that person is passed to
  `resolve_payment` as the payer. The card then says "⚠️ Read as: Carol paid. Check."
- **Otherwise the sender paid**, as before.
- The payment rules don't change: an owned account still decides the payer, and an outside payer
  still goes on "Paid by someone else" (ADR-0037). When one of those rules already put its own
  warning on the card, that warning stays and the "Read as" line isn't added.
- Like every payer warning, the line is never stored and never blocks a save.

This changes one part of ADR-0037: the model may now suggest a payer. Python still decides
whether to use it.

## Alternatives considered

- **Keep adding patterns to `read_payer`.** Rejected as the only fix: "covered", "treat",
  "shouted", "got this one" and the rest don't end, and each miss is silent.
- **Let the model's answer win.** Rejected: where Python matches, it is deterministic and
  tested. The model is only better where Python has nothing.
- **Only warn ("⚠️ Did Carol pay?") and leave the sender as payer.** Kept as the fallback if
  `scripts/check_extraction` shows the model often naming a split partner as the payer. The bot
  works the answer out first and buttons fix it, so the first choice is to apply the reading and
  say so.

## Consequences

- "Carol covered dinner" now gives a card that says Carol paid, with a line asking for a check.
- A wrong reading by the model is visible on the card and fixed with "Change who paid". It is
  never silent.
- Measured 2026-11-16 with `scripts/check_extraction`, 20 runs a message, on the model's
  answer alone: it named a payer where the message only mentions a person, "Carol's birthday
  cake 55" in 3 runs, "bought Carol a coffee 6" in 2 of 10 and "dinner with Carol 70" in 1. A
  sentence in the field description about things bought for someone didn't stop it. With the
  paying-word check, all 35 messages were right in 20 of 20 runs.
- A way of saying someone paid that uses none of the words ("dinner was on Carol") is missed,
  and the sender is shown, as before this record. Words are added when real messages miss.
- The trap cases ("split with Carol", "dinner with Carol", "bought Carol a coffee") are in
  `scripts/check_extraction`, and are run after any change to the prompt.
- With trip mode off the candidates are the household only, so an outside payer is still not
  read on an everyday entry (a candidate follow-up in `ROADMAP.md`).
