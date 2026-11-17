# ADR-0047: Pay back only who you owe, and paying on someone's behalf

**Status:** Accepted
**Date:** 2026-11-17
**Plan:** step 5 of group-trip splitting in `ROADMAP.md`, with ADR-0046
**Code:** `app/services/balances.py`, `app/services/settlements.py`, `app/bot_core.py::handle_settle_button`

## Context

The first real use of `/trip_bals` on production gave a list like this:

```
3. Athale family pays Sujay 291.20
4. Bhagwat family pays Laura 254.16
5. Bhagwat family pays Sujay 37.04
```

Athale and Bhagwat only shared the Airbnb, which Laura paid. They had nothing to do with the car
rental Sujay paid, yet they were told to pay him. The totals were right. The list was not.

The cause was in ADR-0044: `suggest_payments` put everyone who is owed into one pile and paired
the biggest debtor with the biggest creditor. That is Splitwise's "simplify debts". It keeps the
number of payments down, but it sends people to someone they never owed, and in a family group
that just starts arguments.

Separately: sometimes someone pays another family's share for them, as a kindness. For example,
Varsha pays what Sudha Ajji owes Laura. Nobody then owes Varsha anything.

## Decision

- **Balances are kept as lines: "this family owes this person".** Each bill adds a line from
  every outside sharer's family (or the sharer, if they settle alone) to the person who paid,
  as before. Shares inside the payer's own family still cancel.
- **Lines only cancel between the same two families.** If the Lee family owes Alice (a Tan) 50
  and the Tan family owes Lee's Carol 20, the two cancel down to "Lee family owes Alice 30".
  Money never moves through a third family.
- **The suggested payments are those lines.** Nothing is pooled. Sometimes that means a payment
  more than before; we accept that, because every line is a debt the person recognises.
- **A payback pays down one line.** It clears "the payer's family owes the receiver". If it pays
  more than the line, the line flips: the receiver's family now owes the extra back to whoever
  handed it over.
- **Paying on someone's behalf is a gift.** On the payback card, "Who paid" can be anyone on the
  trip except the receiver's own family (money from inside it would be that family paying
  itself). If it's someone outside the family that owes, the card says so ("Varsha pays for Bhagwat
  family, as a gift"), and the payback clears that family's line. Nobody ends up owing the
  person who paid. It's stored as `settlements.for_participant_id`: a member of the family whose
  debt it clears (NULL means the payer's own family).
- **Only at payback time.** We considered letting a bill say "Varsha covers Sudha Ajji" when it
  is logged, and decided against it for now: one place to record it is enough, and the payback
  card shows it clearly.

## Alternatives considered

- **Keep pooling (simplify debts).** Rejected: fewer payments, but confusing, as above.
- **"X covers Y" read from the bill's text.** Dropped by the user on 2026-11-17. It may come back
  later as its own decision.
- **Paying for someone creates a new debt to the payer ("fronting").** Not now. A gift is the
  common case; a loan between those two can be logged as its own entry.

## Consequences

- The suggested list can be a bit longer, but every line can be checked against the bills.
- Settling works line by line, which makes "Mark paid" exact and an overpayment easy to explain.
- ADR-0044's "family debt comes out of its own members' credits" is no longer needed: shares in
  the payer's family cancel per bill, and families only net against each other in pairs.
- The family totals shown ("Lee family owes 30.00") are sums of the lines, so they don't change
  meaning.
