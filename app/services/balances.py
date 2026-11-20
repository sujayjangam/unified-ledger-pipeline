"""Pure trip debt lines: whole cents, separate currencies, no FX or rounding.

Worked out per person first (ADR-0051): every share is a debt from the sharer to whoever
paid, even inside the payer's own family, and opposite debts between the same two people
net out. Paybacks then reduce those debts; any excess is owed back to whoever handed the
money over (ADR-0047). Only at the end are debts rolled up: a family's debts to someone
outside it become one line, while a debt inside a family stays person to person. So each
person's total is fixed by the bills and paybacks; rolling up only groups who hands money
to whom. Before roll-up, cancel person-level loops and shortcut only along existing
rolled debt lines, choosing the fewest payments within a bounded deterministic search.
"""

from dataclasses import dataclass, field

from app.add_expense import format_cents


@dataclass(frozen=True)
class Balances:
    """Remaining debt lines and each person's signed total.

    net:      {currency: {("person", id): cents}}. Plus = is owed, minus = owes. Always per
              person: a family's total is the sum of its members', added up for display.
    lines:    {currency: {(debtor unit, creditor person id): cents}}. The debtor is a family
              when the creditor is outside it, otherwise the person.
    no_payer: the split entries that had no payer recorded. They are left out of `net`, and
              handed back here so the caller can show a warning rather than lose them quietly.
    shortcuts: {currency: tuple of notes} explaining visible moves on the winning path;
               each note is (shown payer, creditor, middle unit, incoming unit, middle id, cents).
    """
    net: dict
    lines: dict
    no_payer: tuple = ()
    # Per currency: (shown payer, creditor, middle unit, incoming unit, middle person, cents).
    shortcuts: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Payment:
    """One repayment: e.g. family T pays person Bob 100 cents on a remaining line."""
    from_unit: object
    to_unit: object
    cents: int


def unit_balances(entries, families: dict, settlements=()) -> Balances:
    """Debts per person from the bills, less paybacks, simplified and rolled up to families.

    Entries contain currency, paid_by and splits ({person id: whole cents}).
    Settlements contain from_id, to_id, cents, currency and optional for_id and bills.
    Keep empty currencies to distinguish settled bills from no bills.
    """
    debts, no_payer, _ = _fold_debts(entries, families, settlements)
    net, lines, shortcuts = {}, {}, {}
    for currency, owed in debts.items():
        owed, notes = _simplify_debts(owed, families)
        if notes:
            shortcuts[currency] = notes
        people: dict = {}
        rolled: dict = {}
        for (debtor, creditor), cents in owed.items():
            people[debtor] = people.get(debtor, 0) - cents
            people[creditor] = people.get(creditor, 0) + cents
            key = (debtor_unit(debtor, creditor, families), creditor)
            rolled[key] = rolled.get(key, 0) + cents
        net[currency] = {("person", pid): cents for pid, cents in people.items() if cents}
        lines[currency] = rolled
    return Balances(net=net, lines=lines, no_payer=tuple(no_payer), shortcuts=shortcuts)


# How many debt states the search may look at, per currency. Measured 2026-11-19 on random
# 10-person trips (10-60 bills): 20,000 states took ~3.7 s and 1,000 ~0.15 s, while 200 took
# ~0.03 s and found within one or two payments of the 20,000 result. /trip_bals works this out
# two or three times per screen, so the bot must stay well under a second.
SIMPLIFY_BUDGET = 200


def _cancel_loops(owed: dict, on_cycle=None) -> dict:
    """Cancel cycles in sorted person/creditor DFS order, optionally reporting each amount."""
    owed = dict(owed)
    while True:
        adjacent = {}
        for debtor, creditor in sorted(owed):
            adjacent.setdefault(debtor, []).append(creditor)
        visited, active, path = set(), {}, []

        def cycle_from(person):
            """Return the first back-edge cycle from this person, or no cycle."""
            if person in active:
                loop = path[active[person]:] + [person]
                return list(zip(loop, loop[1:]))
            if person in visited:
                return None
            visited.add(person)
            active[person] = len(path)
            path.append(person)
            for creditor in adjacent.get(person, ()):
                cycle = cycle_from(creditor)
                if cycle:
                    return cycle
            path.pop()
            del active[person]
            return None

        cycle = None
        for person in sorted(adjacent):
            cycle = cycle_from(person)
            if cycle:
                break
        if not cycle:
            return owed
        amount = min(owed[edge] for edge in cycle)
        if on_cycle is not None:
            on_cycle(cycle, amount)
        for edge in cycle:
            owed[edge] -= amount
            if not owed[edge]:
                del owed[edge]


def _rolled_keys(owed: dict, families: dict) -> set:
    """Count payment lines without merging the underlying people's balances."""
    return {(debtor_unit(u, c, families), c) for u, c in owed}


def _shortcut_moves(owed: dict, families: dict) -> list:
    """Find full shortcuts whose destination already exists in the rolled report."""
    rolled = _rolled_keys(owed, families)
    moves = []
    for (p, c), outgoing in owed.items():
        for (u, receiver), incoming in owed.items():
            if receiver == p and u != c and (debtor_unit(u, c, families), c) in rolled:
                moves.append((min(incoming, outgoing), p, c, u))
    return sorted(moves, key=lambda move: (-move[0], *move[1:]))


def _simplify_path(owed: dict, families: dict) -> tuple:
    """Search loop-normalized states depth first; keep the first best payment count.

    Memoization ignores the path: revisiting identical debts cannot improve the result,
    and keeping the first path also preserves the specified deterministic tie-breaking.
    """
    best = _cancel_loops(owed)
    best_path, best_count = (), len(_rolled_keys(best, families))
    seen = set()
    # An explicit stack avoids depending on Python's recursion limit for shortcut paths.
    pending = [(best, ())]
    while pending and len(seen) < SIMPLIFY_BUDGET:
        state, path = pending.pop()
        state = _cancel_loops(state)
        key = frozenset(state.items())
        if key in seen:
            continue
        seen.add(key)
        count = len(_rolled_keys(state, families))
        if count < best_count:
            best, best_path, best_count = state, path, count
        # Reverse insertion makes the largest shortcut the next DFS branch visited.
        for x, p, c, u in reversed(_shortcut_moves(state, families)):
            child = dict(state)
            for edge in ((u, p), (p, c)):
                child[edge] -= x
                if not child[edge]:
                    del child[edge]
            # Keep both directions until loop cancellation, so its fixed order decides.
            child[(u, c)] = child.get((u, c), 0) + x
            pending.append((child, path + ((x, p, c, u),)))

    return best, best_path


def _simplify_debts(owed: dict, families: dict) -> tuple:
    """Return the winning debts and their visible shortcut notes, in the original order."""
    best, best_path = _simplify_path(owed, families)
    notes = {}
    for x, p, c, u in best_path:
        shown, middle = debtor_unit(u, c, families), debtor_unit(p, c, families)
        if shown == middle:
            continue  # Moving within the same displayed line needs no explanation.
        key = (shown, c, middle, debtor_unit(u, p, families), p)
        notes[key] = notes.get(key, 0) + x
    return best, tuple((*key, cents) for key, cents in
                       sorted(notes.items(), key=lambda item: (-item[1], item[0])))


def _take_pieces(pieces, cents):
    """Remove cents from the front, splitting only the last piece used."""
    taken = []
    while cents:
        path, amount = pieces.pop(0)
        used = min(cents, amount)
        taken.append((path, used))
        if used < amount:
            pieces.insert(0, (path, amount - used))
        cents -= used
    return taken


def _debt_pieces(owed: dict, families: dict) -> dict:
    """Replay the winning search path, retaining original edges behind each cent."""
    _, best_path = _simplify_path(owed, families)
    pieces = {edge: [(edge, cents)] for edge, cents in owed.items()}

    def drop_cycle(cycle, cents):
        for edge in cycle:
            _take_pieces(pieces[edge], cents)

    state = _cancel_loops(owed, drop_cycle)
    # After the first cancellation the used original edges are acyclic. Moves add no
    # original edge, so a concatenated path can never repeat a person.
    for x, p, c, u in best_path:
        incoming = _take_pieces(pieces[(u, p)], x)
        outgoing = _take_pieces(pieces[(p, c)], x)
        joined = pieces.setdefault((u, c), [])
        while incoming:
            left, a = incoming[0]
            right, b = outgoing[0]
            used = min(a, b)
            joined.append((left + right[1:], used))
            _take_pieces(incoming, used)
            _take_pieces(outgoing, used)
        for edge in ((u, p), (p, c)):
            state[edge] -= x
            if not state[edge]:
                del state[edge]
        state[(u, c)] = state.get((u, c), 0) + x
        state = _cancel_loops(state, drop_cycle)
    return {edge: tuple(pieces[edge]) for edge in state}


@dataclass(frozen=True)
class LinePart:
    """One member's cents and immutable bill rows for each hop to the receiver.

    Each row is (transaction_id, for_id, payer_id, cents), in oldest bill order.
    """
    member: int
    cents: int
    hops: tuple


def line_parts(entries, families, settlements, currency, owing_unit, creditor):
    """Describe a shortened line's bills, or None when ordinary payback rules apply."""
    entries = tuple(entries)
    debts, _, shares = _fold_debts(entries, families, settlements)
    pieces = _debt_pieces(debts.get(currency, {}), families)
    selected = [piece for (u, c), edge_parts in pieces.items()
                if c == creditor and debtor_unit(u, c, families) == owing_unit
                for piece in edge_parts]
    if not any(len(path) > 2 for path, _ in selected):
        return None
    parts = []
    for path, cents in selected:
        hops = []
        for a, b in zip(path, path[1:]):
            left, rows = cents, []
            for entry in entries:
                if entry.get('paid_by') != b or entry['currency'] != currency:
                    continue
                key = (entry.get('transaction_id'), a)
                used = min(left, shares.get(key, 0))
                if used:
                    rows.append((key[0], a, b, used))
                    shares[key] -= used
                    left -= used
                if not left:
                    break
            if left:
                # An owed-back overpayment has no bill: keep the rowless behaviour.
                return None
            hops.append(tuple(rows))
        parts.append(LinePart(path[0], cents, tuple(hops)))
    return tuple(parts)


def payment_rows(parts, first, clears_cents):
    """Take `first`'s pieces first, then larger member totals; offset every used hop.

    `first` is the giver, or the member named on a payment from outside the owing family
    (for_id), so a part payment clears the same person a line with no shortcut would.

    Within one member, longer chains go first. Erin owes Alice 60 directly and 40 through
    Bob; if a partial 80 paid her direct 60 first, the 20 left through Bob would have no
    Ngs-to-Alice line to ride on (shortcuts only use existing lines), and the report would
    split it into two new lines. Paying the chain first leaves her direct debt, so the rest
    stays one line. Whose debt is cleared is the same either way, so nobody's total moves.
    """
    totals = {}
    for part in parts:
        totals[part.member] = totals.get(part.member, 0) + part.cents
    ordered = sorted(parts, key=lambda part: (
        part.member != first, -totals[part.member], part.member, -len(part.hops)))
    rows = {}
    for part in ordered:
        used = min(clears_cents, part.cents)
        if not used:
            break
        for hop in part.hops:
            left = used
            for transaction_id, for_id, payer_id, cents in hop:
                amount = min(left, cents)
                key = (transaction_id, for_id)
                row = rows.setdefault(key, dict(transaction_id=transaction_id, for_id=for_id,
                                                payer_id=payer_id, cents=0))
                row['cents'] += amount
                left -= amount
                if not left:
                    break
        clears_cents -= used
    return list(rows.values())


def open_bill_shares(entries, families: dict, settlements=()) -> dict:
    """Positive cents still owed on each (transaction_id, person id), in entry order.

    Shares start gross: opposite debts never cancel at bill level. For the Tans (Alice,
    Bob), Lees (Carol, Dave), and Ngs (Erin, Frank), suppose Bob owes Alice 100 for lodging
    and Alice owes Bob 50 for a car. Bob's lodging share starts at 100 although his net
    line is 50. A row paying 100 on that lodging removes its 100 gross cents; only 50
    clears the net line, and the other 50 is owed back to the giver.

    Replay paybacks in the given order through the same netted debts as unit_balances.
    Eligible bill rows reduce only their own shares, at most to zero; missing bills or
    unbalanced offsets clear nothing. Balanced offsets clear middle people's shares
    without moving money, alongside payment of their own debts. An old payback without
    rows reduces the shares of each debtor _pay_back actually cleared, oldest receiver-paid
    bill first (the supplied entry order), in the payback's currency. A family payback
    thus uses the same beneficiary-first order as the balance report. By contrast, a bill
    row for Carol paying Alice on Erin's behalf clears only Erin's named share, never
    Dave's or Frank's.
    """
    _, _, shares = _fold_debts(entries, families, settlements)
    return {key: cents for key, cents in shares.items() if cents > 0}


def _fold_debts(entries, families: dict, settlements):
    """Replay debts and gross shares; accept balanced offsets together or drop them all."""
    # {currency: {(debtor id, creditor id): cents}}. Between any two people at most one
    # direction is ever stored, because _owe nets each new debt against the opposite one.
    debts: dict = {}
    no_payer = []
    payer_of, currency_of, shares = {}, {}, {}
    for entry in entries:
        if not entry.get("splits"):
            continue
        payer = entry.get("paid_by")
        if payer is None:
            no_payer.append(entry)
            continue
        owed = debts.setdefault(entry["currency"], {})
        transaction_id = entry.get('transaction_id')
        if transaction_id is not None:
            payer_of[transaction_id] = payer
            currency_of[transaction_id] = entry['currency']
        for sharer, cents in entry["splits"].items():
            # Your own share of a bill you paid is nothing owed. Every other share is owed
            # to the payer, whatever family the sharer is in.
            if sharer != payer:
                _owe(owed, sharer, payer, cents)
                if transaction_id is not None:
                    shares[(transaction_id, sharer)] = cents
    for settlement in settlements:
        owed = debts.setdefault(settlement["currency"], {})
        if settlement.get('bills'):
            # Each row is capped by what is still open on its own share, before it touches a
            # debt line. Without the cap, a second payback naming the same (already paid)
            # share would spill over onto the person's other bills to the same receiver,
            # instead of being owed back to the giver.
            allocations = []
            direct, offsets = _offset_bills(settlement, payer_of, currency_of, shares, owed)
            for row in offsets:
                payer = payer_of[row['transaction_id']]
                shares[(row['transaction_id'], row['for_id'])] -= row['cents']
                edge = (row['for_id'], payer)
                owed[edge] -= row['cents']
                if not owed[edge]:
                    del owed[edge]
            for row in direct:
                key = (row['transaction_id'], row['for_id'])
                capped = min(row['cents'], shares.get(key, 0))
                if capped:
                    shares[key] -= capped
                    allocations.append((row['for_id'], capped))
            _pay_back(owed, settlement, families, allocations)
        else:
            cleared = _pay_back(owed, settlement, families)
            for debtor, cents in cleared.items():
                for key in shares:
                    transaction_id, sharer = key
                    if (sharer == debtor and payer_of[transaction_id] == settlement['to_id']
                            and currency_of[transaction_id] == settlement['currency']):
                        used = min(cents, shares[key])
                        shares[key] -= used
                        cents -= used
                        if not cents:
                            break
    return debts, no_payer, shares


def _eligible_bills(settlement: dict, payer_of: dict, currency_of: dict):
    """Count bills in this currency; other payers need balanced offset validation."""
    for row in settlement['bills']:
        payer = payer_of.get(row['transaction_id'])
        if (payer is not None and payer != row['for_id']
                and currency_of.get(row['transaction_id']) == settlement['currency']):
            yield row


def _offset_bills(settlement, payer_of, currency_of, shares, owed):
    """Check offsets against the pre-payback state, before reducing any share or debt.

    Erin's taxi can be offset only as far as Bob's own debt is cleared on his behalf.
    Check both gross shares and net debts: a bill may still be open even though an
    opposite debt has already reduced what its sharer owes. Sum repeated shares and
    pairs before checking, so two rows cannot both spend the same available cents.
    """
    direct, offsets = [], []
    for row in _eligible_bills(settlement, payer_of, currency_of):
        target = direct if payer_of[row['transaction_id']] == settlement['to_id'] else offsets
        target.append(row)
    # A row whose bill is gone from the bills read (deleted) but was paid by someone other
    # than the receiver was an offset row, and its payer a middle person (ADR-0052). Without
    # this, the middle's own rows would read as a plain payment on their behalf and still
    # clear, and the giver would lose that money unseen. A broken chain fails as a whole.
    broken = {row['payer_id'] for row in settlement['bills']
              if row['transaction_id'] not in payer_of and row.get('payer_id') is not None
              and row['payer_id'] != settlement['to_id']}
    middles = {payer_of[row['transaction_id']] for row in offsets} | broken
    checked = offsets + [row for row in direct if row['for_id'] in middles]
    by_share, by_pair, incoming, outgoing = {}, {}, {}, {}
    for row in checked:
        key = (row['transaction_id'], row['for_id'])
        payer = payer_of[row['transaction_id']]
        pair = (row['for_id'], payer)
        by_share[key] = by_share.get(key, 0) + row['cents']
        by_pair[pair] = by_pair.get(pair, 0) + row['cents']
        incoming[payer] = incoming.get(payer, 0) + row['cents']
        outgoing[row['for_id']] = outgoing.get(row['for_id'], 0) + row['cents']
    valid = (not broken
             and all(cents <= shares.get(key, 0) for key, cents in by_share.items())
             and all(cents <= owed.get(pair, 0) for pair, cents in by_pair.items())
             and all(incoming.get(m, 0) <= outgoing.get(m, 0) for m in middles))
    if not valid:
        # Bob's dinner cannot be paid on his behalf if Erin's taxi offset is invalid.
        # Leave both open; unused money becomes owed back to the giver.
        return [row for row in direct if row['for_id'] not in middles], []
    return direct, offsets


def _owe(owed: dict, debtor: int, creditor: int, cents: int) -> None:
    """Add "debtor owes creditor cents", netted against anything owed the other way.

    Bob owes Alice 100 for the airbnb and Alice owes Bob 50 for the car: one debt, Bob owes
    Alice 50. Netting only between the same two people keeps each person's total unchanged.
    """
    left = owed.pop((debtor, creditor), 0) - owed.pop((creditor, debtor), 0) + cents
    if left > 0:
        owed[(debtor, creditor)] = left
    elif left < 0:
        owed[(creditor, debtor)] = -left


def _pay_back(owed: dict, settlement: dict, families: dict, allocations=None) -> dict:
    """Take one payback off the line it pays, person by person.

    The line is "the beneficiary's unit owes the receiver": the beneficiary is for_id on a
    payment made on someone's behalf (ADR-0047), otherwise whoever handed the money over.
    A family line is the sum of its members' debts to the receiver, so the payback clears
    the beneficiary's own debt first, then the other members', largest first, ties to the
    lowest id. That only decides whose name is under the family; the family total is the same.
    A payback with bill rows passes `allocations` instead: [(person id, cents)], already
    capped by each named share (see _fold_debts), and each clears only that person's line
    to the receiver. Anything left over is owed back to whoever handed the money over.
    Return {debtor id: cents cleared} so old paybacks use this same order when allocated
    to bills.
    """
    giver, receiver = settlement["from_id"], settlement["to_id"]
    if allocations is None:
        beneficiary = settlement.get("for_id")
        beneficiary = giver if beneficiary is None else beneficiary
        unit = debtor_unit(beneficiary, receiver, families)
        members = [debtor for debtor, creditor in owed
                   if creditor == receiver and debtor_unit(debtor, receiver, families) == unit]
        members.sort(key=lambda debtor: (debtor != beneficiary, -owed[(debtor, receiver)], debtor))
        allocations = [(debtor, settlement['cents']) for debtor in members]
    left = settlement["cents"]
    cleared_by = {}
    for debtor, cents in allocations:
        cleared = min(left, cents, owed.get((debtor, receiver), 0))
        if not cleared:
            continue
        owed[(debtor, receiver)] -= cleared
        if not owed[(debtor, receiver)]:
            del owed[(debtor, receiver)]
        left -= cleared
        cleared_by[debtor] = cleared_by.get(debtor, 0) + cleared
    if left:
        # A payment on someone's behalf still returns excess to whoever handed it over.
        _owe(owed, receiver, giver, left)
    return cleared_by


def debtor_unit(debtor: int, creditor: int, families: dict) -> tuple:
    """Who a debt is shown and paid as: the debtor's family when the creditor is outside it,
    otherwise the debtor in person. Rolled up where possible (ADR-0051).

    Lee's Carol owes Alice (a Tan): ("family", "Lee"). Bob owes Alice, both Tans:
    ("person", Bob), since a family can't owe its own member.
    """
    unit = unit_of(debtor, families)
    return unit if unit != unit_of(creditor, families) else ("person", debtor)


def unit_of(participant_id: int, families: dict) -> tuple:
    """The unit a person settles in: their family, or themselves when they have none.

    `families` maps participant id -> family label for one trip (`trip_members.family`).
    A unit is a small tuple so a family and a person can never be mixed up, even if a family
    were labelled "7":  ("family", "Tan")  or  ("person", 7).
    For example, Bob in T owes through ("family", "T"), but his credit uses his person
    key. Without a label both debt and credit use ("person", id), so they net naturally.
    """
    family = families.get(participant_id)
    return ("family", family) if family else ("person", participant_id)


# --- Words for people to read. ---

def trip_payments(entries, families: dict, settlements=()) -> list[tuple[str, Payment]]:
    """Number currencies alphabetically, then largest first within each one.

    Ties go by debtor (families before people, then label or id), then creditor id, so the
    numbers never depend on the order the bills were logged in.
    """
    result = unit_balances(entries, families, settlements)
    return [(currency, Payment(debtor, ("person", creditor), cents)) for currency in sorted(result.lines)
            for (debtor, creditor), cents in sorted(result.lines[currency].items(),
                key=lambda item: (-item[1], item[0][0][0], str(item[0][0][1]), item[0][1]))]

NO_SPLITS_LINE = "No split entries on this trip yet."
NO_PAYER_LINE = "⚠️ {count} split {entries} no payer recorded, so {they} not counted."


def _says(name: str, cents: int) -> str:
    """One balance in words rather than a plus or minus sign, so a forwarded message can't be
    read the wrong way round: "Tan family owes 240.00", "Erin is owed 25.00".
    Family credit summaries are for display; repayment still goes to the payer."""
    if cents > 0:
        return f"{name} is owed {format_cents(cents)}"
    if cents < 0:
        return f"{name} owes {format_cents(-cents)}"
    return f"{name} is settled"


def trip_balance_lines(entries, names: dict, families: dict, show_members: bool = True,
                       settlements=()) -> list[str]:
    """The balances of one trip as plain lines of text.

    entries:  what get_trip_split_entries returns (or any rows unit_balances can read)
    names:    {participant id: display name}
    families: {participant id: family label} for this trip

    For each currency: each family's total, with every member whose own total isn't zero
    underneath when requested, even for a family of one; then people with no family. A
    family can be settled while two of its members still owe each other. Payments name the
    payer: e.g. "T family pays Bob 100.00". Plain text with
    no Markdown, so a name with _ or * in it can't break the message, and it reads the same
    when forwarded to people who don't use the bot.

    show_members=False leaves out the people under each family: the shorter form, for when
    the full one is too long for one Telegram message.
    """
    def person(participant_id) -> str:
        """Name a payer or lone debtor, e.g. Bob, preserving unknown ids in the report."""
        # A sharer missing from the directory should never happen (a foreign key requires the
        # row), but a balance must not vanish because a name couldn't be looked up.
        return names.get(participant_id, f"Person #{participant_id}")

    def unit_name(unit) -> str:
        """Name a debt or credit unit: e.g. T family or the actual payer Bob."""
        kind, value = unit
        if kind == "person":
            return person(value)
        # A one-word label is a surname, so it reads better as "Tan family". A longer label
        # ("Alice and Bob", "Tan family") is already a phrase and is shown as stored.
        return f"{value} family" if len(value.split()) == 1 else value

    entries, settlements = tuple(entries), tuple(settlements)
    result = unit_balances(entries, families, settlements)
    numbered = list(enumerate(trip_payments(entries, families, settlements), 1))
    lines = []
    for currency in sorted(result.net):
        units = result.net[currency]
        display: dict = {}
        members: dict = {}
        # Totals are per person; a family's is the sum of its members', for display only.
        for unit, cents in units.items():
            shown = unit_of(unit[1], families)
            display[shown] = display.get(shown, 0) + cents
            # A family whose members only owe each other adds up to zero ("is settled") but
            # is still listed, with both members underneath.
            if shown[0] == "family":
                members.setdefault(shown, []).append(unit[1])
        if lines:
            lines.append("")
        lines.append(currency)
        # Families first, then people on their own; each group in name order.
        for unit in sorted(display, key=lambda unit: (unit[0], unit_name(unit).lower())):
            lines.append(_says(unit_name(unit), display[unit]))
            if show_members:
                for pid in sorted(members.get(unit, []), key=lambda pid: (person(pid).lower(), pid)):
                    lines.append("    " + _says(person(pid), units[("person", pid)]))
        payments = [(n, p) for n, (c, p) in numbered if c == currency]
        if payments:
            lines.append("To settle:")
            lines.extend(f"{n}. {unit_name(p.from_unit)} pays {unit_name(p.to_unit)} {format_cents(p.cents)}"
                         for n, p in payments)
        else:
            lines.append("Everyone is settled.")
        for shown, creditor, middle, incoming, p, cents in result.shortcuts.get(currency, ()):
            amount = format_cents(cents)
            payer = "" if incoming == shown else unit_name(incoming) + " "
            lines.append(f"💡 {unit_name(shown)} pays {person(creditor)} the {amount} "
                         f"{unit_name(middle)} owes, and {payer}pays {person(p)} {amount} less.")
        paid_back = [s for s in settlements if s["currency"] == currency]
        if paid_back:
            lines.append("Already paid back:")
            for s in paid_back:
                handed = (f" (as {format_cents(s['paid_cents'])} {s['paid_currency']})"
                          if s.get("paid_currency", currency) != currency else "")
                # Name whose line was paid: "on behalf of Bob" when Bob is the receiver's
                # relative (a person line), "on behalf of Lee family" otherwise. No
                # note when the giver pays a line they are part of.
                beneficiary = s.get('for_id')
                cleared = (debtor_unit(beneficiary, s['to_id'], families)
                           if beneficiary is not None else None)
                on_behalf = (f" on behalf of {unit_name(cleared)}"
                        if cleared is not None and
                        debtor_unit(s['from_id'], s['to_id'], families) != cleared else "")
                when = f", {s['date'].day} {s['date']:%b}" if s.get("date") else ""
                lines.append(f" • {person(s['from_id'])} paid {person(s['to_id'])} "
                             f"{format_cents(s['cents'])} {currency}{on_behalf}{handed}{when}")
    if not lines:
        lines.append(NO_SPLITS_LINE)
    if result.no_payer:
        count = len(result.no_payer)
        lines += ["", NO_PAYER_LINE.format(count=count, entries="entry has" if count == 1 else "entries have",
                                           they="it is" if count == 1 else "they are")]
    return lines
