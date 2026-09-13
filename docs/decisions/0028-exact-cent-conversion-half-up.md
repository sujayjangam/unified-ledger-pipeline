# ADR-0028: Convert amounts to cents with exact decimal maths, rounding half a cent up; split in whole cents

**Status:** Accepted
**Date:** 2026-09-13
**Issues:** [#39](https://github.com/sujayjangam/unified-ledger-pipeline/issues/39)
**Code:** `app/add_expense.py::dollars_to_cents`, `::format_cents`, `app/bot_core.py::_display_amount`, `app/main.py::add_transaction`

## Context

[ADR-0004](0004-money-as-integer-cents.md) stores money as integer cents. The conversion into cents
was `int(round(float(amount_dollars) * 100))`, and the REST API had its own copy of it.

**What that actually got wrong,** measured on 2026-09-12:

- **Every two-decimal amount from 0.01 to 1,000,000.00 converted correctly** (100 million
  amounts, as text and as floats).
- **Half-cent amounts didn't.** Of the 100,000 three-decimal amounts ending in 5 up to 1,000.000,
  50,000 rounded down: 1.005 became 100 cents, and 0.005 became 0 and was rejected.
- **Amounts above about 10 trillion dollars** also lost precision.
- **No production entry was affected.** None of the 47 rows' wording had more than two decimal
  places.

So no stored amount was wrong. Three things made it worth fixing now anyway:

- **The two conversions could disagree.** The bot and the REST API used separate copies of the
  logic.
- **The card could disagree with the save.** The confirmation card formatted the float itself,
  so fixing the conversion alone would have shown 1.005 as "1.00" and saved it as 1.01.
- **Future code needs one exact conversion to reuse.** Phase 1's statement parser will turn text
  into cents, and Phase 3's household splitting will produce amounts like SGD 5.55 / 2.

## Decision

**One conversion, exact.** `dollars_to_cents` builds a `Decimal` from `str(amount)`. `str()` turns
a float back into the shortest text that reads as the same float, which is the number that was
typed or extracted. It then rounds to the cent with `ROUND_HALF_UP` (1.005 → 101). It raises
`ValueError` for anything that isn't a positive, finite number of cents. The bot, the CLI and the
REST API all call it.

**Half a cent rounds up.** Two sources point the same way:

- **IRAS** rounds GST to the nearest cent with half a cent going up ($1.145 → $1.15), in its
  *GST: Guide for Retailers*.
- **EU Regulation 1103/97, Article 5,** rounds half-way conversion results up.

Both were quoted from the search index; neither page rendered in full here. It's also what a
person expects. It has to be set explicitly, because Python's `decimal` defaults to banker's
rounding.

**One way out, for display.** `format_cents` turns cents into text with whole-number maths.
Every bot card shows the amount through `_display_amount`, from the same cents value that is
saved.

**Splits share out whole cents; they never round each share.** This is recorded here because
it's the same question, but it's built in Phase 3.

- **Why not round each share:** a rounding mode can't keep split parts adding up. SGD 5.55
  between two is 277.5 cents each, and both half-up and banker's rounding make that 278 + 278 =
  556.
- **Store each person's share as whole cents** in `transaction_splits`, and check that the shares
  add up to the entry's amount. Reconciliation depends on that total, which was agreed before
  this record.
- **Work shares out by rounding every share down, then handing the leftover cents out one at a
  time.** Actual Budget and the Dinero.js and go-money libraries do the same.
- **Give leftover cents by a fixed rule, not at random,** so the same entry always splits the
  same way. The rule itself is a Phase 3 decision; the suggestion is whoever's card paid.
- **This replaces `split_ratio`,** the Decimal column in `docs/SCHEMA.md`. Nothing writes it
  today.

## Alternatives considered

**Keep the float conversion.** Actual Budget, an established open-source budgeting app, uses
`Math.round(amount * 100)`, so it clearly works in practice. Rejected: it's only right because
real input has two decimals. The statement parser and splitting shouldn't inherit a conversion
that is right by coincidence.

**Banker's rounding (`ROUND_HALF_EVEN`).** It's Python's default, and it avoids upward bias when
rounding many computed values. Rejected: it's surprising for a person (0.125 → 0.12), and it
disagrees with how IRAS rounds SGD. The bias argument needs volumes of rounded values this ledger
doesn't have.

**Reject amounts with more than two decimals.** Rejected: half cents will come from splitting
and parsed amounts, not only from typing. An error message for them would make every such path
handle a failure that a rounding rule settles.

**`Decimal(amount)` straight from the float.** Rejected: it carries the binary error in exactly,
so `Decimal(1.005)` is 1.00499999999999989…, which still rounds to 100.

**Round each split share, or store a ratio and multiply out later.** Rejected: the parts stop
adding up to the amount spent, which breaks reconciliation.

**Give leftover cents at random, as Splitwise does.** That's fair over time. Rejected for this
ledger: reconciliation needs the same input to give the same split.

## Consequences

**Bought:**

- Every path converts the same input to the same cents, and every card shows exactly the amount
  that is saved.
- Half-cent input is handled by a stated rule.
- The statement parser and splitting have one exact conversion to reuse, and the split rule is
  written down before any split code exists.

**Cost:**

- **Amounts still arrive as floats** from the LLM's JSON and the REST API's Pydantic model.
  `str()` recovers them exactly for any realistic amount (up to about 15 significant digits), but
  a float-free path would need `Decimal` or strings through those models.
- **The REST API now refuses zero or negative amounts** with a 422. It used to save them.
- **ADR-0004's Consequences section still quotes the old `int(round(float(...)))`.** This record
  supersedes that detail; ADR-0004's decision to store integer cents stands.

## Sources

- [Python `decimal` documentation](https://docs.python.org/3/library/decimal.html): the default
  rounding is `ROUND_HALF_EVEN`, and a Decimal made from a float is the float's exact binary
  value.
- [IRAS, *GST: Guide for Retailers*](https://www.iras.gov.sg/media/docs/default-source/e-tax/etaxguide_gst_gst-guide-for-retailers.pdf?sfvrsn=dab0b963_21):
  $1.145 may be rounded up to $1.15.
- [EU Regulation 1103/97](https://eur-lex.europa.eu/legal-content/EN/ALL/?uri=CELEX:31997R1103),
  Article 5: an exactly half-way result is rounded up.
- [Actual Budget, split transactions](https://actualbudget.org/docs/transactions/split-transactions/):
  split parts must equal the parent, and leftover cents are distributed one by one.
- [Actual Budget source, `amountToInteger`](https://github.com/actualbudget/actual/blob/master/packages/loot-core/src/shared/util.ts):
  `Math.round(amount * multiplier)`.
- [Splitwise support reply, 2013-11-12](https://feedback.splitwise.com/forums/162446-general/suggestions/3309275-rotate-who-pays-the-extra-penny-when-the-bill-cann):
  the extra penny is assigned at random.
- [Dinero.js `allocate`](https://www.dinerojs.com/docs/api/mutations/allocate) and
  [go-money](https://github.com/Rhymond/go-money): both split without losing a cent.
  Dinero.js splits 1003 50/50 into 502 + 501; go-money hands leftover pennies out round-robin.
