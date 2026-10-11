# ADR-0031: Tell "last Tuesday" from "Tuesday last week" with a text match, not the model; check prompts against the real model

**Status:** Accepted
**Date:** 2026-10-11
**Issues:** [#15](https://github.com/sujayjangam/unified-ledger-pipeline/issues/15) (via [#75](https://github.com/sujayjangam/unified-ledger-pipeline/issues/75)), [#77](https://github.com/sujayjangam/unified-ledger-pipeline/issues/77)
**Code:** `app/services/dates.py::mentions_last_week`, `::apply_date_resolution`, `app/services/extraction.py::DateReference`, `scripts/check_extraction.py`

Builds on [ADR-0030](0030-llm-reads-dates-python-resolves.md), which still stands: the model
describes the date and Python calculates it. This record moves one more piece from the first
side to the second.

## Context

ADR-0030's rules separate "last Tuesday" (the most recent Tuesday) from "Tuesday last week" (the
Tuesday of the previous Monday-to-Sunday week). Said on a Thursday, those are two days ago and
nine days ago.

The first build gave the model a `last_week` true/false field to mark the difference. Checked
against GPT-4o-mini on 2026-10-11, it marked "coffee 5 last tues" as last week in 5 of 5 runs.
Two rewordings did no better:

- a field named `previous_calendar_week`, with "'last Tuesday' is false" in its description:
  "last tues" and "last monday" marked true in 5 of 5 runs each
- a field asking the model to copy out the words it saw about the week: it wrote "last week" for
  "last monday" in 5 of 5 runs, a phrase the message doesn't contain

The error is silent. The date it produces is up to a week too early, which is well inside the
60-day window, so ADR-0030's plausibility warning wouldn't flag it either.

The same session found a second prompt problem
([#77](https://github.com/sujayjangam/unified-ledger-pipeline/issues/77)): "Lunch 15 yday" came
back with no amount in 13 of 13 runs. Neither problem could have been caught by the test suite,
which never calls the model.

## Decision

**The model no longer reports "last week".** `DateReference` has no field for it.
`dates.py::mentions_last_week` checks the raw message for the phrase "last week" (also "previous
week" and "wk"), as whole words in any case, and `apply_date_resolution` adds the result to the
reference before resolving it. The model still names the weekday, which it does reliably.

**Prompt wording is checked against the real model by a script.**
`scripts/check_extraction.py` sends a fixed list of messages, several runs each, through the real
`extract_transactions` and `apply_date_resolution`, and reports how often each one came out right.
It is run by hand after any change to the prompt or the schema descriptions. It isn't part of
`pytest`, because the suite never makes network calls.

## Alternatives considered

- **Keep rewording the field description.** Three versions failed the same way, which suggests
  the problem isn't the wording: the message contains "last", the field is about the previous
  week, and the model joins the two.
- **Run the script in CI.** It would catch a bad prompt change before merge, but it needs the
  OpenAI key in CI and its results vary between runs, so a pass or fail wouldn't always mean the
  same thing. Run by hand for now.

## Consequences

- **"last Tuesday" versus "Tuesday last week" is deterministic and unit-tested**, including
  capitals, "wk", and "weekend" not counting.
- **Only phrasings the pattern lists count.** "The Tuesday of the week before" wouldn't match, and
  would be read as the most recent Tuesday. Add to the pattern if real messages show other
  phrasings.
- **A prompt change now has a measurable check.** After this change, all 17 messages in the
  script passed 10 of 10 runs. That is evidence for the wording, not proof: the model can drift,
  and the list only covers the phrasings in it.
