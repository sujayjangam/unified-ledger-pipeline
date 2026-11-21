"""Who shares an entry, and how many cents each person's share is (ADR-0039).

The model only reads the message: it returns a `split_reference` saying who and how many
("named: Carol, plus the sender", "everyone except Dave"). Everything after that happens here,
in plain Python the test suite covers:

  - resolve_split turns the names as said into participants, and never guesses one.
  - share_out turns an amount and a weight per person into whole cents that add up exactly.
  - split_shares puts the two together into the rows the card shows and Confirm saves.

Nothing in this module touches the database, the model or Telegram.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from app.add_expense import dollars_to_cents, format_cents
from app.services.currencies import ISO_4217
from app.services.participants import Participant, match_person

# Card lines. Display only: never stored, and they never block a save.
UNEVEN_NOT_BUILT_WARNING = "⚠️ Uneven shares: tap 👥 Add split, tick who shares, then Set amounts. No split recorded."
UNEVEN_EVEN_SPLIT_WARNING = "⚠️ Uneven shares: tap Set amounts to type each share. Shown as an even split."
AMOUNTS_CLEARED_WARNING = "⚠️ Amounts cleared. Tap 👥 Change split → Set amounts again."
# The total varies, so the restart check recognises this fixed introductory line.
AMOUNTS_PROMPT_LINE = "Set split amounts"
EVERYONE_NEEDS_TRIP_WARNING = '⚠️ "Everyone" needs a trip switched on. No split recorded.'
NOBODY_SHARES_WARNING = "⚠️ Couldn't tell who shares this. No split recorded."
NO_MATCH_WARNING = "⚠️ Couldn't match '{name}'"
ADDED_AT_COUNT_WARNING = "⚠️ {name} added at {count} {unit}. Check."
EXCEPT_UNREAD_WARNING = "⚠️ Couldn't tell who is left out. No split recorded."
COUNT_UNREAD_WARNING = "⚠️ Couldn't read a count for everyone. No split recorded."
COUNT_EVEN_SPLIT_WARNING = "⚠️ Couldn't read a count for everyone. Shown as an even split."
# A count was read loosely: a payer's count dropped though it wasn't in their paying clause, or
# a missing count taken from a number in another clause. The split stands; the card asks.
CHECK_COUNT_WARNING = "⚠️ Check {name}'s {unit}."

# Words that mean the sender. A 'me' entry carries their explicit count, while
# include_sender alone includes them without specifying a count.
_SENDER_WORDS = frozenset({"me", "i", "myself"})
_COLLECTIVE_WORDS = frozenset({"everyone", "everybody", "all", "the rest", "rest", "others", "everyone else"})
# "Carol's family", "Carol’s family", "Carol family": the name before the family wording.
_FAMILY_SUFFIX = re.compile(r"^(?P<name>.+?)(?:'s|’s)?\s+family$", re.IGNORECASE)
# A fixed share or a percentage: "Carol's share is 50", "share of 20", "Dave owes 30", "40%".
# "Paid"/"pay" are left out on purpose: "I paid 50, split with Carol" is an ordinary split.
_FIXED_SHARE = re.compile(
    r"(?<!\w)(?:share|portion|part)\s+(?:is|was|of|=)\s*\$?\d"
    r"|(?<!\w)split\s+(?:is|was|=)\s*\$?\d"
    r"|\d\s*(?:%|percent(?!\w))"
    r"|(?<!\w)owes?\s+(?:me\s+|us\s+)?\$?\d", re.IGNORECASE)
# The money number in a fixed-share phrase: "Bob's split is 90" -> 90, "Carol owes 30" -> 30.
# The same phrases as _FIXED_SHARE, without percentages: a percentage is never an amount.
_SHARE_AMOUNT = re.compile(
    r"(?<!\w)(?:(?:share|portion|part)\s+(?:is|was|of|=)|split\s+(?:is|was|=)|owes?\s+(?:me\s+|us\s+)?)"
    r"\s*\$?(\d+(?:\.\d+)?)", re.IGNORECASE)
# Words that say the shares are not equal: "split unevenly", "not equally", "split by nights".
# The model used to set an `uneven` flag for these; whether the words are there is a question
# Python answers exactly, as it does for split words and "last week", so it is read here
# (#120). It only adds the "Set amounts" warning when no count was read; it never changes a
# share. "Per night" is left out on purpose: "hotel 200 per night split with Carol" is even.
# "Uneven" and "unequal" count only beside a split word, so "uneven roads, go halves" is even.
_SPLIT_VERB = r"(?:split|splits|splitting|share|shares|shared|sharing|divide|divided|dividing)"
_UNEVEN_ADVERB = r"(?:un-?even(?:ly)?|un-?equal(?:ly)?|not\s+(?:equally|evenly))"
_UNEVEN_WORD = re.compile(
    # "split unevenly", "shared with Carol, not equally": at most three words between.
    rf"(?<!\w){_SPLIT_VERB}(?:\s+\S+){{0,3}}?\s+{_UNEVEN_ADVERB}(?!\w)"
    # "uneven split", "unequal shares", "not split equally".
    rf"|(?<!\w){_UNEVEN_ADVERB}(?:\s+\S+)?\s+{_SPLIT_VERB}(?!\w)"
    r"|(?<!\w)not\s+split\s+(?:equally|evenly)(?!\w)"
    # "by nights", "by number of nights", "split by the night".
    r"|(?<!\w)by\s+(?:the\s+)?(?:number\s+of\s+)?(?:nights|days|counts?)(?!\w)"
    rf"|(?<!\w){_SPLIT_VERB}\s+by\s+(?:the\s+)?(?:night|day)(?!\w)", re.IGNORECASE)
# A ratio of shares: "60/40" or "70:30", two whole numbers that add up to 100, which keeps a
# date ("3/7") and a time ("10:30") out; or "2:1", a colon with one digit after it, which no
# time has. A slash with small numbers is always read as a date.
_RATIO = re.compile(r"(?<![\d.])(\d{1,2})\s*[/:]\s*(\d{1,2})(?![\d.])")
_SMALL_RATIO = re.compile(r"(?<![\d.:])(\d{1,2})\s*:\s*(\d)(?![\d.:])")
# What joins two names the model returned as one: "Reds and Blues", "Carol & Dave", "Carol, Dave".
_NAME_JOINER = re.compile(r"\s*(?:,|&|(?<!\w)and(?!\w))\s*", re.IGNORECASE)
_EXCEPT_WORD = re.compile(r"(?<!\w)(?:except|excluding|but\s+not|apart\s+from|other\s+than)(?!\w)", re.IGNORECASE)
_EVERYONE_WORD = re.compile(r"(?<!\w)(?:everyone|everybody|all|all\s+of\s+us|whole\s+group|the\s+group)(?!\w)", re.IGNORECASE)


# A message asks for a split only if it uses one of these words. The model sometimes invented
# a split without one: "dinner with Carol 70" came back as a split with Carol in 1 of 5 runs
# (2026-11-16). Whether a word is in the message is a question Python answers exactly, so it is
# checked here, as "last week" is for dates (ADR-0031).
_SPLIT_WORD = re.compile(
    r"(?<!\w)(?:split|splits|splitting|share|shares|shared|sharing|between|among|amongst|"
    r"divide|divided|halves|dutch)(?!\w)", re.IGNORECASE)
# "between"/"among" and everything after it: the list of sharers.
_BETWEEN = re.compile(r"(?<!\w)(?:between|among|amongst)(?!\w)(?P<rest>.*)", re.IGNORECASE | re.DOTALL)
# "split with", "split the bill with", "shared equally with": a split word, at most four words,
# then "with". Scoped like this so the "with" in "paid with Card A" doesn't count.
_SPLIT_WITH = re.compile(
    r"(?<!\w)(?:split|splits|splitting|share|shares|shared|sharing|divide|divided)(?:\s+\S+){0,4}?(?<!paid)(?<!pay)\s+with(?!\w)",
    re.IGNORECASE)
# "I" and "we" are left out on purpose: "I paid, split between Carol and Dave" must not put the
# sender in the split.
_SENDER_IN_LIST = re.compile(r"(?<!\w)(?:me|myself|us)(?!\w)", re.IGNORECASE)
# Said about the payer, not the sharers: "split between Carol and Bob, paid by me".
_PAID_BY_ME = re.compile(r"(?<!\w)(?:paid\s+(?:for\s+)?)?by\s+(?:me|myself)(?!\w)", re.IGNORECASE)

# --- "for <person>": a cost paid for someone, who owes it back (#154) ------------------------
# "Carol paid 50 for Dave's ticket" means Dave owes Carol 50. Read by Python only, and only
# when the message has no split word (a split word means the split rules above decide).
FOR_NO_MATCH_WARNING = "⚠️ Couldn't match '{name}'. No split recorded."
# Any of these and nobody owes: "gift for Dave", "for Carol's birthday", "Dave's treat".
_GIFT_WORD = re.compile(
    r"(?<!\w)(?:gift|gifts|gifted|present|presents|treat|treats|treated|birthday|birthdays|bday)(?!\w)",
    re.IGNORECASE)
# Where a list of people may start. "Paid for by Carol" names the payer, so "for by" is skipped.
_FOR_WORD = re.compile(r"(?<!\w)(?:for|on\s+behalf\s+of)\s+(?=\S)(?!by(?!\w))", re.IGNORECASE)
# "Carol paid 58 on my behalf" is "for me".
_ON_MY_BEHALF = re.compile(r"(?<!\w)on\s+my\s+behalf(?!\w)", re.IGNORECASE)
# A word (keeping its 's), or a mark that ends or joins a list.
_LIST_TOKEN = re.compile(r"[\w'’]+|[^\w\s]")
_WORD_TOKEN = re.compile(r"[\w'’]+")
_POSSESSIVE = re.compile(r"['’]s$", re.IGNORECASE)
# Words that say whose a thing is, as "Dave's" does: "Dave's ticket and her ticket".
_OWNER_PRONOUNS = frozenset({"my", "her", "his", "their", "our", "your"})
# After a later name in a list, one of these starts a phrase about it ("and Erin on the bus");
# any other word ("and Erin booked it") means the name begins a new clause.
_PREPOSITIONS = frozenset("on at in from to with by via during after before into onto over through".split())


def _owner_word(word: str) -> bool:
    """True for "Dave's" or "her": a word that says whose the next thing is."""
    return bool(_POSSESSIVE.search(word)) or word.casefold() in _OWNER_PRONOUNS


def _phrase_start(word: str) -> bool:
    """True when a word after a name still belongs with it: a preposition ("Erin on the
    bus"), an amount ("Erin 30") or a currency ("Erin sgd 30"). Anything else ("Erin booked
    it") starts a new clause."""
    return word in _PREPOSITIONS or word[0].isdigit() or word.upper() in ISO_4217
_LIST_JOINERS = frozenset({",", "&", "and"})
# "for Dave's mum" is for Dave's mum, not Dave, so Dave owes nothing ("my" works the same way).
_RELATIVE_WORDS = frozenset("""
    mum mom mummy mommy mother ma amma dad daddy father pa appa parent parents
    wife husband partner girlfriend boyfriend fiance fiancee son daughter kid kids child children baby
    brother sister bro sis grandma grandpa grandmother grandfather granny aunt aunty auntie uncle
    cousin niece nephew friend friends boss colleague colleagues inlaws
""".split())
# The longest name tried at one place in the list: "Ann Lee", or "the Reds family".
_MAX_NAME_WORDS = 3


def asks_for_split(message: str) -> bool:
    """True when the message contains a split word ("split", "shared", "between"...)."""
    return _SPLIT_WORD.search(message) is not None


def says_uneven(message: str) -> bool:
    """True when the message says the shares aren't equal ("unevenly", "by nights", "60/40",
    "2:1").

    50/50 and 1:1 are even splits, so a ratio counts only when its two numbers differ.
    """
    if _UNEVEN_WORD.search(message):
        return True
    if any(int(a) + int(b) == 100 and int(a) != int(b) for a, b in _RATIO.findall(message)):
        return True
    return any(int(a) != int(b) for a, b in _SMALL_RATIO.findall(message))


def sender_shares(message: str) -> bool | None:
    """Whether the sender is one of the sharers, read from the words, or None when the words
    don't settle it and the model's include_sender stands.

    "Between" lists the sharers in full, so the sender shares only if they are in the list:
    "split between Carol and Dave" is False, "split between me and Carol" is True. Otherwise
    "with" adds people to the sender: "split with Carol" is True. The model got the first of
    these wrong in 2 of 5 runs (2026-11-16), and it is one literal word, so Python reads it.
    """
    between = _BETWEEN.search(message)
    if between:
        # Only the words after "between" are the list. "Paid by me" there, or a "me" earlier in
        # the message ("paid by me, split between Carol and Bob"), is about the payer.
        return _SENDER_IN_LIST.search(_PAID_BY_ME.sub(" ", between.group("rest"))) is not None
    if _SPLIT_WITH.search(message):
        return True
    return None


def share_out(amount_cents: int, weights: dict[int, int]) -> dict[int, int]:
    """Hands out `amount_cents` between people in whole cents that add up exactly.

    `weights` maps participant id -> a positive whole number. An even split is every weight 1;
    a split by nights is the number of nights each (ADR-0042, through split_shares).

    Rounding each share separately loses or invents cents: 5.55 two ways is 2.775 each, which
    rounds to 2.78 + 2.78 = 5.56. So instead:
      1. Everyone gets their share rounded DOWN: amount * weight // total weight.
      2. That leaves a few spare cents (always fewer than the number of people). They go one
         each to the people who lost the most to the rounding - the largest remainder,
         amount * weight % total weight.
      3. A tie goes to the lowest participant id.
    The rule has no randomness and ignores who paid (the payer may not be in the split at all),
    so the same entry always splits the same way.

    Whole-number maths only: no float, so no binary rounding error (ADR-0004).
    The result is ordered by participant id. Raises ValueError for nothing to share or nobody
    to share it between.
    """
    if amount_cents < 1 or not weights or any(w < 1 for w in weights.values()):
        raise ValueError("A split needs a positive amount and positive weights")
    total = sum(weights.values())
    shares = {pid: amount_cents * weights[pid] // total for pid in sorted(weights)}
    spare = amount_cents - sum(shares.values())
    # Largest remainder first (hence the minus), then lowest id.
    order = sorted(weights, key=lambda pid: (-(amount_cents * weights[pid] % total), pid))
    for pid in order[:spare]:
        shares[pid] += 1
    return shares


@dataclass(frozen=True)
class SplitResult:
    """What resolve_split worked out: who shares (ordered by id; empty means "not split") and
    any ⚠️ lines for the card."""

    people: tuple[Participant, ...]
    warnings: tuple[str, ...]
    counts: dict[int, int] = field(default_factory=dict)
    default_count: int | None = None
    unit: str | None = None


def _label_key(text: str) -> str:
    """A family label or a name as said, in the form they are compared in: lower case, single
    spaces, and no family wording, so "the Lee family", "Lee's family" and "lee" are all "lee".
    """
    text = " ".join(text.casefold().split()).removeprefix("the ")
    text = re.sub(r"\s+famil(?:y|ies)$", "", text)
    # "Lee's" loses its 's; a label that itself ends in s ("Reds'") loses just the apostrophe.
    return re.sub(r"['’]s$", "", text).strip("'’ ")


def _valid_count(count, message: str | None) -> bool:
    """Check literal counts, never infer them from amounts or other numbers."""
    if type(count) is not int or not 1 <= count <= 999:
        return False
    if message is None:
        return True
    return count in _numbers(message)


# Number words a Whisper transcript may use for a count: one .. thirty.
_NUMBER_WORDS = ('one two three four five six seven eight nine ten eleven twelve thirteen '
                 'fourteen fifteen sixteen seventeen eighteen nineteen twenty').split()
_NUMBER_WORDS += [f'twenty {word}' for word in _NUMBER_WORDS[:9]] + ['thirty']
# A whole number standing alone: never the 3 inside 2300, 3.50 or 3,000. Number words longest
# first, so "twenty one" is read as 21, not 20. "twenty-one" is accepted too.
_NUMBER_TOKEN = re.compile(
    r"(?<![\w.])(?<!\d,)(?:(?P<digits>\d+)|(?P<word>"
    + "|".join(w.replace(' ', '[ -]') for w in sorted(_NUMBER_WORDS, key=len, reverse=True))
    + r"))(?!\w|[.,]\d)", re.IGNORECASE)


def _numbers(text: str) -> list[int]:
    """Every whole number in the text, in the order they appear (digits and number words)."""
    values = []
    for found in _NUMBER_TOKEN.finditer(text):
        if found.group('digits'):
            values.append(int(found.group('digits')))
        else:
            word = re.sub(r'[ -]+', ' ', found.group('word').casefold())
            values.append(_NUMBER_WORDS.index(word) + 1)
    return values


def _mentions(name: str, message: str) -> list[re.Match]:
    """Every place `name` is said in the message, as a whole word, ignoring case."""
    return list(re.finditer(rf"(?<!\w){re.escape(name.strip())}(?!\w)", message, re.IGNORECASE))


def _is_paying(found: re.Match, message: str) -> bool:
    """True when this mention of a name says who paid, not who shares: "Carol paid",
    "Carol pays", "paid by Carol", "paid for by Carol" - the same wordings read_payer reads."""
    return bool(re.match(r"\s+(?:paid|pays)(?!\w)", message[found.end():], re.IGNORECASE)
                or re.search(r"(?<!\w)paid\s+(?:for\s+)?by\s+$", message[:found.start()], re.IGNORECASE))


def _only_paying(name: str, message: str, count: int) -> bool:
    """True when the message says this name, but only as the payer ("Carol paid 300 for 6
    nights"), so nothing in it gives the name a count of its own.

    False when the payer's clause says the count is theirs: "Carol paid for her 4 nights".
    """
    found = _mentions(name, message)
    if not found or not all(_is_paying(f, message) for f in found):
        return False
    return count not in _owned_counts(message)


def _owned_counts(message: str, start: int = 0) -> list[int]:
    """Every number said to be someone's own, from `start` on: "her 4", "his own 3".

    Only a number straight after the word: in "for their airbnb for 6 nights" the 6 is still
    the bill's length.
    """
    counts = []
    for owner in re.finditer(r"(?<!\w)(?:her|his|their)\s+(?:own\s+)?", message[start:], re.IGNORECASE):
        token = _NUMBER_TOKEN.match(message, start + owner.end())
        if token:
            counts += _numbers(token.group(0))
    return counts


def _count_after(name: str, message: str) -> int | None:
    """The first count (1-999, digits or a number word up to thirty) after `name` in the message.

    Used only to fill a count the model left out for someone it did list as a sharer. For
    "Carol and Erin 4" the model gave 4 to Carol and nothing to Erin in 9 of 10 runs
    (2026-11-16); in the message, the 4 is the first number after "Erin". The count still has
    to be in the text, so this never invents one, and the card shows every count before Confirm.

    A mention that says who paid is skipped: in "Carol paid 300 for 6 nights" the 6 is how long
    the bill runs, not Carol's count. "Carol paid for her 4 nights" still gives her 4.
    """
    mentions = _mentions(name, message)
    found = next((f for f in mentions if not _is_paying(f, message)), None)
    if not found:
        # Only paying mentions: a count after one is hers only if said so ("her 4").
        owned = [n for f in mentions[:1] for n in _owned_counts(message, f.end()) if 1 <= n <= 999]
        return owned[0] if owned else None
    return next((n for n in _numbers(message[found.end():]) if 1 <= n <= 999), None)


# "The rest", "others", "everyone else": the people the message doesn't name. In "Reds 5 nights.
# Rest all stay 6 nights." the model left the 6 out in 6 of 10 runs (2026-11-17).
# "The rest of the airbnb" is part of the bill, not people; "the rest of us" is people.
_REST_WORD = re.compile(
    r"(?<!\w)(?:the\s+)?(?:rest(?!\s+of\s+(?!(?:us|them)(?!\w)))|others|everyone\s+else|everybody\s+else)(?!\w)",
    re.IGNORECASE)
# Where a clause ends: . ! ? ; , or a new line - but not the point in 2717.84 or the comma
# in 2,717.84.
_CLAUSE_END = re.compile(r"[!?;\n]|\.(?!\d)|,(?!\d)|(?<!\d),")


def _rest_count(message: str) -> int | None:
    """The count the message gives "the rest", read from its own clause, or None.

    Narrow on purpose: when this returns None, the model's reading stands. It reads:
      - the one number in the rest word's clause: "rest all stay 6 nights", "Reds 5, rest 6",
        "6 nights for the rest". Never one from another clause, so "6 nights for the rest,
        Reds 5" can't give the rest 5.
    It returns None for:
      - no rest word. "The rest of the airbnb" is not one; "the rest of us" is.
      - a clause with no number, or two ("6 nights for the rest and Carol 4"): either could
        be the rest's.
      - "2 others": a bare number touching "others" counts people, not nights each.
    The caller also reads it only beside a named person's count (see _reread).
    """
    found = _REST_WORD.search(message)
    if not found:
        return None
    word_before = re.search(r"(\S+)\s+$", message[:found.start()])
    if (found.group(0).casefold().endswith("others") and word_before
            and _NUMBER_TOKEN.fullmatch(word_before.group(1))):
        return None
    ends = [m.start() for m in _CLAUSE_END.finditer(message)]
    start = max((e + 1 for e in ends if e < found.start()), default=0)
    stop = min((e for e in ends if e >= found.end()), default=len(message))
    numbers = [n for n in _numbers(message[start:stop]) if 1 <= n <= 999]
    return numbers[0] if len(numbers) == 1 else None


def _clause(message: str, found: re.Match) -> str:
    """The clause a match sits in: the text between the clause ends either side of it (see
    _CLAUSE_END), so in "Carol paid 600 for the villa, stayed 4 nights" Carol's clause is
    "Carol paid 600 for the villa"."""
    ends = [m.start() for m in _CLAUSE_END.finditer(message)]
    start = max((e + 1 for e in ends if e < found.start()), default=0)
    stop = min((e for e in ends if e >= found.end()), default=len(message))
    return message[start:stop]


def _names_a_count(reference: dict) -> bool:
    """True when the model gave a count to someone it named, not just to "the rest"."""
    def name(e):
        return (e if isinstance(e, str) else e.get("name") or "").strip().casefold()
    return any(isinstance(e, dict) and e.get("count") is not None and name(e) not in _COLLECTIVE_WORDS
               for e in (*(reference.get("people") or ()), *(reference.get("except_people") or ())))


def _reread(reference: dict, message: str, trip_on: bool) -> tuple[dict, list[tuple[str, int]]]:
    """The model's split_reference with two misreadings fixed from the words (2026-11-17).

    Measured on 10 runs of "Carol paid 2717.84 for 6 nights. Reds only staying 5 nights. Rest
    all stay 6 nights.", where Carol is one of the Reds:
      1. The payer got the bill's length as a count ("Carol, 6") in 7 runs. A name said only as
         the payer loses the count the model gave it; the payer still shares if the model
         listed them, at a count from the other words (their family label, or the rest).
      2. The rest's 6 was left out in 6 runs. A count read from the rest word's clause becomes
         the default, over the model's. With a trip on and no "between"/"split with" list of
         sharers, "the rest" means the whole trip.
    Returns a copy (the model's dict is never changed) and the names whose count was dropped
    although it wasn't in their paying clause, which get a CHECK_COUNT_WARNING line: in "Carol
    paid 600 for the villa, stayed 4 nights" the 4 may well be Carol's.
    """
    reference = dict(reference)
    # Checked before the payer's count is stripped.
    named_counted = _names_a_count(reference)
    unsure = []

    def fixed(entry):
        name = entry if isinstance(entry, str) else entry.get("name") or ""
        if (isinstance(entry, dict) and entry.get("count") is not None
                and _only_paying(name, message, entry["count"])):
            # "Carol paid ... for 6 nights": the 6 sits in the paying clause, so it is the
            # bill's length and dropping it is safe. A count from anywhere else is a guess
            # either way, so it is still dropped, but the card asks for a check.
            if not any(entry["count"] in _numbers(_clause(message, f))
                       for f in _mentions(name, message)):
                unsure.append((name, entry["count"]))
            return {**entry, "count": None}
        return entry

    reference["people"] = [fixed(e) for e in reference.get("people") or ()]
    # A rest count only means something beside someone else's count, and alone it never makes
    # a split: "Dinner 80 split with the others" must not give "the others" 80.
    rest = _rest_count(message) if named_counted else None
    if rest is not None:
        reference["default_count"] = rest
        # A "rest" entry from the model carries its own count, which would replace this one in
        # resolve_split. Python's reading wins, so the entry goes.
        reference["people"] = [e for e in reference["people"] if (
            e if isinstance(e, str) else e.get("name") or "").strip().casefold() not in _COLLECTIVE_WORDS]
        # "Split between Carol, Dave and Erin ... the rest 6" lists the sharers in full, so
        # "the rest" there is the rest of that list, not the trip.
        if trip_on and reference.get("kind") in ("named", "everyone") and sender_shares(message) is None:
            reference["kind"] = "everyone"
    return reference, unsure


def _add_unlisted(reference: dict, message: str, candidates, families: dict[int, str]) -> dict:
    """The model's split_reference plus anyone the message gives a count the model left out.

    For "Airbnb 900 split by nights. Reds 5 nights, rest all 6 nights." (a /help example) the
    model returned no people at all, 94% sure (logprobs run 2026-11-18, #120), so the Reds'
    5 was lost and the trip split evenly. Python knows every name and family label, so it
    reads them from the words, as _rest_count reads the rest. A name is added only when all
    of these hold, so nothing is guessed:
      - it is a split by count: the model read a count or a default, or the words say
        "by nights" (says_uneven). An even split never gains a name this way.
      - the model listed nobody by that name or label, in people or except_people.
      - a mention of it that doesn't say who paid has exactly one number after it in its
        own clause ("Reds 5 nights", "Carol stayed 4 nights"), from 1 to 999.
    Returns a copy; the model's dict is never changed.
    """
    entries = [*(reference.get("people") or ()), *(reference.get("except_people") or ())]
    by_count = reference.get("default_count") is not None or says_uneven(message) or any(
        isinstance(e, dict) and e.get("count") is not None for e in entries)
    if not by_count:
        return reference
    listed = {_label_key(e if isinstance(e, str) else e.get("name") or "") for e in entries}
    # Labels first, so a label that is also someone's name stays one entry (match_person in
    # resolve_split still tries the person first, as it does for the model's entries).
    said = {label: True for label in families.values() if label}
    said.update((p.display_name, False) for p in candidates if p.display_name not in said)
    added = []
    for name, is_family in said.items():
        if _label_key(name) in listed:
            continue
        for found in _mentions(name, message):
            if _is_paying(found, message):
                continue
            clause = _clause(message, found)
            after = clause[clause.casefold().find(found.group(0).casefold()) + len(found.group(0)):]
            numbers = _numbers(after)
            if len(numbers) == 1 and 1 <= numbers[0] <= 999:
                # "Bob's family 5" is Bob's whole family, as the model is told to read it.
                family = is_family or bool(re.match(r"(?:['’]s)?\s+famil(?:y|ies)(?!\w)", after, re.IGNORECASE))
                added.append({"name": name, "is_family": family, "count": numbers[0]})
                listed.add(_label_key(name))
                break
    if not added:
        return reference
    return {**reference, "people": [*(reference.get("people") or ()), *added]}


def _number_after_except(message: str) -> bool:
    """True when a whole number (digits or a number word) comes after the first except word."""
    found = _EXCEPT_WORD.search(message)
    if not found:
        return False
    return bool(_numbers(message[found.end():]))


def _cents_or_none(value) -> int | None:
    """dollars_to_cents for a value that may be missing or bad: None instead of raising."""
    try:
        return dollars_to_cents(value)
    except (TypeError, ValueError):
        return None


def drop_share_entries(transactions: list[dict], message: str) -> list[dict]:
    """Remove extra entries the model made out of a share of the first one.

    "I paid 135 split with Bob. Bob's split is 90" came back as two expenses (135 and 90) in
    10 of 24 runs (2026-11-18), and the one-expense guard refused it. Prompt wording fixed that
    but cost the nights readings, so Python undoes it instead, reading the message as it does
    for dates and the payer. A later entry goes only when all three hold:
      - the message has a fixed-share phrase with a money number ("split is 90", "share is 50");
      - the entry's amount is that number, in the same currency as the first entry;
      - it is less than the first entry's amount.
    Real separate expenses ("Dinner 80 split with Carol. Taxi 20.") have no share phrase, so
    they are never merged - which keeps this right once several expenses per message are allowed.
    Returns a new list; the input is not changed.
    """
    if len(transactions) < 2:
        return list(transactions)
    shares = {_cents_or_none(m.group(1)) for m in _SHARE_AMOUNT.finditer(message or "")} - {None}
    first = transactions[0]
    first_cents = _cents_or_none(first.get("amount"))
    if not shares or first_cents is None:
        return list(transactions)

    def is_share(entry):
        cents = _cents_or_none(entry.get("amount"))
        return (cents in shares and cents < first_cents
                and entry.get("currency") == first.get("currency"))

    return [first, *(t for t in transactions[1:] if not is_share(t))]


def resolve_split(reference: dict | None, candidates, sender: Participant,
                  families: dict[int, str], trip_on: bool, message: str | None = None) -> SplitResult:
    """Who shares the entry: the model's split_reference (see _read_reference), or, when that
    finds nothing and the message has no split word, the people it was paid "for" (#154).

    The "for" reading comes second so that every split the rules below already read stays
    exactly as it was; it only fills in entries that would otherwise be personal.
    """
    result = _read_reference(reference, candidates, sender, families, trip_on, message)
    if message is None or result.people or result.warnings or asks_for_split(message):
        return result
    return read_for_people(message, candidates, sender, families, trip_on)


def read_for_people(message: str, candidates, sender: Participant,
                    families: dict[int, str], trip_on: bool) -> SplitResult:
    """The people a cost was paid for, who each owe an even share of it (#154).

    "Carol paid 50 for Dave's ticket" -> Dave. "for Carol and Dave" -> both. "for me", "for my
    haircut" and "on my behalf" -> the sender. The payer is left to the card: a "for" list
    naming only the payer is no split there (_owed_split in bot_core).

    Nothing is guessed. A gift word means nobody owes. A "for" with no known person after it
    ("for dinner", "for Dave's mum") is not about a person, so it is silent. A list that names a
    person and also something unknown ("for her and Dave") records nothing and says why.
    """
    if _GIFT_WORD.search(message):
        return SplitResult((), ())
    lists = [_for_list(message[found.end():], candidates, sender, families, trip_on)
             for found in _FOR_WORD.finditer(message)]
    if _ON_MY_BEHALF.search(message):
        lists.append(([sender], []))
    people = {}
    for matched, unmatched in lists:
        if not matched:
            continue  # "for dinner": no person in it
        if unmatched:
            return SplitResult((), (FOR_NO_MATCH_WARNING.format(name=unmatched[0]),))
        people.update((p.id, p) for p in matched)
    return SplitResult(tuple(people[pid] for pid in sorted(people)), ())


def _for_list(text: str, candidates, sender: Participant, families: dict[int, str],
              trip_on: bool) -> tuple[list[Participant], list[str]]:
    """Reads the list right after one "for": the people it names, and the items that matched
    nobody. `text` is the message after "for".

    Items are separated by ",", "&" or "and", and a repeated "for" ("for Dave and for Erin")
    is skipped. Each item is matched by its longest opening run of words that is a person, a
    family or a trip label ("Ann Lee", "Carol's family", "the Reds"); words after that are the
    thing bought ("Dave's ticket"). An item that matches nobody goes on the unmatched list, and
    the list carries on only if a joiner follows it. Two kinds of unknown item just end the
    list instead: one after a comma ("for Carol, 80" is a new clause), and a plain thing after
    a thing ("for Dave's flight and hotel": the hotel is Dave's too). An unknown owner there
    ("Dave's ticket and Zed's ticket", "and her ticket") is still unmatched. A later name
    followed by anything but an owner's "'s" or a preposition starts a new clause, so the list
    ends before it ("for Dave's ticket and Erin booked it").
    """
    tokens = _LIST_TOKEN.findall(text)
    matched, unmatched = [], []
    i, joiner, after_thing = 0, None, False
    while i < len(tokens):
        # One item: the words up to the next joiner or mark ("$", ".", "(" ...).
        end = i
        while end < len(tokens) and _WORD_TOKEN.fullmatch(tokens[end]) and tokens[end].casefold() != "and":
            end += 1
        words = tokens[i:end]
        # "for Dave and for Erin": a second "for" says a person comes next, so an unknown
        # one there is unmatched, never taken for a thing bought.
        repeated_for = joiner is not None and bool(words) and words[0].casefold() == "for"
        if repeated_for:
            words = words[1:]
        if not words:
            break
        found = None
        for n in range(min(len(words), _MAX_NAME_WORDS), 0, -1):
            people = _people_named(" ".join(words[:n]), candidates, sender, families, trip_on)
            if people:
                found = (n, people)
                break
        after = words[found[0]].casefold() if found and found[0] < len(words) else None
        if (found and joiner is not None and after is not None
                and not _owner_word(words[found[0] - 1]) and not _phrase_start(after)):
            break  # "for Dave's ticket and Erin booked it": Erin starts a new clause
        if found and (after is None or _POSSESSIVE.sub("", after) not in _RELATIVE_WORDS):
            matched.extend(found[1])
            after_thing = after is not None
        elif not repeated_for and (joiner == "," or (after_thing and not _owner_word(words[0]))):
            break
        else:
            # Nobody, or a relative of someone ("Dave's mum"): not a known person.
            unmatched.append(" ".join(words))
            if found:
                break
        if end >= len(tokens) or tokens[end].casefold() not in _LIST_JOINERS:
            break
        # "Dave, Erin, and Frank": the "and" after an Oxford comma joins too, and is the
        # joiner that counts for the next item.
        i = end
        while i < len(tokens) and tokens[i].casefold() in _LIST_JOINERS:
            joiner = tokens[i].casefold()
            i += 1
    return matched, unmatched


def _people_named(said: str, candidates, sender: Participant, families: dict[int, str],
                  trip_on: bool) -> tuple[Participant, ...]:
    """Who one item of a "for" list stands for, by the same matching a split name gets in
    _read_reference: a person, their family, or a trip label. "My" is the sender, as "me" is."""
    if said.casefold() == "my":
        said = "me"
    result = _read_reference({"kind": "named", "include_sender": False,
                              "people": [{"name": said, "is_family": False}]},
                             candidates, sender, families, trip_on)
    return result.people if not result.warnings else ()


def _read_reference(reference: dict | None, candidates, sender: Participant,
                    families: dict[int, str], trip_on: bool, message: str | None = None) -> SplitResult:
    """Turns the model's split_reference into the participants who share the entry.

    `candidates` are the people who may be named: the sender's trip members while a trip is on,
    otherwise every participant. `families` maps participant id -> family label on that trip
    (empty with no trip on, so then a "family" is just the person named). `message` is the raw
    text: with it, two things the model gets wrong are read from the words instead (see
    asks_for_split and sender_shares). Counts must also appear literally in the message;
    None skips the text checks, but not the 1..999 range check.

    Nobody is ever guessed. A name that matches no one, or more than one person, becomes a
    "Couldn't match" warning and the others keep their shares. A reference with keys missing
    (an older shape) reads as "not split" rather than raising.
    """
    if not reference or reference.get("kind", "none") == "none":
        return SplitResult((), ())
    candidates = tuple(candidates)
    if message is not None:
        # Before anything reads the entries: an added count is what lets "the rest" below
        # read its own count (it is read only beside someone else's).
        reference = _add_unlisted(reference, message, candidates, families)
    # "Reds 5, others stay 6" says "everyone" as surely as "all" does - but only beside a named
    # person's count, so "Dinner with the others 80" stays a personal entry.
    says_everyone = message is not None and bool(
        _EVERYONE_WORD.search(message) or (_REST_WORD.search(message) and _names_a_count(reference)))
    unsure = []
    if message is not None:
        reference, unsure = _reread(reference, message, trip_on)
    unit = reference.get('count_unit') or "count"
    entries = reference.get("people") or ()
    exceptions = reference.get("except_people") or ()
    default_count = reference.get("default_count")
    by_count = default_count is not None or any(
        isinstance(e, dict) and e.get("count") is not None for e in (*entries, *exceptions))
    if message is not None and not asks_for_split(message) and not (by_count and says_everyone):
        # The model read a split the message never asked for. A personal entry, silently: the
        # message decides.
        return SplitResult((), ())
    # Uneven shares this reader can't work out: a fixed share or a percentage, or "by nights"
    # with no counts. The names are still right, so they are ticked with an even split and a
    # warning pointing to "Set amounts" (ADR-0048): one tap to fix, and the warning stays until
    # amounts are set. The warning never blocks a save, so an unfixed card saves even shares.
    # The model turns "Carol's share is 50" into a count of 50 for Carol alone (5 of 10 runs,
    # 2026-11-16), so for a fixed share every count is dropped, never trusted. Both are read
    # from the words (says_uneven replaced the model's `uneven` flag, #120), so with no
    # message there is no uneven reading, like every other text check here.
    uneven_unread = message is not None and bool(
        (not by_count and says_uneven(message)) or _FIXED_SHARE.search(message))
    if uneven_unread:
        entries = tuple({**e, 'count': None} if isinstance(e, dict) else e for e in entries)
        exceptions = tuple({**e, 'count': None} if isinstance(e, dict) else e for e in exceptions)
        default_count, by_count, unsure = None, False, []
    # The shorthand without a split word is allowed only with trustworthy counts.
    # Unused defaults are deliberately checked later, after resolving the sharers.
    if message is not None and not asks_for_split(message) and any(
            isinstance(e, dict) and e.get('count') is not None
            and not _valid_count(e['count'], message) for e in (*entries, *exceptions)):
        return SplitResult((), (COUNT_UNREAD_WARNING,))
    warnings = []

    def named(entry) -> list[Participant]:
        """The people one entry stands for: one person, or their whole family. Empty (plus a
        warning) when the name can't be matched. An entry is {"name", "is_family"}; a bare
        string is accepted too and means one person."""
        name = entry if isinstance(entry, str) else entry.get("name")
        if not name:
            return []
        is_family = not isinstance(entry, str) and entry.get("is_family")
        # The model is told to drop "'s family", but sometimes returns "Carol's family" as the
        # name (2 of 5 runs, 2026-11-16), which matches nobody. The wording itself says it is
        # a family, so strip it and treat the entry as one.
        family_words = _FAMILY_SUFFIX.match(name.strip())
        if family_words:
            name, is_family = family_words.group("name"), True
        matched = match_one(name, is_family)
        if matched is None:
            # "Reds and Blues" as one name: the model splits it or not about half the time
            # (53% vs 47% at that point, logprobs run 2026-11-18, #120), and as one name it
            # matches nobody, so the whole split was lost. When every part matches on its own,
            # it is those parts; one unmatched part keeps the warning for the whole name. Each
            # part is an exact match, so nothing is guessed.
            parts = [part for part in _NAME_JOINER.split(name) if part.strip()]
            found = [match_one(part, is_family) for part in parts] if len(parts) > 1 else [None]
            if any(f is None for f in found):
                warnings.append(NO_MATCH_WARNING.format(name=name))
                return []
            matched = [p for group in found for p in group]
        return matched

    def match_one(name: str, is_family: bool) -> list[Participant] | None:
        """One name: a person (or their family), else one trip family label. None if neither."""
        person = sender if name.strip().casefold() in _SENDER_WORDS else match_person(name, candidates)
        if person is None:
            # Not a person's name, so try the trip's family labels: "Reds 5 nights" names a
            # family by its label. A person is always tried first, so this only ever turns a
            # "Couldn't match" into a match, never changes one.
            said = _label_key(name)
            labels = {label for label in families.values() if label and _label_key(label) == said}
            if len(labels) == 1:
                return [p for p in candidates if families.get(p.id) in labels]
            return None
        label = families.get(person.id)
        if is_family and label:
            return [p for p in candidates if families.get(p.id) == label]
        return [person]

    # Keyed by id, so naming someone twice (or the sender by name and by "with") is one share.
    people = {}
    explicit_counts = {}
    kind = reference.get("kind")
    if kind == "everyone" and entries and message is not None and not says_everyone:
        kind = "named"
    if kind == "everyone":
        if not trip_on:
            # Outside a trip "everyone" has no fixed meaning, and the whole directory would be
            # a guess.
            return SplitResult((), (EVERYONE_NEEDS_TRIP_WARNING,))
        people = {p.id: p for p in candidates}
    elif kind == "named":
        # "Split with Carol" includes the sender; "split between Carol and Dave" doesn't.
        include_sender = sender_shares(message) if message is not None else None
        if include_sender is None and by_count and message is not None:
            # In a split by count the model said the sender shares even when the message
            # didn't name them ("Bob's family 5, Carol and Dave 4": 10 of 10 runs, 2026-11-16).
            # The sender then has no count and the split fails, so read it from the words:
            # "me" or "myself" in the list (not "paid by me"), or the sender inside a family
            # or name the message lists (added below with the other entries).
            include_sender = _SENDER_IN_LIST.search(_PAID_BY_ME.sub(" ", message)) is not None
        if include_sender is None:
            include_sender = reference.get("include_sender")
        if include_sender:
            people[sender.id] = sender
    else:
        return SplitResult((), ())
    # Collective pseudo-names carry the default, never a directory lookup.
    for entry in entries:
        count = entry.get('count') if isinstance(entry, dict) else None
        name = entry if isinstance(entry, str) else entry.get('name', '')
        if name.strip().casefold() in _COLLECTIVE_WORDS:
            if _valid_count(count, message):
                default_count = count
            elif count is not None:
                return SplitResult((), (*warnings, COUNT_UNREAD_WARNING))
            continue
        if count is not None and not _valid_count(count, message):
            return SplitResult((), (*warnings, COUNT_UNREAD_WARNING))
        if count is None and by_count and message is not None and name:
            # The model listed this sharer but dropped their count; take the first number
            # after their name in the message (see _count_after).
            said = _FAMILY_SUFFIX.sub(r"\g<name>", name.strip()).removesuffix("'s")
            count = _count_after(said, message)
            if count is not None:
                entry = {**entry, 'count': count} if isinstance(entry, dict) else {'name': entry, 'count': count}
                # "Carol and Erin 4" gives Erin the 4 in her own clause, which is safe. In
                # "split between Carol, Dave and Erin. Carol 2, the rest 3" the first number
                # after Dave is Carol's 2, in another clause, so the card asks for a check.
                own = next((f for f in _mentions(said, message) if not _is_paying(f, message)), None)
                line = CHECK_COUNT_WARNING.format(name=name, unit=unit)
                if own is not None and count not in _numbers(_clause(message, own)) and line not in warnings:
                    warnings.append(line)
        matched = named(entry)
        people.update((p.id, p) for p in matched)
        if isinstance(entry, dict) and entry.get("count") is not None:
            explicit_counts.update((p.id, entry['count']) for p in matched)
    excluded = set()
    counted_exception = False
    for entry in exceptions:
        count = entry.get('count') if isinstance(entry, dict) else None
        matched = named(entry)
        if _valid_count(count, message):
            counted_exception = True
            people.update((p.id, p) for p in matched)
            explicit_counts.update((p.id, count) for p in matched)
        elif by_count and message is not None and _number_after_except(message):
            # "5 nights, everyone except Carol's family who only stayed 4" once came back with
            # Carol under except and no count (1 of 10 runs, 2026-11-16), which would drop her
            # family from a bill they share. In a split by count, a number after "except"
            # probably belongs to someone left out, so refuse rather than guess. A refusal only
            # costs a warning; a wrong exclusion costs someone's share.
            return SplitResult((), (*warnings, COUNT_UNREAD_WARNING))
        else:
            excluded.update(p.id for p in matched if p.id in people)
    for pid in excluded:
        people.pop(pid, None)
    # A dropped plain exclusion must never silently charge the entire group. An "except" that
    # came back as a counted sharer is fine: for "everyone except Carol's family who only stayed
    # 4" the model puts Carol's family at 4 in either list (3 of 5 runs in people, 2026-11-16).
    # What this still can't catch is a message with both a dropped exclusion and someone else's
    # count, such as "5 nights, Carol 4, everyone except Dave"; the card shows every sharer.
    if (message is not None and _EXCEPT_WORD.search(message)
            and not excluded and not counted_exception and not explicit_counts):
        return SplitResult((), (*warnings, EXCEPT_UNREAD_WARNING))
    if not people and not warnings:
        warnings.append(NOBODY_SHARES_WARNING)
    counts = {}
    if (by_count and message is not None and sender.id in people
            and sender.id not in explicit_counts and default_count is None):
        # "me 3 and Carol 2" with no "me" entry from the model: the sender's count is the
        # first number after "me", the same reading _count_after gives everyone else.
        mine = _count_after("me", message) or _count_after("myself", message)
        if mine is not None:
            explicit_counts[sender.id] = mine
    if by_count:
        counts = {pid: explicit_counts.get(pid, default_count) for pid in people}
        # An unused model default is discarded; menu additions use the modal read count.
        if all(pid in explicit_counts for pid in people):
            default_count = None
        if any(not _valid_count(c, message) for c in counts.values()):
            return SplitResult((), (*warnings, COUNT_UNREAD_WARNING))
        if len(counts) == 1:
            # A count matters only relative to someone else's. One sharer with a count means
            # a number was misread as a count, so refuse rather than charge them the lot.
            return SplitResult((), (*warnings, COUNT_UNREAD_WARNING))
        if len(set(counts.values())) == 1:
            # Everyone has the same count, so the shares are even. "dinner for 4, split between
            # Carol and Dave" came back as 4 each (7 of 10 runs, 2026-11-16): the same cents,
            # but the card would say "split by count". An even split is what was meant.
            by_count, counts = False, {}
    # A payer whose dropped count is the one they end up with anyway ("Carol paid ...", her
    # family 5, the model gave her 5) lost nothing, so only a count that changed is flagged.
    checks = [CHECK_COUNT_WARNING.format(name=name, unit=unit) for name, count in dict.fromkeys(unsure)
              if (person := match_person(name, candidates)) is None or counts.get(person.id) != count]
    warnings = [*checks, *warnings]
    if uneven_unread:
        # Ticked people: "shown as an even split". Nobody matched: "no split recorded".
        # The generic "couldn't tell who shares" line would only repeat the uneven one.
        warnings = [UNEVEN_EVEN_SPLIT_WARNING if people else UNEVEN_NOT_BUILT_WARNING,
                    *(w for w in warnings if w != NOBODY_SHARES_WARNING)]
    return SplitResult(tuple(people[pid] for pid in sorted(people)), tuple(warnings),
                       counts, default_count if by_count else None,
                       reference.get('count_unit') if by_count else None)


def split_shares(amount_cents: int, people, counts: dict[int, int] | None = None) -> tuple[dict, ...]:
    """An even or count-weighted split, as the rows kept on the pending entry:
    {"participant_id", "name", "share_cents"}, ordered by participant id, plus count for counted
    rows only. The card draws these rows and Confirm saves their cents, so the money shown is
    the money stored. Missing counts raise rather than silently becoming even shares."""
    people = {p.id: p for p in people}
    shares = share_out(amount_cents, {pid: counts[pid] if counts else 1 for pid in people})
    return tuple({"participant_id": pid, "name": people[pid].display_name, "share_cents": cents,
                  **({'count': counts[pid]} if counts else {})}
                 for pid, cents in shares.items())


# --- The split menu (PR 2, ADR-0041) --------------------------------------------------------
#
# The card's split menu changes the pending entry one tap at a time. Each function below takes
# the pending entry and returns a changed copy (it never changes its argument), so the handler in
# bot_core can redraw the card from the copy first and keep the change only once that worked.
#
# process_expense_text stores `split_menu` on the pending entry when the entry can be split:
#   amount_cents  the amount as whole cents, so a tap never converts it again
#   groups        the trip's members as (family label or None, members), in card order
#   families      participant id -> family label on the trip (empty with no trip on)
#   everyone      every participant, household first, then A-Z (menu_people)
#   opened        set once the menu has been opened or the split removed; stops the
#                 "tick everyone" on first open from happening a second time
#   counts        participant id -> count, retained even while unticked
#   default_count, unit  the default and unit read from the message
#   added_counts  participant id -> warning for guessed counts; only ticked warnings display
#   amounts       set participant id -> whole cents; unset people share the rest by count
#   cleared_amounts  last amounts cleared by a tick change; hints for the next prompt
# "Ticked" always means "has a row in pending['split']": there is no second list to drift.


def menu_people(directory) -> tuple[Participant, ...]:
    """Every participant, household first (directory order), then everyone else A-Z.

    Both people menus use this order when the card isn't tagged to a trip, so the people the
    sender picks most often are at the top and the rest are easy to find."""
    return directory.household() + tuple(sorted(
        (p for p in directory.participants if not p.is_household),
        key=lambda p: p.display_name.casefold(),
    ))


def split_groups(people, families: dict) -> tuple:
    """The trip's members grouped by family label, in the order the card lists them.

    Families are ordered by their lowest member id and people within a family by id; people
    with no family label form one group at the end. The order must not change while the card
    is open, because a family button sends its index in this tuple, not its label."""
    groups = {}
    for person in sorted(people, key=lambda p: p.id):
        groups.setdefault(families.get(person.id) or None, []).append(person)
    labels = [label for label in groups if label is not None]
    if None in groups:
        labels.append(None)
    return tuple((label, tuple(groups[label])) for label in labels)


def split_people(pending: dict) -> tuple[Participant, ...]:
    """The people the split menu shows now: the trip's members while the card is tagged to the
    trip, otherwise everyone. Decided from the card as it is now, so a "Not trip" tap changes
    the list. A tap on a person is checked against this, so a stale button is refused."""
    menu = pending['split_menu']
    people = (tuple(p for _, members in menu['groups'] for p in members)
              if pending.get('trip_id') else menu['everyone'])
    by_id = {p.id: p for p in people}
    # A ticked person must stay in the menu so they can be unticked, even when they aren't on
    # the list shown now (ticked from the everyone list, then the card was tagged back to the
    # trip). Every participant is in 'everyone', so their real record is normally there; the
    # stand-in built from the row is only a safety net, so a tick can never get stuck.
    everyone = {p.id: p for p in menu['everyone']}
    for row in pending.get('split') or ():
        pid = row['participant_id']
        if pid not in by_id:
            by_id[pid] = everyone.get(pid) or Participant(pid, row['name'], None, None)
    return tuple(by_id.values())


def split_rows(pending: dict, participant_ids) -> tuple[dict, ...]:
    """The split rows for the ticked people: {participant_id, name, share_cents, family}.

    Used for the split read from the message and for every tap, so both give identical rows,
    the rows the card draws and Confirm saves. Nobody ticked is a personal entry, `()`, and
    share_out is never called then (it refuses an empty set of sharers)."""
    ids = set(participant_ids)
    if not ids:
        return ()  # share_out deliberately refuses an empty set of sharers.
    menu = pending['split_menu']
    people = {p.id: p for p in menu['everyone']}
    people.update((p.id, p) for p in split_people(pending))
    if menu.get('amounts'):
        # Explicit amounts have priority; counts only weight the people sharing the rest.
        # Both kinds of row carry 'set', not 'count', so the card explains these shares.
        amounts = menu['amounts']
        unset = ids - amounts.keys()
        counts = menu.get('counts') or {}
        shares = share_out(menu['amount_cents'] - sum(amounts.values()),
                           {pid: counts.get(pid, 1) for pid in unset}) if unset else {}
        shares.update(amounts)
        return tuple({'participant_id': pid, 'name': people[pid].display_name,
                      'share_cents': shares[pid], 'family': menu['families'].get(pid),
                      'set': pid in amounts}
                     for pid in sorted(ids))
    return tuple({**row, 'family': menu['families'].get(row['participant_id'])}
                 for row in split_shares(menu['amount_cents'], (people[pid] for pid in ids), menu.get('counts')))


def _set_split(pending: dict, participant_ids) -> dict:
    """A copy of the entry with exactly these people ticked, and the warnings brought up to date.

    Once the ticks really change, the reading warnings ("Couldn't match 'Zed'") go: the menu is
    now the answer. Uneven-share and unread-count warnings stay until Confirm, because an even split is still
    not what the message asked for; its wording says "shown as an even split" once anyone is
    ticked, so the card never says "No split recorded" above a split. Count-addition warnings
    also persist while their person is ticked, including after unticking and re-ticking them."""
    ids = set(participant_ids)
    menu = dict(pending['split_menu'])
    ticks_changed = ids != {row['participant_id'] for row in pending.get('split') or ()}
    cleared = False
    counts = dict(menu.get('counts') or {})
    added = dict(menu.get('added_counts') or {})
    if counts or menu.get('default_count') is not None:
        # Guessed additions never vote for the fallback; only the original reading does.
        frequencies = Counter(c for pid, c in counts.items() if pid not in added)
        default = menu.get('default_count') or max(frequencies, key=lambda c: (frequencies[c], c))
        people = {p.id: p for p in split_people(pending)}
        for pid in sorted(ids - counts.keys()):
            counts[pid] = default
            added[pid] = ADDED_AT_COUNT_WARNING.format(
                name=people[pid].display_name, count=default,
                unit=menu.get('unit') or '').replace(' . Check.', '. Check.')
        menu.update(counts=counts, added_counts=added)
    if ticks_changed and menu.get('amounts'):
        old = menu['amounts']
        menu['amounts'] = {pid: cents for pid, cents in old.items() if pid in ids}
        # New ticks share the remainder. Clear only if those shares no longer fit.
        if not ids or amounts_misfit(menu, ids):
            menu['cleared_amounts'] = old if not ids else menu['amounts']
            menu.pop('amounts')
            cleared = True
    changed = {**pending, 'split_menu': menu}
    changed['split'] = split_rows(changed, ids)
    if ticks_changed:
        retained = []
        # Only while someone is still ticked: after "Remove split" there is nothing to set again.
        if ids and (cleared or AMOUNTS_CLEARED_WARNING in (pending.get('split_warnings') or ())):
            retained.append(AMOUNTS_CLEARED_WARNING)
        for unread, even in ((UNEVEN_NOT_BUILT_WARNING, UNEVEN_EVEN_SPLIT_WARNING),
                             (COUNT_UNREAD_WARNING, COUNT_EVEN_SPLIT_WARNING)):
            if any(w in (unread, even) for w in pending.get('split_warnings') or ()):
                retained.append(even if ids else unread)
        changed['split_warnings'] = tuple(retained) + tuple(
            added[pid] for pid in sorted(ids) if pid in added)
    return changed


def amounts_misfit(menu: dict, ids) -> str | None:
    """Explain a remainder that cannot give every unset person a positive share."""
    ids = set(ids)
    amounts = menu.get('amounts') or {}
    total = menu['amount_cents']
    assigned = sum(amounts.values())
    if assigned > total:
        return f"⚠️ Set amounts come to {format_cents(assigned)}, more than {format_cents(total)}."
    unset = ids - amounts.keys()
    remainder = total - assigned
    if remainder and not unset:
        return f"⚠️ {format_cents(remainder)} short and nobody left to take it. Set someone's amount, or send rest."
    if unset:
        counts = menu.get('counts') or {}
        # share_out refuses zero totals; all unset shares are zero in that case.
        shares = share_out(remainder, {pid: counts.get(pid, 1) for pid in unset}) if remainder else dict.fromkeys(unset, 0)
        people = {p.id: p.display_name for p in menu['everyone']}
        for pid in sorted(unset):
            if shares[pid] == 0:
                name = people[pid]
                return f"⚠️ Nothing left for {name}. Lower an amount, or untick {name}."
    return None


def parse_split_amounts(pending: dict, text: str, sender_id: int) -> dict:
    """Merge named amounts into a copy; reject misfits without touching the card.

    Amount tokens end pairs, so separators are optional and names may have several words.
    The shared money converter owns validation and half-up rounding.
    """
    ids = {row['participant_id'] for row in pending.get('split') or ()}
    people = tuple(p for p in split_people(pending) if p.id in ids)
    if len(ids) < 2:
        raise ValueError('⚠️ Tick at least two people, then Set amounts again.')
    amounts = dict(pending['split_menu'].get('amounts') or {})
    seen = set()
    words = []
    for token in re.split(r'[\s,:]+', text.strip()):
        if not token:
            continue
        # Include malformed numeric tokens so the money helper can reject them clearly.
        is_amount = re.match(r'^\$?[+\-]?(?:\d|\.)', token)
        if token.casefold() != 'rest' and not is_amount:
            words.append(token)
            continue
        name = ' '.join(words)
        words = []
        if not name:
            raise ValueError(f"⚠️ Couldn't read '{token}'. Send a name and amount, e.g. Bob 90.")
        person = (next((p for p in people if p.id == sender_id), None)
                  if name.casefold() in _SENDER_WORDS else match_person(name, people))
        if person is None:
            raise ValueError(f"⚠️ Couldn't match '{name}'.")
        if person.id in seen:
            raise ValueError(f'⚠️ More than one amount for {person.display_name}.')
        seen.add(person.id)
        if token.casefold() == 'rest':
            amounts.pop(person.id, None)
        else:
            try:
                amounts[person.id] = dollars_to_cents(token.removeprefix('$'))
            except ValueError:
                raise ValueError(f"⚠️ Invalid amount for {person.display_name}. Use a positive amount; for zero, untick them instead.") from None
    if words or not seen:
        unread = ' '.join(words)
        raise ValueError(f"⚠️ Couldn't read '{unread}'. Send a name and amount, e.g. Bob 90.")
    menu = {**pending['split_menu'], 'amounts': amounts}
    error = amounts_misfit(menu, ids)
    if error:
        raise ValueError(error)
    menu.pop('cleared_amounts', None)
    removed = {UNEVEN_NOT_BUILT_WARNING, UNEVEN_EVEN_SPLIT_WARNING, AMOUNTS_CLEARED_WARNING}
    # Counts still decide the remainder until everyone has an explicit amount.
    if ids <= amounts.keys():
        removed.update({COUNT_UNREAD_WARNING, COUNT_EVEN_SPLIT_WARNING, *menu.get('added_counts', {}).values()})
    changed = {**pending, 'split_menu': menu,
               'split_warnings': tuple(w for w in pending.get('split_warnings', ()) if w not in removed)}
    changed['split'] = split_rows(changed, ids)
    return changed


def amounts_prompt(pending: dict, error: str | None = None) -> tuple[str, str]:
    """Show current shares or cleared hints, keeping the restart marker first."""
    menu = pending['split_menu']
    amounts = menu.get('amounts') or {}
    old = menu.get('cleared_amounts') or {}
    rows = pending.get('split') or ()
    lines = [AMOUNTS_PROMPT_LINE]
    if error:
        lines.append(error)
    lines.append(f"Total {format_cents(menu['amount_cents'])}.")
    for row in rows:
        pid = row['participant_id']
        suffix = ''
        if amounts:
            suffix = f" {format_cents(row['share_cents'])}" + (' ✏️' if pid in amounts else " (what's left)")
        elif pid in old:
            suffix = f" (was {format_cents(old[pid])})"
        lines.append(row['name'] + suffix)
    lines.append("Send a name and amount, e.g. Bob 90. Anyone not set shares what's left; send rest to put someone back.")
    first = next((row for row in rows if row['participant_id'] not in amounts), rows[0] if rows else None)
    placeholder = f"{first['name']} 50" if first else ''
    return '\n'.join(lines), placeholder[:64]


def open_split(pending: dict) -> dict:
    """Opening the menu. On a card tagged to a trip with no split yet, the first opening ticks
    every trip member and applies that split at once, so the ticks and the card agree. It
    happens once only: after "Remove split", reopening ticks nobody."""
    menu = pending['split_menu']
    changed = dict(pending)
    if pending.get('trip_id') and not pending.get('split') and not menu.get('opened'):
        changed = _set_split(pending, (p.id for _, members in menu['groups'] for p in members))
    changed['split_menu'] = {**changed['split_menu'], 'opened': True}
    return changed


def toggle_split_person(pending: dict, participant_id: int) -> dict:
    """Tick or untick one person. Raises ValueError for someone the menu doesn't show now."""
    if participant_id not in {p.id for p in split_people(pending)}:
        raise ValueError('Person is not offered')
    ids = {row['participant_id'] for row in pending.get('split') or ()}
    return _set_split(pending, ids ^ {participant_id})


def toggle_split_family(pending: dict, index: int) -> dict:
    """Tick a whole family, or untick it when every member is already ticked. Raises ValueError
    for an index the menu doesn't show now: the card isn't tagged to the trip, the index is out
    of range, or the group has no label (the unlabelled group has no toggle)."""
    groups = pending['split_menu']['groups']
    if not pending.get('trip_id') or not 0 <= index < len(groups) or not groups[index][0]:
        raise ValueError('Family is not offered')
    members = {p.id for p in groups[index][1]}
    ids = {row['participant_id'] for row in pending.get('split') or ()}
    return _set_split(pending, ids - members if members <= ids else ids | members)


def remove_split(pending: dict) -> dict:
    """"Remove split": untick everyone. Also marks the menu as opened, so a later opening
    doesn't tick everyone again and undo the choice."""
    changed = _set_split(pending, ())
    changed['split_menu'] = {**changed['split_menu'], 'opened': True}
    return changed
