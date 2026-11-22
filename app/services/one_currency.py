"""Convert payment suggestions without changing the underlying per-currency debts."""

from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_HALF_UP

from app.services.balances import Payment, debtor_unit, unit_of


@dataclass(frozen=True)
class Part:
    """A whole payment or a conserved piece of one, in both currencies.

    Each part is saved as one ordinary payback of its own payment: from `giver` (the card's
    payer when None) to the payment's receiver. A reverse part takes money off the line; a
    part handed over by someone else (a shortcut's middle person) is passed straight on, so
    it doesn't change the amount on the card.
    """
    number: int
    currency: str
    payment: Payment
    cents_in_currency: int
    sgd_cents: int
    reverse: bool = False
    giver: int | None = None


@dataclass(frozen=True)
class Line:
    """One suggested transfer, with all debts it clears retained for Confirm."""
    from_unit: tuple
    to_person: int
    sgd_cents: int
    parts: tuple[Part, ...]
    # One (middle unit, middle person, SGD cents) per shortcut moved onto this line, read as
    # "from_unit pays to_person the x the middle unit owes, and pays the middle person x less".
    shortcuts: tuple = ()


def _round(value):
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def take_line_parts(parts, first, cents):
    """Partition shortened bill paths without assigning the same bill cents twice.

    A reverse payment may be shared by two SGD cards. Each gets its own portion of
    every hop, using payment_rows' member/chain order; Confirm can still call that
    unchanged helper on the retained portion, in either card order.
    """
    totals = {}
    for part in parts:
        totals[part.member] = totals.get(part.member, 0) + part.cents
    ordered = sorted(parts, key=lambda p: (p.member != first, -totals[p.member],
                                           p.member, -len(p.hops)))
    taken, remaining = [], []
    for part in ordered:
        used = min(cents, part.cents)
        taken_hops, remaining_hops = [], []
        for hop in part.hops:
            left, before, after = used, [], []
            for transaction, member, payer, amount in hop:
                piece = min(left, amount)
                if piece:
                    before.append((transaction, member, payer, piece))
                if amount > piece:
                    after.append((transaction, member, payer, amount - piece))
                left -= piece
            taken_hops.append(tuple(before))
            remaining_hops.append(tuple(after))
        if used:
            taken.append(replace(part, cents=used, hops=tuple(taken_hops)))
        if used < part.cents:
            remaining.append(replace(part, cents=part.cents - used, hops=tuple(remaining_hops)))
        cents -= used
    return tuple(taken), tuple(remaining)


def one_currency(payments, families, rates):
    """Group and net in SGD; payment numbers remain those of trip_payments.

    Ties use the first original payment number. Cross-family netting consumes the
    largest forward line first. The last reverse piece owns any rounding remainder,
    so neither bill cents nor converted cents disappear when a payment spans lines.
    """
    grouped = {}
    for number, (currency, payment) in enumerate(payments, 1):
        rate = Decimal(1) if currency == 'SGD' else rates[currency]
        if not rate.is_finite() or rate <= 0:
            raise ValueError('Rates must be positive')
        part = Part(number, currency, payment, payment.cents,
                    _round(Decimal(payment.cents) / rate))
        grouped.setdefault((payment.from_unit, payment.to_unit[1]), []).append(part)
    lines = [Line(unit, person, sum(p.sgd_cents for p in parts), tuple(parts))
             for (unit, person), parts in grouped.items()]
    lines.sort(key=lambda line: line.parts[0].number)
    pairs = {}
    for line in lines:
        source = (unit_of(line.from_unit[1], families)
                  if line.from_unit[0] == 'person' else line.from_unit)
        target = unit_of(line.to_person, families)
        # Relatives only offset the exact opposite person, never another relative.
        a, b = ((line.from_unit, ('person', line.to_person)) if source == target
                else (source, target))
        key = tuple(sorted((a, b), key=lambda unit: (unit[0], str(unit[1]))))
        sides = pairs.setdefault(key, ([], []))
        sides[a != key[0]].append(line)
    result = []
    for key in sorted(pairs, key=str):
        sides = pairs[key]
        # An equal total keeps the side whose first payment appeared first.
        forward, reverse = sorted(sides, key=lambda side: (
            -sum(line.sgd_cents for line in side),
            min((line.parts[0].number for line in side), default=len(payments) + 1)))
        forward = sorted(forward, key=lambda line: (-line.sgd_cents, line.parts[0].number))
        attached = [[] for _ in forward]
        available = [line.sgd_cents for line in forward]
        index = 0
        for part in sorted((p for line in reverse for p in line.parts), key=lambda p: p.number):
            left_sgd, left_original = part.sgd_cents, part.cents_in_currency
            while left_sgd:
                while not available[index]:
                    index += 1
                piece = min(available[index], left_sgd)
                rate = Decimal(1) if part.currency == 'SGD' else rates[part.currency]
                original = (left_original if piece == left_sgd else
                            min(left_original, _round(Decimal(piece) * rate)))
                attached[index].append(replace(part, cents_in_currency=original, sgd_cents=piece,
                                               reverse=True, giver=forward[index].to_person))
                available[index] -= piece
                left_sgd -= piece
                left_original -= original
            if part.sgd_cents == 0:
                # Retain even sub-cent debts: the UI can refuse an unrepresentable save.
                attached[0].append(replace(part, reverse=True, giver=forward[0].to_person))
        result.extend(replace(line, sgd_cents=available[i], parts=line.parts + tuple(attached[i]))
                      for i, line in enumerate(forward))
    return _shortcut(_ordered(result), families, rates)


def _ordered(lines):
    return tuple(sorted(lines, key=lambda line: (-line.sgd_cents, line.parts[0].number)))


def _split(part, sgd, rates):
    """Cut `sgd` cents off a part; both pieces keep its bill cents and SGD cents exactly."""
    rate = Decimal(1) if part.currency == 'SGD' else rates[part.currency]
    original = min(part.cents_in_currency, _round(Decimal(sgd) * rate))
    return (replace(part, cents_in_currency=original, sgd_cents=sgd),
            replace(part, cents_in_currency=part.cents_in_currency - original,
                    sgd_cents=part.sgd_cents - sgd))


def _direct(line, part):
    """A debt the line's own payer owes its own receiver: no shortcut or netting behind it."""
    return part.giver is None and not part.reverse and part.payment.to_unit[1] == line.to_person


def _can_give(line, sgd):
    """All of the line, or no more than its direct debts (see _take)."""
    return sgd == line.sgd_cents or sgd <= sum(p.sgd_cents for p in line.parts if _direct(line, p))


def _take(line, sgd, rates):
    """Return (parts worth `sgd` of this line, the parts left on it).

    The whole line moves when all of it is taken, reverse parts and earlier shortcuts
    included, since its amount is already net of them. Otherwise only its direct debts are
    taken (_direct), SGD ones first so no rate rounding is needed: cutting a piece off an
    earlier shortcut would leave its pass-on money on the wrong card (Codex review, #171).
    _shortcut only asks for a part when the direct debts cover it.
    """
    if sgd == line.sgd_cents:
        return line.parts, ()
    order = sorted(range(len(line.parts)), key=lambda i: (
        not _direct(line, line.parts[i]), line.parts[i].currency != 'SGD', line.parts[i].number))
    taken, kept, left = [], {}, sgd
    for i in order:
        part = line.parts[i]
        if left and _direct(line, part):
            if part.sgd_cents <= left:
                taken.append(part)
                left -= part.sgd_cents
                continue
            piece, rest = _split(part, left, rates)
            taken.append(piece)
            kept[i] = rest
            left = 0
        else:
            kept[i] = part
    return tuple(taken), tuple(kept[i] for i in sorted(kept))


def _shortcut(lines, families, rates):
    """Move debts along lines that already exist, as /trip_bals does in each currency.

    "U pays p" and "p's unit pays c", when U already pays c, become "U pays c more" and
    both other lines that much less (ADR-0051, amended for #171). Nobody is ever sent to
    someone they didn't already pay. Here p's unit is p's family, so p's credit can
    clear a relative's debt to c: money may move between relatives, never between
    families, and each family's total is unchanged. The largest move goes first and
    each one removes at least one line, so this ends.
    """
    lines = list(lines)
    while True:
        at = {(line.from_unit, line.to_person): k for k, line in enumerate(lines)}
        moves = []
        for i, a in enumerate(lines):
            for j, b in enumerate(lines):
                if (i == j or a.sgd_cents <= 0 or b.sgd_cents <= 0 or b.to_person == a.to_person
                        or b.from_unit != debtor_unit(a.to_person, b.to_person, families)):
                    continue
                k = at.get((a.from_unit, b.to_person))
                x = min(a.sgd_cents, b.sgd_cents)
                # One of the two lines goes whole, so every move removes a line.
                if (k is not None and k not in (i, j) and lines[k].sgd_cents > 0
                        and _can_give(a, x) and _can_give(b, x)):
                    moves.append((-x, i, j, k))
        if not moves:
            return _ordered(lines)
        x, i, j, k = min(moves)
        x = -x
        a, b, c = lines[i], lines[j], lines[k]
        a_taken, a_kept = _take(a, x, rates)
        b_taken, b_kept = _take(b, x, rates)
        # The middle person hands b's money on: their own debt to c is cleared first.
        b_taken = tuple(replace(part, giver=a.to_person) if part.giver is None else part
                        for part in b_taken)
        notes = dict(((unit, person), cents) for unit, person, cents in c.shortcuts)
        notes[(b.from_unit, a.to_person)] = notes.get((b.from_unit, a.to_person), 0) + x
        lines[k] = replace(c, sgd_cents=c.sgd_cents + x, parts=c.parts + a_taken + b_taken,
                           shortcuts=tuple((u, p, cents) for (u, p), cents in notes.items()))
        lines[i] = replace(a, sgd_cents=a.sgd_cents - x, parts=a_kept)
        lines[j] = replace(b, sgd_cents=b.sgd_cents - x, parts=b_kept)
        lines = [line for line in lines if line.parts]
