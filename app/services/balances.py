"""Pure trip debt lines: whole cents, separate currencies, no FX or rounding.

Bills skip shares inside the payer's unit (ADR-0044). Outside shares create lines
from the sharer's unit to the person who paid. Paybacks reduce one line; excess
returns to the actual giver. Only opposite lines between two units cancel,
largest first, then lowest creditor id (ADR-0047). Never route through a third unit.
"""

from dataclasses import dataclass

from app.add_expense import format_cents


@dataclass(frozen=True)
class Balances:
    """Remaining debt lines and their signed display totals.

    net:      {currency: {unit: cents}}. Plus = is owed, minus = owes.
    lines:    {currency: {(debtor unit, creditor person id): cents}}.
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
    """Build bill lines, subtract paybacks, then cancel only between pairs of units.

    Entries contain currency, paid_by and splits ({person id: whole cents}).
    Settlements contain from_id, to_id, cents, currency and optional for_id.
    Keep empty currencies to distinguish settled bills from no bills.
    """
    by_currency: dict = {}
    no_payer = []
    for entry in entries:
        if not entry.get("splits"):
            continue
        payer = entry.get("paid_by")
        if payer is None:
            no_payer.append(entry)
            continue
        lines = by_currency.setdefault(entry["currency"], {})
        for sharer, cents in entry["splits"].items():
            debtor = unit_of(sharer, families)
            if debtor != unit_of(payer, families):
                key = (debtor, payer)
                lines[key] = lines.get(key, 0) + cents
    for settlement in settlements:
        payer, receiver = settlement["from_id"], settlement["to_id"]
        beneficiary = settlement.get("for_id")
        debtor = unit_of(payer if beneficiary is None else beneficiary, families)
        receiver_unit = unit_of(receiver, families)
        lines = by_currency.setdefault(settlement["currency"], {})
        if debtor == receiver_unit:
            continue
        remaining = lines.pop((debtor, receiver), 0) - settlement["cents"]
        if remaining > 0:
            lines[(debtor, receiver)] = remaining
        elif remaining < 0:
            # Even a gift's excess belongs to whoever actually handed it over.
            reverse = (receiver_unit, payer)
            lines[reverse] = lines.get(reverse, 0) - remaining
    net = {}
    for currency, lines in by_currency.items():
        pairs = {tuple(sorted((debtor, unit_of(creditor, families))))
                 for debtor, creditor in lines}
        for left, right in sorted(pairs):
            sides = [[key for key in lines if key[0] == debtor
                      and unit_of(key[1], families) == creditor]
                     for debtor, creditor in ((left, right), (right, left))]
            cancel = min(sum(lines[key] for key in side) for side in sides)
            for side in sides:
                remaining = cancel
                for key in sorted(side, key=lambda key: (-lines[key], key[1])):
                    reduction = min(remaining, lines[key])
                    lines[key] -= reduction
                    remaining -= reduction
        by_currency[currency] = {key: cents for key, cents in lines.items() if cents}
        units = {}
        for (debtor, creditor), cents in by_currency[currency].items():
            units[debtor] = units.get(debtor, 0) - cents
            person = ("person", creditor)
            units[person] = units.get(person, 0) + cents
        net[currency] = {unit: cents for unit, cents in units.items() if cents}
    return Balances(net=net, lines=by_currency, no_payer=tuple(no_payer))


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
    """Number currencies alphabetically, then use the same stable rule within each one."""
    result = unit_balances(entries, families, settlements)
    return [(currency, Payment(debtor, ("person", creditor), cents)) for currency in sorted(result.lines)
            for (debtor, creditor), cents in sorted(result.lines[currency].items(),
                key=lambda item: (-item[1], item[0][0], item[0][1]))]

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

    For each currency: family debts and family summaries of personal credits, with owed
    members underneath when requested, even for a family of one. Owing families have no
    member lines. Payments name the payer: e.g. "T family pays Bob 100.00". Plain text with
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
        # Group credits only for presentation: settlement must keep the payer's identity.
        for unit, cents in units.items():
            shown = unit_of(unit[1], families) if unit[0] == "person" else unit
            display[shown] = display.get(shown, 0) + cents
            if unit[0] == "person" and shown[0] == "family" and cents > 0:
                members.setdefault(shown, []).append(unit[1])
        if lines:
            lines.append("")
        lines.append(currency)
        # Families first, then people on their own; each group in name order.
        for unit in sorted(display, key=lambda unit: (unit[0], unit_name(unit).lower())):
            lines.append(_says(unit_name(unit), display[unit]))
            if show_members and display[unit] > 0:
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
                beneficiary = s.get('for_id')
                gift = (f" for {unit_name(unit_of(beneficiary, families))}"
                        if beneficiary is not None and
                        unit_of(beneficiary, families) != unit_of(s['from_id'], families) else "")
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
