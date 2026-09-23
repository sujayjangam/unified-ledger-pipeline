# ADR-0030: Let the model describe the date and Python calculate it; warn on implausible dates

**Status:** Accepted (not yet implemented)  
**Date:** 2026-09-23  
**Issues:** [#15](https://github.com/sujayjangam/unified-ledger-pipeline/issues/15), via [#73](https://github.com/sujayjangam/unified-ledger-pipeline/issues/73), [#75](https://github.com/sujayjangam/unified-ledger-pipeline/issues/75), [#74](https://github.com/sujayjangam/unified-ledger-pipeline/issues/74)  
**Code:** `app/services/extraction.py::DateReference`, `::extract_transactions`, `app/services/dates.py` (planned), `app/bot_core.py::process_expense_text`

## Context

Every entry, voice or typed, is saved with today's date, whatever the user says. The model is
asked for a `YYYY-MM-DD` date and told only `Today is 2026-09-23`, without the weekday. To turn
"last Tuesday" into a date it has to work out the weekday first and then count back.

Two things make that a poor place for the logic:

- **It can't be tested.** The suite forbids network calls (`tests/conftest.py`), so date
  arithmetic done by the model has no automated check at all.
- **A wrong date is permanent.** Saved rows can't be edited yet
  ([#22](https://github.com/sujayjangam/unified-ledger-pipeline/issues/22)), and Phase 1's matcher
  pairs entries with statement lines inside a date window.

Typed text is now the main input, which adds formats a voice note never produces: "yday", "last
tues", "12 Sep" and numeric dates like "3/7".

## Decision

**The model describes the date and Python calculates it.** The model returns a structured
`date_reference` that records what was said about the date: nothing, today, *N* days ago, a
weekday, or a calendar day and month with an optional year. A pure function,
`resolve_date_reference(ref, today)`, turns that into a date. `today` is worked out once in
`process_expense_text` (Singapore time, via `get_sgt_now`) and passed to both the prompt and the
resolver, so the two can't disagree around midnight.

The rules the resolver applies:

- **"last Tuesday" and a bare "Tuesday"** both mean the most recent Tuesday *before* today, so
  either said on a Tuesday means a week ago.
- **"Tuesday last week"** means the Tuesday in the previous Monday-to-Sunday week, the same week
  boundary `/week` uses (`get_week_start`).
- **A date with no year** means its most recent past occurrence. "25 Dec" said in September means
  last December.
- **Numeric dates are day first**, as written in Singapore: "3/7" is 3 July. The prompt states
  this rule, and it is the one part the resolver can't enforce.
- **A date that can't be resolved** (such as 29 February in a non-leap year) falls back to today,
  with a warning.

**Implausible dates get a warning, not a rejection.** A ⚠️ line appears under the date on the
confirmation card when the date:

- is after today
- is more than 60 days before today, the span of a monthly statement plus catching up
- couldn't be worked out

It never blocks the save. This follows the duplicate warning's rule
([ADR-0027](0027-duplicate-warning-before-the-card.md)) that a check informs the user and the user
decides.

## Alternatives considered

- **Keep the model calculating the date, and improve the prompt** by adding the weekday, examples
  and a day-first rule. This is a smaller change, but the arithmetic stays untestable. It could only
  be checked by hand on the test bot, and every model or prompt change would need that manual check
  again.
- **A date-parsing library such as `dateparser`.** It is well tested for phrases like "last
  Tuesday", but it needs the date phrase already separated from the rest of the message, which is
  the job the model is doing anyway. It would also add a dependency to cover what about forty lines
  of Python can.
- **Reject implausible dates** the way a missing amount is rejected. This is stricter, but the user
  would have to retype everything to fix one field, which is the friction Phase 0 exists to
  remove.

## Consequences

- **The date maths is deterministic and fully unit-tested** with a fixed `today`, including year
  rollover and invalid dates.
- **The model's job shrinks** to reading the date, and reading is what it's good at.
- **Day-first reading of numeric dates still depends on the prompt.** It can only be checked on
  the test bot, and a US-style "7/3" typed on purpose will be read as 7 March.
- **The 60-day window is a guess.** It can be measured against real entries later, the way Phase 2
  measures the matcher's window.
- **No schema change.** `transactions.date` is still the resolved business date, and the reference
  is used only on the way in.
