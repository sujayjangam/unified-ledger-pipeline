"""Read a typed payback and assign whole cents to open shares (ADR-0052).

The caller supplies a snapshot, already folded by open_bill_shares. Nothing here
loads bills, nets debts, looks up exchange rates or saves a payment. Replaying a
button choice against the same snapshot therefore gives the same card.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date

from app.add_expense import dollars_to_cents
from app.services.currencies import ISO_4217
from app.services.participants import Participant, match_person
from app.services.splits import _label_key

VERBS = ("paid", "pays", "gave", "sent", "transferred")
_NEEDS = 'Settle needs who paid and how much, e.g. "Settle Carol paid 381".'
_STOP = frozenset("the a an my our his her their share shares part portion of".split())
_START = re.compile(r"^\s*settle\b", re.IGNORECASE)
_VERB = re.compile(r"\b(?:" + "|".join(VERBS) + r")\b", re.IGNORECASE)
_NUMBER = re.compile(r"[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?")


class SettleRefused(Exception):
    """An unreadable or inconsistent payback; str(exc) is the card's refusal."""


@dataclass(frozen=True)
class SettlePart:
    """One bill phrase and its beneficiaries, in the order they were named."""

    people: tuple[int, ...]
    words: tuple[str, ...]
    said: str


@dataclass(frozen=True)
class SettleReading:
    """The literal payment, before any bill or receiver has been inferred."""

    giver_id: int
    receiver_id: int | None
    cents: int
    currency: str | None
    parts: tuple[SettlePart, ...]


@dataclass(frozen=True)
class Bill:
    """A live bill with gross, still-open shares; input order is oldest first."""

    transaction_id: str
    trip_id: int | None
    date: date
    description: str
    amount_cents: int
    currency: str
    payer_id: int
    open: Mapping[int, int]


@dataclass(frozen=True)
class BillMatch:
    """Transaction ids for a full, close or absent description match."""

    kind: str
    matched: tuple[str, ...]
    ticked: tuple[str, ...]


@dataclass(frozen=True)
class SettleChoices:
    """Per-card overrides; a missing pick retains the oldest close match."""

    receiver_id: int | None = None
    picks: Mapping[int, str] = field(default_factory=dict)
    oldest_first: frozenset[int] = frozenset()
    everyday: bool = False
    giver_id: int | None = None
    shares: tuple[tuple[str, int], ...] | None = None


@dataclass(frozen=True)
class SettleRow:
    """Whole cents clearing one beneficiary's share of one bill."""

    transaction_id: str
    for_id: int
    cents: int


@dataclass(frozen=True)
class PartResult:
    """The ticks and explanation needed to redraw one part of the card."""

    kind: str
    matched: tuple[str, ...]
    ticked: tuple[str, ...]
    warning: str | None


@dataclass(frozen=True)
class SettleResult:
    """A proposed payback, or a receiver/rate question with no writable rows."""

    giver_id: int
    receiver_id: int | None
    trip_id: int | None
    currency: str | None
    paid_currency: str | None
    rows: tuple[SettleRow, ...]
    overpaid_cents: int
    parts: tuple[PartResult, ...]
    can_confirm: bool
    clears_all: bool
    receiver_options: tuple[tuple[int, int], ...] = ()
    needs_rate: tuple[str, str] | None = None
    question: str | None = None
    question_said: str | None = None
    question_payer: int | None = None


def card_people(reading: SettleReading, choices: SettleChoices) -> tuple[int, ...]:
    """Names explicitly said survive changing the giver; unnamed parts follow the giver."""
    giver = choices.giver_id if choices.giver_id is not None else reading.giver_id
    return tuple(dict.fromkeys(p for part in reading.parts for p in (part.people or (giver,)))) or (giver,)


def receiver_options(reading: SettleReading, bills: Sequence[Bill], result: SettleResult,
                     choices: SettleChoices = SettleChoices()) -> tuple[tuple[int, int], ...]:
    """People owed in this card's scope and currency, largest debt first."""
    totals = {}
    for bill in bills:
        if bill.trip_id != result.trip_id or result.currency and bill.currency != result.currency:
            continue
        cents = sum(max(0, bill.open.get(p, 0)) for p in card_people(reading, choices))
        if cents and bill.payer_id != result.giver_id:
            totals[bill.payer_id] = totals.get(bill.payer_id, 0) + cents
    return tuple(sorted(totals.items(), key=lambda item: (-item[1], item[0])))


def bill_share_options(reading: SettleReading, bills: Sequence[Bill], result: SettleResult,
                       choices: SettleChoices = SettleChoices()) -> tuple[tuple[str, int, int, bool], ...]:
    """Open shares as (bill id, person id, cents, ticked), oldest bill then named person.

    Ticks include selected shares receiving zero cents, so a short payment can still
    show every intended bill. The menu may move ticked rows first for display only.
    """
    selected = choices.shares
    if selected is None:
        parts = reading.parts or (SettlePart((), (), ""),)
        selected = tuple((bid, p) for part, found in zip(parts, result.parts)
                         for bid in found.ticked
                         for p in (part.people or (result.giver_id,)))
    return tuple((b.transaction_id, p, b.open[p], (b.transaction_id, p) in selected)
                 for b in bills if b.trip_id == result.trip_id and b.payer_id == result.receiver_id
                 and (result.currency is None or b.currency == result.currency)
                 for p in card_people(reading, choices) if b.open.get(p, 0) > 0)


def is_settle(text: str) -> bool:
    """Route only the first word 'settle', including voice-note punctuation."""
    return bool(_START.match(text))


def normalise(text: str) -> tuple[str, ...]:
    """Use the same whole words on both sides; never invent synonyms or typos."""
    words = re.findall(r"[^\W_]+", text.lower(), re.UNICODE)
    # A possessive splits off a lone "s" ("mum's" -> "mum", "s"), which would become an empty
    # word after the plural "s" is dropped and then never match; drop empties too.
    return tuple(w for w in (w.removesuffix("s") for w in words if w not in _STOP) if w)


def _person(text, candidates, sender):
    if text.casefold() in {"i", "me"}:
        return sender
    return match_person(text, candidates)


def _part(said, candidates, sender, families):
    """Longest names win before family labels; leftover words describe the bill."""
    said = re.sub(r"(?:\s*\band\b|[\s,&])+$", "", said.strip(), flags=re.I)
    tokens = re.findall(r"[^\W_]+(?:['’]s)?|[,&]", said)
    people, bill_words = [], []
    i = 0
    while i < len(tokens):
        found = ()
        for length in range(min(3, len(tokens) - i), 0, -1):
            span = " ".join(tokens[i:i + length])
            person = _person(span, candidates, sender)
            if person:
                found = (person.id,)
            else:
                key = _label_key(span)
                labels = {key, key.removesuffix("s")}
                found = tuple(p.id for p in candidates
                              if p.id in families and _label_key(families[p.id]) in labels)
            if found:
                break
        if found:
            people.extend(p for p in found if p not in people)
            i += length
        else:
            if tokens[i].casefold() not in {"and", ",", "&"}:
                bill_words.append(tokens[i])
            i += 1
    return SettlePart(tuple(people), normalise(" ".join(bill_words)), said)


def read_settle(text: str, candidates: Sequence[Participant], sender: Participant,
                families: Mapping[int, str]) -> SettleReading:
    """Read the fixed grammar, refusing leftovers in the payment clause.

    'for airbnb for the Ngs' is the documented postfix-beneficiary spelling:
    an immediately following names-only clause qualifies a bill-only clause.
    Other clauses stay separate, so money follows the order of the message.
    """
    start = _START.match(text)
    if not start:
        raise SettleRefused(_NEEDS)
    body = text[start.end():].lstrip(" \t\r\n,.:;!?—-").rstrip(" \t\r\n.!?;,")
    clauses = re.split(r"\bfor\b", body, flags=re.I)
    head = clauses[0].strip()
    verb = _VERB.search(head)
    if not verb or not head[:verb.start()].strip():
        raise SettleRefused(_NEEDS)
    giver_text = head[:verb.start()].strip()
    rest = head[verb.end():].strip()
    number = _NUMBER.search(rest)
    if not number:
        raise SettleRefused(_NEEDS)
    before, after = rest[:number.start()].strip(), rest[number.end():].strip()
    currency = None
    # Consume only actual ISO codes, never the first three letters of a name.
    prefix = re.search(r"(?<!\w)([A-Za-z]{3})$", before)
    suffix = re.match(r"^([A-Za-z]{3})(?!\w)", after)
    # A known receiver wins over a coincidentally valid code (Bob / BOB).
    # Currency after the amount remains unambiguous for that rare collision.
    if prefix and prefix[1].upper() in ISO_4217 and _person(prefix[1], candidates, sender) is None:
        currency, before = prefix[1].upper(), before[:prefix.start()].strip()
    if suffix and suffix[1].upper() in ISO_4217:
        if currency and currency != suffix[1].upper():
            raise SettleRefused(_NEEDS)
        currency, after = suffix[1].upper(), after[suffix.end():].strip()
    before = re.sub(r"(?:S)?\$$", "", before, flags=re.I).strip()
    # Back/to are grammar, not parts of a receiver's name.
    before = re.sub(r"^(?:(?:back|to)\b\s*)+", "", before, flags=re.I)
    before = re.sub(r"\s*\bback$", "", before, flags=re.I).strip()
    receiver_text = before
    if after:
        tail = re.fullmatch(r"to\s+(.+)", after, flags=re.I)
        if not tail or receiver_text:
            raise SettleRefused(_NEEDS)
        receiver_text = tail[1].strip()
    try:
        cents = dollars_to_cents(number[0].replace(",", ""))
    except ValueError:
        raise SettleRefused(_NEEDS) from None
    giver = _person(giver_text, candidates, sender)
    if giver is None:
        raise SettleRefused(f'Couldn\'t match "{giver_text}" as who paid.')
    receiver = _person(receiver_text, candidates, sender) if receiver_text else None
    if receiver_text and receiver is None:
        raise SettleRefused(f'Couldn\'t match "{receiver_text}" as who got it.')
    if receiver and receiver.id == giver.id:
        raise SettleRefused("Who paid and who got it must be two different people.")
    parts = []
    for index, clause in enumerate(clauses[1:]):
        part = _part(clause, candidates, sender, families)
        if not part.people and not part.words:
            raise SettleRefused(_NEEDS)
        # An explicit joiner means another allocation part ('and for Erin'),
        # whereas bare 'for airbnb for Erin' qualifies the bill just mentioned.
        joined = re.search(r"(?:\band|,|&)\s*$", clauses[index], flags=re.I)
        if (part.people and not part.words and parts and parts[-1].words
                and not parts[-1].people and not joined):
            previous = parts.pop()
            parts.append(SettlePart(part.people, previous.words, previous.said))
        elif (part.words and not part.people and parts and parts[-1].people
                and not parts[-1].words and not joined):
            # The same pairing the other way round: "for Erin for the airbnb" is Erin's
            # share of the airbnb, not Erin's whole debt and then the giver's own airbnb.
            previous = parts.pop()
            parts.append(SettlePart(previous.people, part.words, part.said))
        else:
            parts.append(part)
    return SettleReading(giver.id, receiver.id if receiver else None, cents, currency, tuple(parts))


def match_bills(words: tuple[str, ...], bills: Sequence[Bill], money_left: int,
                people: Sequence[int], *, currency: str | None = None) -> BillMatch:
    """Prefer all full matches; an amount can corroborate words, never replace them."""
    full, close = [], []
    wanted = set(words)
    for bill in bills:
        description = set(normalise(bill.description))
        # The amount backs up the words only in the bill's own currency: 100 SGD is
        # never "equal" to an INR share of 10,000 cents, and a converted amount is
        # approximate. So a payback said in another currency never close-matches, and
        # the reading stays the same before and after the rate is applied.
        same_money = currency is None or bill.currency == currency
        if wanted and wanted <= description:
            full.append(bill.transaction_id)
        elif wanted & description and same_money and (money_left == bill.amount_cents or any(
                money_left == bill.open.get(p, 0) and money_left > 0 for p in people)):
            close.append(bill.transaction_id)
    if full:
        return BillMatch("full", tuple(full), tuple(full))
    return BillMatch("close", tuple(close), tuple(close[:1])) if close else BillMatch("none", (), ())


def _owing(bills, people):
    return tuple(b for b in bills if any(b.open.get(p, 0) > 0 for p in people))


def _walk(parts, bills, amount, choices, names, *, everyday=(), paid=None):
    """Select and fill together: later close matches must see the money left.

    The returned explicit ticks decide scope/receiver. Fallback ticks do not:
    a miss with trip mode off must never silently select an unrelated trip.
    All selected shares are kept, even after money runs out, for clears_all.
    `paid` is the currency the message said, if any (see match_bills).
    """
    if choices.shares is not None:
        # Manual ticks replace phrase matching, but retain chronological allocation.
        people = tuple(dict.fromkeys(p for part in parts for p in part.people))
        left, rows, selected, explicit = amount, [], {}, []
        for bill in bills:
            for person in people:
                key = (bill.transaction_id, person)
                if key not in choices.shares or bill.open.get(person, 0) <= 0:
                    continue
                selected[key] = bill.open[person]
                explicit.append((0, bill))
                cents = min(left, bill.open[person])
                if cents:
                    rows.append(SettleRow(*key, cents))
                    left -= cents
        return (tuple(rows), left, (), explicit,
                bool(selected) and sum(selected.values()) == amount)
    rows, selected, explicit, results = {}, {}, [], []
    left = amount
    for i, part in enumerate(parts):
        candidates = _owing(bills, part.people)
        match = match_bills(part.words, candidates, left, part.people, currency=paid)
        kind, ticks, warning = match.kind, match.ticked, None
        if not part.words:
            kind = "names"
        elif kind != "full" and i in choices.oldest_first:
            kind, ticks = "none", ()
        if kind == "close":
            pick = choices.picks.get(i)
            ticks = (pick,) if pick in match.matched else ticks
            # "1 bills could fit" read badly on the test bot: one candidate needs no count.
            fit = f" {len(match.matched)} bills could fit." if len(match.matched) > 1 else ""
            warning = f"❓ Did you mean this bill?{fit}"
        elif kind == "full" and len(ticks) > 1:
            warning = f"⚠️ {len(ticks)} bills matched"
        if kind in {"full", "close"}:
            explicit.extend((i, b) for b in candidates if b.transaction_id in ticks)
        if kind == "none" and match_bills(
                part.words, _owing(everyday, part.people), left, part.people,
                currency=paid).kind != "none":
            kind = "everyday_only"
            warning = f'❓ "{part.said}" matched an everyday bill, not a trip bill.'
        elif kind in {"none", "names"}:
            ticks = tuple(b.transaction_id for b in candidates)
            if kind == "none":
                who = " and ".join(names[p] for p in part.people)
                warning = f'⚠️ No bill matched "{part.said}". Used {who}\'s oldest bills. Check.'
        results.append(PartResult(kind, match.matched if kind == "close" else (), ticks, warning))
        for bill in candidates:
            if bill.transaction_id not in ticks:
                continue
            for person in part.people:
                key = (bill.transaction_id, person)
                owed = max(0, bill.open.get(person, 0))
                if not owed:
                    continue
                selected[key] = owed
                cents = min(left, max(0, owed - rows.get(key, 0)))
                if cents:
                    rows[key] = rows.get(key, 0) + cents
                    left -= cents
    return (tuple(SettleRow(*key, cents) for key, cents in rows.items()), left,
            tuple(results), explicit, bool(selected) and rows == selected and left == 0)


def settle_up(reading: SettleReading, bills: Sequence[Bill], *, trip_id: int | None,
              names: Mapping[int, str], choices: SettleChoices = SettleChoices(),
              clears_cents: int | None = None) -> SettleResult:
    """Resolve scope, receiver and currency, then fill the selected open shares.

    Names are a display-only snapshot. Bills are already oldest first, and that
    order also breaks close-match ties. A rate question never guesses converted
    cents; the caller returns with clears_cents after obtaining the conversion.
    """
    if choices.giver_id is not None:
        reading = replace(reading, giver_id=choices.giver_id)
    amount = reading.cents if clears_cents is None else clears_cents
    if type(amount) is not int or amount <= 0:
        raise ValueError("The amount to clear must be positive integer cents")
    if choices.shares == ():
        # Unticking the last share must leave an editable menu, not lose the
        # inferred scope/receiver and turn into a refusal or an everyday payment.
        original = settle_up(reading, bills, trip_id=trip_id, names=names,
                             choices=replace(choices, shares=None), clears_cents=clears_cents)
        return replace(original, rows=(), overpaid_cents=amount, parts=(),
                       can_confirm=False, clears_all=False)
    parts = tuple(SettlePart(p.people or (reading.giver_id,), p.words, p.said)
                  for p in reading.parts) or (SettlePart((reading.giver_id,), (), ""),)
    concerned = tuple(dict.fromkeys(p for part in parts for p in part.people))
    receiver = choices.receiver_id if choices.receiver_id is not None else reading.receiver_id
    if receiver == reading.giver_id:
        raise SettleRefused("Who paid and who got it must be two different people.")
    scope = None if choices.everyday else trip_id
    base = tuple(b for b in bills if choices.everyday and b.trip_id is None
                 or not choices.everyday and (trip_id is None or b.trip_id == trip_id))
    base = _owing(base, concerned)

    def currency_filter(candidates):
        """Keep the bills in the currency that this payback is about.

        A bill named by its words decides first: "100 SGD for diwali deco" on an INR
        bill is an other-currency payback of that bill (a rate question), never a
        payment of some unrelated SGD bill. Only when no bill is named does the paid
        currency pick the bills; a foreign paid currency is kept when none use it.
        With no currency said, nothing is filtered, so bills in two currencies still
        reach the "Say which" refusal.
        """
        if reading.currency is None or choices.shares is not None:
            return candidates
        # Per part: a part whose words match bills in the paid currency (and others)
        # keeps only those, but two parts naming bills in different currencies keep
        # both, so the one-currency check below refuses instead of dropping a bill.
        per_part = {}
        for i, bill in _walk(parts, candidates, amount, choices, names, paid=reading.currency)[3]:
            per_part.setdefault(i, set()).add(bill.currency)
        named = set()
        for codes in per_part.values():
            named |= {reading.currency} if reading.currency in codes else codes
        if named:
            return tuple(b for b in candidates if b.currency in named)
        matching = tuple(b for b in candidates if b.currency == reading.currency)
        return matching or candidates

    def discover(candidates):
        return _walk(parts, candidates, amount, choices, names, paid=reading.currency)[3]

    def question(kind, said=None, payer=None, bill_currency=None):
        # Questions never carry writable allocations. The same option helper powers
        # both these suggestions and Change receiver, preventing divergent debts.
        scoped = tuple(b for b in base if b.trip_id == scope and (payer is None or b.payer_id == payer))
        # "The saree was paid by Carol" is about that one bill: its currency decides,
        # so the giver's other debts to Carol in another currency can't turn the
        # suggestion into a "Say which" refusal (found on the test bot).
        codes = ({bill_currency} if bill_currency and reading.currency is None
                 else {b.currency for b in currency_filter(scoped)})
        if len(codes) > 1:
            ordered = sorted(codes)
            raise SettleRefused(f'{names[reading.giver_id]} owes in {" and ".join(ordered)}. '
                                f'Say which, e.g. "Settle {names[reading.giver_id]} paid 50 {ordered[0]}".')
        currency = next(iter(codes)) if len(codes) == 1 else reading.currency
        result = SettleResult(reading.giver_id, receiver, scope, currency,
                              reading.currency or currency, (), amount, (), False, False,
                              question=kind, question_said=said, question_payer=payer)
        options = receiver_options(reading, bills, result, choices)
        if payer is not None:
            options = tuple(item for item in options if item[0] == payer)
        if not options:
            raise SettleRefused("No match.")
        return replace(result, receiver_options=options)

    pool = tuple(b for b in base if receiver is None or b.payer_id == receiver)
    if trip_id is None and not choices.everyday:
        # Trip mode off: the bills named decide the scope (none named: everyday). This
        # runs before the paid currency filters anything, so an unrelated trip debt in
        # that currency can't hide the everyday bill the payback is really for.
        scopes = {b.trip_id for _, b in discover(pool)}
        if not scopes and receiver is not None:
            # A wrongly named receiver must not hide the trip of a named bill;
            # that scope is needed for the paid-by-other suggestion below.
            scopes = {b.trip_id for _, b in discover(base)}
        if len(scopes) > 1:
            if None in scopes:
                raise SettleRefused("These bills are on a trip and outside it. Send one settle message for each.")
            raise SettleRefused("These bills are on two trips. Send one settle message for each.")
        scope = next(iter(scopes), None)
        pool = tuple(b for b in pool if b.trip_id == scope)
    candidates = currency_filter(pool)
    ticks = discover(candidates)
    if receiver is not None:
        # Narrow first. Only a phrase with no match to this receiver is checked
        # against other payers, so another person's identical dinner is harmless.
        matched_parts = {i for i, _ in ticks}
        for i, bill in discover(currency_filter(tuple(b for b in base if b.trip_id == scope))):
            if i not in matched_parts and bill.payer_id != receiver:
                return question("paid_by_other", parts[i].said, bill.payer_id, bill.currency)
    payers = {b.payer_id for _, b in ticks}
    if receiver is None and len(payers) > 1:
        who = " and ".join(names[p] for p in sorted(payers))
        raise SettleRefused(f"These bills were paid by {who}. Send one settle message for each.")
    if receiver is None and payers:
        receiver = next(iter(payers))
    if receiver is not None:
        candidates = tuple(b for b in candidates if b.payer_id == receiver)
    totals = {}
    for bill in candidates:
        totals[bill.payer_id] = totals.get(bill.payer_id, 0) + sum(
            max(0, bill.open.get(p, 0)) for p in concerned)
    # A receiver worked out only from who is owed (no bill named, none given) is a
    # fallback: it must not hide an everyday bill the words name (see below).
    guessed = receiver is None and len(totals) == 1
    if guessed:
        receiver = next(iter(totals))
    if receiver == reading.giver_id:
        raise SettleRefused("Who paid and who got it must be two different people.")
    # Check every selected share, including names-only and fallback parts, even
    # when an earlier part exhausts the money. Otherwise that later part could
    # silently disappear when candidates are restricted to the first currency.
    # Everyday bills the words might name (trip mode on only). Worked out before the
    # preview so a part that matches only an everyday bill picks no fallback trip
    # bills there, and so can't trigger the two-currency refusal by accident.
    everyday = tuple(b for b in bills if b.trip_id is None
                     and (receiver is None or guessed or b.payer_id == receiver)) if trip_id is not None and not choices.everyday else ()
    preview = _walk(parts, candidates, amount, choices, names, paid=reading.currency,
                    everyday=currency_filter(everyday))[2]
    selected_ids = ({bid for bid, _ in choices.shares} if choices.shares is not None else
                    {id for part in preview for id in part.ticked})
    currency_bills = tuple(b for b in candidates if b.transaction_id in selected_ids)
    codes = sorted({b.currency for b in currency_bills})
    if len(codes) > 1:
        to = f" {names[receiver]}" if receiver is not None else ""
        raise SettleRefused(f'{names[reading.giver_id]} owes{to} in {" and ".join(codes)}. '
                            f'Say which, e.g. "Settle {names[reading.giver_id]} paid 50 {codes[0]}".')
    currency = codes[0] if codes else None
    paid_currency = reading.currency or currency
    options = tuple(sorted(totals.items(), key=lambda item: (-item[1], item[0]))) if receiver is None else ()
    needs_rate = ((paid_currency, currency) if currency and paid_currency != currency
                  and clears_cents is None else None)
    if currency:
        candidates = tuple(b for b in candidates if b.currency == currency)
    rows, left, results, _, cleared = _walk(parts, candidates, amount, choices, names, paid=reading.currency,
                                          everyday=currency_filter(everyday))
    blocked = any(p.kind == "everyday_only" for p in results)
    if receiver is not None and not candidates and not blocked:
        return question("owes_nothing")
    if receiver is None and not options and not blocked:
        # Nobody to pay: a card with no receiver, no rows and no Confirm would be a dead
        # end, so say why instead.
        raise SettleRefused("No match.")
    if options or needs_rate:
        rows, left, cleared = (), amount, False
    return SettleResult(reading.giver_id, receiver, scope, currency, paid_currency,
                        rows, left, results, bool(receiver is not None and not options
                        and not needs_rate and not blocked and bool(rows)), cleared and not blocked,
                        options, needs_rate)
