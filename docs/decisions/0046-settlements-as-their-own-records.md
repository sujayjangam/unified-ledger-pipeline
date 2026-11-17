# ADR-0046: Paybacks are their own records, and can be in a different currency

**Status:** Accepted
**Date:** 2026-11-17
**Plan:** step 5 of group-trip splitting in `ROADMAP.md`
**Code:** `alembic/versions/0009_add_settlements.py`, `app/services/balances.py::unit_balances`,
`app/services/settlements.py`, `app/services/ledger_queries.py::get_trip_settlements`,
`app/bot_core.py::handle_settle_button`

## Context

`/trip_bals` (ADR-0044) says who owes whom, but nothing can say "she paid me back". Once
someone hands over money, the balance stays where it was, so the report is wrong from the
first payback onwards.

We needed this before loading a finished trip's hand-kept sheet: that sheet has a few rows
that are cash paid back, and there was nowhere honest to put them.

We looked at how Splitwise, Tricount and Settle Up do it. They all agree on the basics: a
payback is its own kind of record (not an expense), the balances are worked out again from
everything, and the app suggests the payments and lets you mark one as paid. Two things they
don't handle well for us:

- **Families.** Splitwise has no way to treat a family as one. We already do that, per bill.
- **Paying back in another currency.** On a trip it's normal to owe 600,000 IDR and hand
  over 50 SGD. Splitwise keeps currencies apart and can't link the two; Tricount converts at
  its own rate. Neither records what the two people actually agreed.

## Decision

- **A new `settlements` table.** One row per payback: who paid, who received, the trip
  (optional, so the later close-family tab `/bals` can use it too), the date, and who logged it.
  It never goes into `transactions`, so the spending totals never move. Money paid for someone
  is still your spending until they pay you back, and a payback isn't new spending.
- **Two amounts on every row.** `clears_cents` + `clears_currency` is the debt it pays off;
  that is what the balances use. `paid_cents` + `paid_currency` is the money that actually
  changed hands; that is for the future dashboard's "money in". In one currency, both sides are
  the same (a database check says so). In two currencies, the person states both. There is no
  exchange-rate lookup: the rate is whatever the two people agreed. The paid side can be any
  real currency (an ISO 4217 code, kept in `app/services/currencies.py`), e.g. INR for an SGD
  debt, even if no bill on the trip used it.
- **Person to person.** A payback always names the person who handed over the money and the
  person who got it. It lowers what the receiver is owed and lowers the debt of the payer's
  family (or the payer, if they settle alone), using the same family rules as the bills.
- **Bills minus paybacks, every time.** No bill is ever edited. `/trip_bals` reads the bills
  and the paybacks and works the balances out fresh. The subtraction happens in Python, inside
  `unit_balances`, because the family rules live there; a SQL view that subtracted per person
  would get families wrong.
- **Overpaying is allowed, but loud.** If someone pays back more than the suggested payment,
  the card shows a ⚠️ line, and the button reads "⚠️ Confirm overpayment" instead of "Confirm".
  It still saves. The extra simply flips around: the receiver now owes that much back to the
  person who paid.
- **Taps first.** Each suggested payment in `/trip_bals` gets a "Mark paid" button. That opens a
  confirm card, filled in from the suggestion. Buttons fix the details: who in the family handed
  over the money (the sender, if they're in that family; otherwise a menu, never a guess), a
  different amount, or a different currency for the paid side. Only the amounts are typed, as a
  reply. Nothing is saved until Confirm, same as an expense card.
- **The sheet loader uses the same path.** Cash paybacks in a hand-kept sheet are loaded as
  settlements through the same function, with the same checks and a fixed idempotency key per
  row, so running the loader twice can't add a payback twice.

## Alternatives considered

- **A payback as a negative or special expense in `transactions`.** Rejected: every total and
  every query would need a filter, and one missed filter would quietly count paybacks as
  spending (or as refunds).
- **A SQL view that subtracts paybacks from balances.** Rejected for the balances (the family
  rules are in Python). A view is still a good fit for the dashboard's simpler sums later.
- **Refusing overpayments.** Rejected by the user: real people round up, or pay for two
  things at once. Better to say so clearly and save it.
- **Converting currencies at a market rate.** Rejected: nobody pays back at the market rate,
  and a looked-up rate would leave both people a few cents out.

## Consequences

- A balance finally goes down when someone pays. A fully paid trip shows "Everyone is settled".
- The future dashboard can show "owed to me" net of paybacks, and "money in" in the currency
  that actually arrived.
- Inside a family, who gets paid back may shift between members (the family's debt still comes
  out of its largest credit first). The family's total is always right; it's an inside-the-family
  matter, same as with bills today.
- A wrong payback can't be fixed from the bot yet. The confirm card is the guard. Removing one
  comes with `/recent` delete (step 6). If time allows, an **Edit** step on the card will come
  too. Until then, a fix is a hand-written SQL statement.
- One additive migration (0009). It is applied to production by hand before the code that uses
  it is merged.
