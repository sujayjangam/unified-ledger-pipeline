"""Convert payment suggestions without changing the underlying per-currency debts."""

from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_HALF_UP

from app.services.balances import Payment, unit_of


@dataclass(frozen=True)
class Part:
    """A whole payment or a conserved piece of a reverse payment, in both currencies."""
    number: int
    currency: str
    payment: Payment
    cents_in_currency: int
    sgd_cents: int
    reverse: bool = False


@dataclass(frozen=True)
class Line:
    """One suggested transfer, with all debts it clears retained for Confirm."""
    from_unit: tuple
    to_person: int
    sgd_cents: int
    parts: tuple[Part, ...]


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
                attached[index].append(replace(part, cents_in_currency=original,
                                               sgd_cents=piece, reverse=True))
                available[index] -= piece
                left_sgd -= piece
                left_original -= original
            if part.sgd_cents == 0:
                # Retain even sub-cent debts: the UI can refuse an unrepresentable save.
                attached[0].append(replace(part, reverse=True))
        result.extend(replace(line, sgd_cents=available[i], parts=line.parts + tuple(attached[i]))
                      for i, line in enumerate(forward))
    return tuple(sorted(result, key=lambda line: (-line.sgd_cents, line.parts[0].number)))
