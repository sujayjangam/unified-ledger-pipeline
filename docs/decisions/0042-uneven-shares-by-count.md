# ADR-0042: Uneven shares by a count per person

**Status:** Accepted (edited 2026-11-18, see below)
**Date:** 2026-11-16
**Plan:** `docs/superpowers/plans/2026-11-16-splits.md` (PR 3)
**Code:** `app/services/extraction.py::SplitReference`, `app/services/splits.py`, `app/bot_core.py::_split_lines`

> **Edited 2026-11-18.** This record first said uneven shares were "counts only". That was
> wrong. Counts are one kind of uneven share, built for things like hotel nights. Exact amounts
> per person ("Alice 45.50, Bob 74.50") are another kind, and they will be typed into a reply
> prompt rather than read from the message
> ([#112](https://github.com/sujayjangam/unified-ledger-pipeline/issues/112)). Normally an
> accepted record is never edited, but this one stated a limit nobody meant to set, so it is
> corrected in place.

## The problem

Until this change, a split was always even (ADR-0039). If a message asked for uneven shares, the
bot saved no split and showed a warning.

The group trip needed uneven shares for the accommodation. Picture a 5-night booking where one
family only stayed 4 nights. They should pay a little less. `share_out` could already hand out
cents by weight; what was missing was reading the counts from the message, keeping them through
the split menu, and showing them on the card.

## What we decided

**A share is worked out from a count.** Each person gets amount × their count ÷ the total count,
in whole cents through `share_out`, with leftover cents handed out as ADR-0039 says. This record
covers counts only. Exact amounts are covered by #112, and percentages aren't planned. Until
#112 ships, a message with a fixed amount ("Carol's share is 50") or a percentage still gets the
"can't do uneven shares yet" warning and no split.

**The model reads the counts. It never works out a share.** The model can put a `count` on each
person or family it names, a `default_count` for everyone else, and a `count_unit` such as
"nights". It copies a count wherever one is said, even next to "except". It doesn't decide what
"except" means.

**Python decides what "except" means.** "Everyone except Carol's family, who only stayed 4"
means Carol's family share at 4 and everyone else at the default. "Everyone except Carol's
family", with no number, means they're left out. We first asked the model to make this call, and
it dropped plain exclusions in 3 of 5 runs (2026-11-16), so Python does it instead. Two checks
guard against a count the model lost:
- If the message says "except", nobody ends up left out, and nobody has a count of their own,
  the card warns and saves no split.
- If a number follows the "except" word but the excepted family came back with no count, the
  split is refused. The count was probably lost (1 run in 10), and quietly leaving that family
  out would be a wrong split that looks right.

One gap remains: "5 nights, Carol 4, everyone except Dave" with Dave dropped by the model isn't
caught. The card shows every sharer, so it can be spotted before Confirm. Words like "Carol's
family" are stripped down in Python and read as a family.

**Every count must appear in the message.** A count is used only if its number is in the raw
text, as digits or a word ("four"), the same way "last week" is read from the text (ADR-0031).
Numbers are read as whole words, so the 3 in 2300, 3.50 or 3,000 never counts. Everyone sharing
must end up with a count. If someone doesn't, the card says "couldn't read a count" and saves no
split. It never falls back to an even split. A family's count applies to each person in it.
"Everyone" or "the rest" listed as a person gets the default count, and a default nobody uses is
ignored.

**Python fills in what the model misses.** If a listed person has no count, they get the first
number after their name: in "Carol and Erin 4", the model gave Erin nothing in 9 of 10 runs. In
a split by count, the sender only shares if the message names them ("me", "with", or a family
they belong to), because the model wrongly included them in 10 of 10 runs. The sender's count is
the first number after "me".

**Python rules out things that aren't counts.**
- A count split with only one person is refused. "Carol's share is 50" came back as Carol at a
  count of 50, which is an amount, not a count.
- Fixed-share wording ("share is 50", "owes 30", "40%") gets the "can't do uneven shares yet"
  warning, whatever the model read. #112 replaces this warning with a way to enter the amounts.
- If everyone ends up with the same count, it's just an even split. "Dinner for 4, split between
  Carol and Dave" came back as 4 each in 7 of 10 runs.

**"Everyone" has to be in the text.** The model's `everyone` only counts if the message says
everyone, all, or the whole group. With names listed and no such word, it means those names. A
message with no split word only gets through if it's a split by count, every count checks out,
and it says everyone ("5 nights, everyone except ..."). Every other message still needs a split
word (ADR-0039).

**The split menu keeps the counts.** They're stored on the pending entry, and the same code
builds the rows for the message and for every tap. Unticking someone keeps everyone else's
count. Ticking someone new gives them the default count (or, if none was said, the most common
count, with a tie going to the larger), plus a ⚠️ line saying who they are and what count they
got. There's no way to change a count in the menu yet.

**The card shows each count.** A header names the unit ("👥 Split by nights between 10:"), and
each line shows the count and the share (" • Alice: 5, 241.97").

**Only the cents are saved.** No migration and no `split_type` column. Balances only need the
shares, and the counts are still in `description`, which keeps the message word for word
(ADR-0008). A count given to someone ticked in the menu isn't in the message, so only their
cents are kept.

## What else we looked at

- **Logging the booking as two entries** (4 nights for everyone, then 1 night for the rest).
  Rejected: one card charge should stay one entry, so it matches the bank statement.
- **A weight column on `transaction_splits`.** It would let balances explain a share later, but
  it needed a production migration during the trip's deploy freeze, and the message already
  holds the counts.
- **Any tap in the menu turns the split even.** Simpler, but adding one forgotten person would
  quietly throw away everyone's nights.
- **Fixed amounts in the same change.** The trip didn't need them first, and they bring their own
  questions (amounts that add up to more than the bill, or an amount for a whole family). They
  are #112, done with a reply prompt instead of reading them from the message.

## What follows

- The trip's accommodation can be logged as one message and one entry.
- A count the model makes up is caught when its number isn't in the message. A count put on the
  wrong person isn't caught, but the card shows every count before Confirm.
- A newly ticked person's count is a guess, so the card says so. Changing a count means sending
  the message again until there's a count editor.
- In the database, rows saved before and after this change look the same: just cents. To get the
  counts back later, read `description`.
