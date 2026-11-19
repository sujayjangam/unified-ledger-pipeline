"""Pure trip debt lines: whole cents, separate currencies, no FX or rounding.

Worked out per person first (ADR-0051): every share is a debt from the sharer to whoever
paid, even inside the payer's own family, and opposite debts between the same two people
net out. Paybacks then reduce those debts; any excess is owed back to whoever handed the
money over (ADR-0047). Only at the end are debts rolled up: a family's debts to someone
outside it become one line, while a debt inside a family stays person to person. So each
person's total is fixed by the bills and paybacks; rolling up only groups who hands money
to whom. Never route a debt through a third person (simplifying is #129).
"""

from dataclasses import dataclass

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
    """
    net: dict
    lines: dict
    no_payer: tuple = ()


@dataclass(frozen=True)
class Payment:
    """One repayment: e.g. family T pays person Bob 100 cents on a remaining line."""
    from_unit: object
    to_unit: object
    cents: int


def unit_balances(entries, families: dict, settlements=()) -> Balances:
    """Debts per person from the bills, less paybacks, then rolled up to families.

    Entries contain currency, paid_by and splits ({person id: whole cents}).
    Settlements contain from_id, to_id, cents, currency and optional for_id.
    Keep empty currencies to distinguish settled bills from no bills.
    """
    # {currency: {(debtor id, creditor id): cents}}. Between any two people at most one
    # direction is ever stored, because _owe nets each new debt against the opposite one.
    debts: dict = {}
    no_payer = []
    for entry in entries:
        if not entry.get("splits"):
            continue
        payer = entry.get("paid_by")
        if payer is None:
            no_payer.append(entry)
            continue
        owed = debts.setdefault(entry["currency"], {})
        for sharer, cents in entry["splits"].items():
            # Your own share of a bill you paid is nothing owed. Every other share is owed
            # to the payer, whatever family the sharer is in.
            if sharer != payer:
                _owe(owed, sharer, payer, cents)
    for settlement in settlements:
        _pay_back(debts.setdefault(settlement["currency"], {}), settlement, families)
    net, lines = {}, {}
    for currency, owed in debts.items():
        people: dict = {}
        rolled: dict = {}
        for (debtor, creditor), cents in owed.items():
            people[debtor] = people.get(debtor, 0) - cents
            people[creditor] = people.get(creditor, 0) + cents
            key = (debtor_unit(debtor, creditor, families), creditor)
            rolled[key] = rolled.get(key, 0) + cents
        net[currency] = {("person", pid): cents for pid, cents in people.items() if cents}
        lines[currency] = rolled
    return Balances(net=net, lines=lines, no_payer=tuple(no_payer))


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


def _pay_back(owed: dict, settlement: dict, families: dict) -> None:
    """Take one payback off the line it pays, person by person.

    The line is "the beneficiary's unit owes the receiver": the beneficiary is for_id on a
    payment made on someone's behalf (ADR-0047), otherwise whoever handed the money over.
    A family line is the sum of its members' debts to the receiver, so the payback clears
    the beneficiary's own debt first, then the other members', largest first, ties to the
    lowest id. That only decides whose name is under the family; the family total is the same.
    Anything left over is owed back to whoever handed the money over.
    """
    giver, receiver = settlement["from_id"], settlement["to_id"]
    beneficiary = settlement.get("for_id")
    beneficiary = giver if beneficiary is None else beneficiary
    unit = debtor_unit(beneficiary, receiver, families)
    members = [debtor for debtor, creditor in owed
               if creditor == receiver and debtor_unit(debtor, receiver, families) == unit]
    members.sort(key=lambda debtor: (debtor != beneficiary, -owed[(debtor, receiver)], debtor))
    left = settlement["cents"]
    for debtor in members:
        cleared = min(left, owed[(debtor, receiver)])
        owed[(debtor, receiver)] -= cleared
        if not owed[(debtor, receiver)]:
            del owed[(debtor, receiver)]
        left -= cleared
    if left:
        # Even a gift's excess belongs to whoever actually handed it over.
        _owe(owed, receiver, giver, left)


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
        paid_back = [s for s in settlements if s["currency"] == currency]
        if paid_back:
            lines.append("Already paid back:")
            for s in paid_back:
                handed = (f" (as {format_cents(s['paid_cents'])} {s['paid_currency']})"
                          if s.get("paid_currency", currency) != currency else "")
                # Name the line the payment cleared, as the receiver sees it: "for Bob" when Bob
                # is the receiver's relative (a person line), "for Lee family" otherwise. No
                # note when the giver pays a line they are part of.
                beneficiary = s.get('for_id')
                cleared = (debtor_unit(beneficiary, s['to_id'], families)
                           if beneficiary is not None else None)
                gift = (f" for {unit_name(cleared)}"
                        if cleared is not None and
                        debtor_unit(s['from_id'], s['to_id'], families) != cleared else "")
                when = f", {s['date'].day} {s['date']:%b}" if s.get("date") else ""
                lines.append(f" • {person(s['from_id'])} paid {person(s['to_id'])} "
                             f"{format_cents(s['cents'])} {currency}{gift}{handed}{when}")
    if not lines:
        lines.append(NO_SPLITS_LINE)
    if result.no_payer:
        count = len(result.no_payer)
        lines += ["", NO_PAYER_LINE.format(count=count, entries="entry has" if count == 1 else "entries have",
                                           they="it is" if count == 1 else "they are")]
    return lines
