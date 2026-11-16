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

from app.services.participants import Participant, match_person

# Card lines. Display only: never stored, and they never block a save.
UNEVEN_NOT_BUILT_WARNING = "⚠️ This asks for uneven shares, which the bot can't do yet. No split recorded."
UNEVEN_EVEN_SPLIT_WARNING = "⚠️ This asked for uneven shares, which the bot can't do yet. Shown as an even split."
EVERYONE_NEEDS_TRIP_WARNING = '⚠️ "Everyone" needs a trip switched on. No split recorded.'
NOBODY_SHARES_WARNING = "⚠️ Couldn't tell who shares this. No split recorded."
NO_MATCH_WARNING = "⚠️ Couldn't match '{name}'"
ADDED_AT_COUNT_WARNING = "⚠️ {name} added at {count} {unit}. Check."
EXCEPT_UNREAD_WARNING = "⚠️ Couldn't tell who is left out. No split recorded."
COUNT_UNREAD_WARNING = "⚠️ Couldn't read a count for everyone. No split recorded."
COUNT_EVEN_SPLIT_WARNING = "⚠️ Couldn't read a count for everyone. Shown as an even split."

# Words that mean the sender. A 'me' entry carries their explicit count, while
# include_sender alone includes them without specifying a count.
_SENDER_WORDS = frozenset({"me", "i", "myself"})
_COLLECTIVE_WORDS = frozenset({"everyone", "everybody", "all", "the rest", "rest", "others", "everyone else"})
# "Carol's family", "Carol’s family", "Carol family": the name before the family wording.
_FAMILY_SUFFIX = re.compile(r"^(?P<name>.+?)(?:'s|’s)?\s+family$", re.IGNORECASE)
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


def asks_for_split(message: str) -> bool:
    """True when the message contains a split word ("split", "shared", "between"...)."""
    return _SPLIT_WORD.search(message) is not None


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
    a split by nights is the number of nights each (PR 3 uses that, the maths is already here).

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


def _valid_count(count, message: str | None) -> bool:
    """Check literal counts, never infer them from amounts or other numbers."""
    if type(count) is not int or not 1 <= count <= 999:
        return False
    if message is None:
        return True
    words = ('one two three four five six seven eight nine ten eleven twelve thirteen '
             'fourteen fifteen sixteen seventeen eighteen nineteen twenty').split()
    words += [f'twenty[ -]{word}' for word in words[:9]] + ['thirty']
    alternatives = str(count)
    if count <= len(words):
        alternatives += '|' + words[count - 1]
    # Do not find 3 inside 2300, 3.50, or 3,000. Dictated numbers are case insensitive.
    return re.search(rf'(?<![\w.])(?:{alternatives})(?!\w|[.,]\d)', message, re.IGNORECASE) is not None


def _number_after_except(message: str) -> bool:
    """True when a whole number (digits or a number word) comes after the first except word."""
    found = _EXCEPT_WORD.search(message)
    if not found:
        return False
    rest = message[found.end():]
    return any(_valid_count(n, rest) for n in range(1, 31)) or re.search(r"(?<![\w.])\d+(?![\w.,]\d)", rest) is not None


def resolve_split(reference: dict | None, candidates, sender: Participant,
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
    entries = reference.get("people") or ()
    exceptions = reference.get("except_people") or ()
    default_count = reference.get("default_count")
    by_count = default_count is not None or any(
        isinstance(e, dict) and e.get("count") is not None for e in (*entries, *exceptions))
    if message is not None and not asks_for_split(message) and not (
            by_count and _EVERYONE_WORD.search(message)):
        # The model read a split the message never asked for. A personal entry, silently: the
        # message decides.
        return SplitResult((), ())
    if reference.get("uneven") and not by_count:
        # Recording an even split for "split by nights" would be wrong without looking wrong.
        return SplitResult((), (UNEVEN_NOT_BUILT_WARNING,))
    # The shorthand without a split word is allowed only with trustworthy counts.
    # Unused defaults are deliberately checked later, after resolving the sharers.
    if message is not None and not asks_for_split(message) and any(
            isinstance(e, dict) and e.get('count') is not None
            and not _valid_count(e['count'], message) for e in (*entries, *exceptions)):
        return SplitResult((), (COUNT_UNREAD_WARNING,))
    candidates = tuple(candidates)
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
        person = sender if name.strip().casefold() in _SENDER_WORDS else match_person(name, candidates)
        if person is None:
            warnings.append(NO_MATCH_WARNING.format(name=name))
            return []
        label = families.get(person.id)
        if is_family and label:
            return [p for p in candidates if families.get(p.id) == label]
        return [person]

    # Keyed by id, so naming someone twice (or the sender by name and by "with") is one share.
    people = {}
    explicit_counts = {}
    kind = reference.get("kind")
    if kind == "everyone" and entries and message is not None and not _EVERYONE_WORD.search(message):
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
    if by_count:
        counts = {pid: explicit_counts.get(pid, default_count) for pid in people}
        # An unused model default is discarded; menu additions use the modal read count.
        if all(pid in explicit_counts for pid in people):
            default_count = None
        if any(not _valid_count(c, message) for c in counts.values()):
            return SplitResult((), (*warnings, COUNT_UNREAD_WARNING))
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
    changed = {**pending, 'split_menu': menu}
    changed['split'] = split_rows(changed, ids)
    if ids != {row['participant_id'] for row in pending.get('split') or ()}:
        retained = []
        for unread, even in ((UNEVEN_NOT_BUILT_WARNING, UNEVEN_EVEN_SPLIT_WARNING),
                             (COUNT_UNREAD_WARNING, COUNT_EVEN_SPLIT_WARNING)):
            if any(w in (unread, even) for w in pending.get('split_warnings') or ()):
                retained.append(even if ids else unread)
        changed['split_warnings'] = tuple(retained) + tuple(
            added[pid] for pid in sorted(ids) if pid in added)
    return changed


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
