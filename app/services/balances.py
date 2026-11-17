"""Who owes whom on a trip, worked out from the saved splits.

The rule: a family pays as one, but the money goes back to the PERSON who paid the bill,
never to their family. Three steps:

  1. unit_balances, bill by bill:
       - a share of someone in the payer's own family is skipped (the family sorts that
         out itself, and the payer's own share is theirs anyway);
       - every other share is owed by the sharer's family to the payer in person.
     Then a family that owes money has that debt taken out of its own members' credits
     first (largest credit first, ties to the lowest id), so a family never "pays" one of
     its own members.
  2. suggest_payments turns the balances into a short list of "X pays Y this much".
  3. trip_balance_lines writes it all as plain text.

Why bill by bill, and not by adding up each person first: family T is Alice, Bob and Carol.
Alice pays 300.00 for the three of them, then Bob pays 700.00 for those three and four
people from other families, 100.00 each. Bill by bill, the first bill moves nothing and the
four outsiders each owe Bob 100.00. Adding up per person first would leave Alice "owed"
100.00 by the family, and part of the outsiders' money could end up going to her, though
she paid for none of them.

A balance is in whole cents. PLUS means a person is owed money; MINUS means a family (or a
person with no family) owes money. Within one currency the balances always add up to zero:
every cent someone is owed is a cent somebody else owes.

Only adding and subtracting whole cents happens here, so nothing is ever rounded (ADR-0004).
Currencies are kept apart: there is no FX conversion.

Nothing in this module touches the database, the model or Telegram, so the tests can run
it on made-up rows.
"""

from dataclasses import dataclass

from app.add_expense import format_cents


@dataclass(frozen=True)
class Balances:
    """Per-bill debts and personal credits after family debt absorption.

    net:      {currency: {unit: cents}}. Plus = is owed, minus = owes.
    no_payer: the split entries that had no payer recorded. They are left out of `net`, and
              handed back here so the caller can show a warning rather than lose them quietly.
    """
    net: dict
    no_payer: tuple = ()


@dataclass(frozen=True)
class Payment:
    """One repayment: e.g. family T pays person Bob 100 cents after absorption."""
    from_unit: object
    to_unit: object
    cents: int


def unit_balances(entries, families: dict) -> Balances:
    """Debit outside sharers' units and credit each bill's payer, in whole cents.

    Each entry is a dict with:
      currency: "SGD", "AUD"...
      paid_by:  the participant id of who paid, or None when it wasn't recorded
      splits:   {participant id: share in cents}, the rows of `transaction_splits`

    Shares within the payer's unit cancel on that bill, before totals are made.
    A family's debt consumes its members' credits, largest first, ties to lowest id.
    For example, Bob in T is owed 400.00 and T owes 150.00 elsewhere: Bob keeps
    250.00 credit and T owes nothing. Unlabelled people net on their personal key.

    An entry with no splits is the payer's alone ("not split"), so it moves nothing.
    """
    net: dict = {}
    no_payer = []
    for entry in entries:
        splits = entry.get("splits") or {}
        if not splits:
            continue
        payer = entry.get("paid_by")
        if payer is None:
            # Without a payer there is nobody to owe the shares to. Never guess one here.
            no_payer.append(entry)
            continue
        units = net.setdefault(entry["currency"], {})
        payer_unit = unit_of(payer, families)
        credit = ("person", payer)
        for participant_id, share_cents in splits.items():
            debtor = unit_of(participant_id, families)
            if debtor == payer_unit:
                # Same family as the payer: nothing to settle with the rest of the group.
                continue
            units[debtor] = units.get(debtor, 0) - share_cents
            units[credit] = units.get(credit, 0) + share_cents
    for currency, units in net.items():
        for family in [unit for unit in units if unit[0] == "family" and units[unit] < 0]:
            members = [unit for unit in units if unit[0] == "person" and units[unit] > 0
                       and unit_of(unit[1], families) == family]
            # The family's debt comes out of what its own members are owed first, so the
            # report never says "T family pays Bob" when Bob is in T.
            for member in sorted(members, key=lambda unit: (-units[unit], unit[1])):
                absorbed = min(-units[family], units[member])
                units[family] += absorbed
                units[member] -= absorbed
                if units[family] == 0:
                    break
        # Keep empty currencies to distinguish a settled split trip from an unsplit trip.
        net[currency] = {unit: cents for unit, cents in units.items() if cents}
    return Balances(net=net, no_payer=tuple(no_payer))


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


def suggest_payments(net: dict) -> list[Payment]:
    """A short list of payments that brings every balance in `net` to zero.

    `net` is {unit: cents} for ONE currency from unit_balances after debt absorption.
    The rule: whoever owes the most pays whoever is owed the most, as much as
    both sides allow; repeat until nobody owes anything. Each payment clears at least one
    side completely, so there are never more payments than (number of units - 1).

    That is a simple, predictable rule, not a guarantee of the very fewest payments possible;
    for a group of this size the difference is at most a payment or so (ROADMAP, step 4).

    For example, T owing 100 cents pays Bob personally when Bob is owed 100 cents.
    Ties go to the unit that sorts first, so the same balances always give the same list.
    Raises ValueError when the balances don't add up to zero: then some money is unaccounted
    for, and any list of payments would be wrong.
    """
    if sum(net.values()) != 0:
        raise ValueError("Balances must add up to zero before payments can be suggested")
    # Two piles, both as positive cents: what each debtor still owes, what each creditor is
    # still owed. Units at exactly zero are already settled and are in neither.
    owes = {unit: -cents for unit, cents in net.items() if cents < 0}
    owed = {unit: cents for unit, cents in net.items() if cents > 0}
    payments = []
    while owes:
        # min() with a minus sign picks the LARGEST amount; the unit itself breaks a tie.
        debtor = min(owes, key=lambda unit: (-owes[unit], unit))
        creditor = min(owed, key=lambda unit: (-owed[unit], unit))
        cents = min(owes[debtor], owed[creditor])
        payments.append(Payment(from_unit=debtor, to_unit=creditor, cents=cents))
        owes[debtor] -= cents
        owed[creditor] -= cents
        if owes[debtor] == 0:
            del owes[debtor]
        if owed[creditor] == 0:
            del owed[creditor]
    return payments


# --- Words for people to read. ---

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


def trip_balance_lines(entries, names: dict, families: dict, show_members: bool = True) -> list[str]:
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

    result = unit_balances(entries, families)
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
        payments = suggest_payments(units)
        if payments:
            lines.append("To settle:")
            lines.extend(f" • {unit_name(p.from_unit)} pays {unit_name(p.to_unit)} {format_cents(p.cents)}"
                         for p in payments)
        else:
            lines.append("Everyone is settled.")
    if not lines:
        lines.append(NO_SPLITS_LINE)
    if result.no_payer:
        count = len(result.no_payer)
        lines += ["", NO_PAYER_LINE.format(count=count, entries="entry has" if count == 1 else "entries have",
                                           they="it is" if count == 1 else "they are")]
    return lines
