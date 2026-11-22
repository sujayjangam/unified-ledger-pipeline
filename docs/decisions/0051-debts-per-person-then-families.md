# ADR-0051: Debts are worked out per person, then rolled up to families

**Status:** Accepted
**Date:** 2026-11-19
**Issues:** [#128](https://github.com/sujayjangam/unified-ledger-pipeline/issues/128), [#129](https://github.com/sujayjangam/unified-ledger-pipeline/issues/129) (parent [#127](https://github.com/sujayjangam/unified-ledger-pipeline/issues/127))
**Code:** `app/services/balances.py`, `app/bot_core.py::handle_settle_button`
**Replaces:** in [ADR-0044](0044-trip-balances-from-splits.md), "shares inside the payer's family cancel"; in [ADR-0047](0047-pay-back-only-who-you-owe.md), "shares inside the payer's family still cancel", "lines cancel between the same two families", "money never moves through a third family" and "nobody from the receiver's own family pays on someone's behalf". The rest of both stays.

## Problem

`/trip_bals` treats a family as one wallet: a share inside the payer's family is dropped, and two
families' debts cancel even when different people are owed. So relatives can't settle between
themselves, and a person can be paid less than they put in.

## Decision

```mermaid
flowchart LR
    A["Every share:<br/>sharer owes payer"] --> B["Paybacks<br/>counted"]
    B --> C["Simplify, only along<br/>existing debt lines"]
    C --> D["Roll up to families<br/>where possible"]
    D --> E["To settle"]
```

- **Per person first.** Every share is a debt from the sharer to whoever paid. Your own share is
  nothing. All the maths is per person.
- **Each person's total never changes.** What each payer gets back, and what each person pays,
  is fixed by the bills and paybacks. Simplifying and rolling up only change who hands money to
  whom.
- **Simplified only along existing debt lines.** If P owes C, and U owes P and already owes C, U
  can pay C part of P's debt and pay P that much less. Opposite debts between the same two people
  cancel, and so does a loop of person-to-person debts (A owes B, B owes C, C owes A), never a
  loop through a family, which would move money between relatives' own totals. That is what lets
  a paid shortcut clear. Money is never sent between two people who share no debt. Persons and
  families both count. Each shortcut gets a plain-text 💡 line under "To settle".
- **Fewest payments wins.** When there's a choice, pick the one with the fewest payments in "To
  settle", counted after roll-up. Ties go to a fixed order (largest amount, then lowest id), so
  the list is the same every time. The search is bounded (`SIMPLIFY_BUDGET`): on random
  10-person trips, trying every choice took ~3.7 s per report, and 200 states took ~0.03 s,
  within one or two payments of it (measured 2026-11-19).
- **Rolled up where possible.** A family's debts to someone outside it are shown and paid as one
  ("Lee family pays Alice"). A debt inside a family can't be rolled up, so it stays per person
  ("Bob pays Alice").
- **Paybacks clear a line**, including a line inside a family. An overpayment is owed back to
  whoever handed the money over (ADR-0047).
- **Anyone can pay anyone, on anyone's behalf.** It clears the other person's debt, and nobody
  then owes the payer in `/trip_bals`. The `settlements` row (`for_participant_id`) is the record.
- **Two records.** Telegram shows "To settle" and "Already paid back", with each on-behalf payment
  marked "for …". A second settle-up, who repays the person who paid on their behalf, will only
  be in the planned Google Sheet (ADR-0044).
- **Members under each family** follow the trip's family labels (`trip_members.family`). Every
  member with a non-zero total is listed, whether they owe or are owed.
- **Per currency, no migration.** Splits and paybacks are already stored per person.

Example (fictional). Tans: Alice, Bob. Lees: Carol, Dave. Alice paid 400 and Bob paid 200, each
shared by all four.

| | Today | After |
|---|---|---|
| Debts | Lee → Alice 200, Lee → Bob 100 | per person, then Bob → Alice 50 net |
| To settle | Lee → Alice 200, Lee → Bob 100 (Bob and Alice never settle) | Lee → Alice 250, Lee → Bob 50 |
| Totals | Alice +200, Bob +100 | Alice +250, Bob +50 |

On behalf: the Ngs are abroad and pay in another currency, so the Lees pay Alice what the Ngs owe.
The Ngs drop out of "To settle". The Lees and the Ngs settle between themselves, using the record.

## Amended 2026-11-22: family to receiver on the "All in SGD" screen ([#171](https://github.com/sujayjangam/unified-ledger-pipeline/issues/171))

- On that screen only (ADR-0056, amended), a family pays as one: when two families owe each
  other, the smaller side's debts come off the larger side's lines to the same family, largest
  line first. Example (fictional): Carol owes Bob SGD 30 back after a family settle, and the Tan
  family owes Carol USD 40; the screen shows one line, Tan family pays Carol the difference.
- Each family's total is unchanged; inside a family, who ends up paying or receiving can shift.
  That's the same as a family settle (ADR-0058), which already pays for the whole family.
- A netted line saves the paybacks both ways (the receiver hands back the smaller side), so each
  per-currency screen still follows this record's rules and reaches zero.
- Then debts move along existing lines, as in each currency (#129), but across currencies and
  with the middle person's family in place of the middle person: "U pays p" and "p's family
  pays c", when U already pays c, become "U pays c more" and both others less, with the same 💡
  line. Nobody is ever sent to someone they didn't already pay. Example (fictional): the Lees
  owe Bob INR 500, Bob's family owes Alice SGD 300, and the Lees already pay Alice; the Lees pay
  Alice Bob's share instead, and the Ngs pay Alice that much less.
- It's saved as two ordinary paybacks, the Lees to Bob and Bob to Alice, so each currency's
  screen clears as before; Bob's own debt to Alice is cleared first.

## Rejected

- **A family as one wallet** (ADR-0044, ADR-0047): hides debts between relatives.
- **Cancelling two families' debts when different people are owed:** changes what each payer gets.
- **Simplifying through anyone** ("simplify debts", ADR-0047): sends people to someone they never owed.
- **Largest first instead of fewest payments:** more payments for no gain at trip size.
- **On-behalf debts in `/trip_bals`:** the giver and receiver settle in their own way (often
  another currency); the sheet holds the record.
