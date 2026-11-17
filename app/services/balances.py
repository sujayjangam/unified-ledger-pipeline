"""Who owes whom on a trip, worked out from the saved splits.

Three steps, each a small function that takes plain data and returns plain data:

  1. net_balances      entries           -> one number per person, per currency
  2. by_family         per-person numbers -> one number per family (the unit that settles)
  3. suggest_payments  per-unit numbers   -> a short list of "X pays Y this much"

A balance is in whole cents. PLUS means the person is owed money (they paid more than their
share); MINUS means they owe money. Within one currency the balances always add up to zero:
every cent one person is owed is a cent somebody else owes.

Only adding and subtracting whole cents happens here, so nothing is ever rounded (ADR-0004).
Currencies are kept apart: there is no FX conversion.

Nothing in this module touches the database, the model or Telegram, so the tests can run
it on made-up rows.
"""

from dataclasses import dataclass

from app.add_expense import format_cents


@dataclass(frozen=True)
class Balances:
    """What net_balances returns.

    net:      {currency: {participant id: cents}}. Plus = is owed, minus = owes.
    no_payer: the split entries that had no payer recorded. They are left out of `net`, and
              handed back here so the caller can show a warning rather than lose them quietly.
    """
    net: dict
    no_payer: tuple = ()


@dataclass(frozen=True)
class Payment:
    """One suggested payment: `from_unit` pays `to_unit` this many cents."""
    from_unit: object
    to_unit: object
    cents: int


def net_balances(entries) -> Balances:
    """Adds up every entry into one balance per person, per currency.

    Each entry is a dict with:
      currency: "SGD", "AUD"...
      paid_by:  the participant id of who paid, or None when it wasn't recorded
      splits:   {participant id: share in cents}, the rows of `transaction_splits`

    For one entry, the payer has put in the whole bill for the group, so they are owed every
    share (plus). Each sharer has used up their share (minus). When the payer is also a sharer
    the two cancel for their own share, which is right: nobody owes themselves.

      Dinner 90.00, Alice paid, split Alice / Bob / Carol at 30.00 each:
        Alice  +90.00 - 30.00 = +60.00   (is owed 60)
        Bob             -30.00           (owes 30)
        Carol           -30.00           (owes 30)

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
        per_person = net.setdefault(entry["currency"], {})
        for participant_id, share_cents in splits.items():
            per_person[participant_id] = per_person.get(participant_id, 0) - share_cents
        per_person[payer] = per_person.get(payer, 0) + sum(splits.values())
    return Balances(net=net, no_payer=tuple(no_payer))


def unit_of(participant_id: int, families: dict) -> tuple:
    """The unit a person settles in: their family, or themselves when they have none.

    `families` maps participant id -> family label for one trip (`trip_members.family`).
    A unit is a small tuple so a family and a person can never be mixed up, even if a family
    were labelled "7":  ("family", "Tan")  or  ("person", 7).
    Someone who settles alone is simply given a family label nobody else has.
    """
    family = families.get(participant_id)
    return ("family", family) if family else ("person", participant_id)


def by_family(net: dict, families: dict) -> dict:
    """Rolls one currency's per-person balances up into one balance per unit.

    `net` is {participant id: cents} (one currency out of Balances.net). A family's balance is
    its members' balances added together, so money owed between two members of the same family
    cancels out and never shows up as a payment.
    """
    units: dict = {}
    for participant_id, cents in net.items():
        unit = unit_of(participant_id, families)
        units[unit] = units.get(unit, 0) + cents
    return units


def suggest_payments(net: dict) -> list[Payment]:
    """A short list of payments that brings every balance in `net` to zero.

    `net` is {unit: cents} for ONE currency: the result of by_family, or per-person balances
    as they are. The rule: whoever owes the most pays whoever is owed the most, as much as
    both sides allow; repeat until nobody owes anything. Each payment clears at least one
    side completely, so there are never more payments than (number of units - 1).

    That is a simple, predictable rule, not a guarantee of the very fewest payments possible;
    for a group of this size the difference is at most a payment or so (ROADMAP, step 4).

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
    read the wrong way round: "Tan family is owed 240.00", "Erin owes 25.00"."""
    if cents > 0:
        return f"{name} is owed {format_cents(cents)}"
    if cents < 0:
        return f"{name} owes {format_cents(-cents)}"
    return f"{name} is settled"


def trip_balance_lines(entries, names: dict, families: dict, show_members: bool = True) -> list[str]:
    """The balances of one trip as plain lines of text.

    entries:  what get_trip_split_entries returns (or any rows net_balances can read)
    names:    {participant id: display name}
    families: {participant id: family label} for this trip

    For each currency: every unit's balance (a family, with its members underneath when it
    has more than one, or a person alone), then the payments that settle it. Plain text with
    no Markdown, so a name with _ or * in it can't break the message, and it reads the same
    when forwarded to people who don't use the bot.

    show_members=False leaves out the people under each family: the shorter form, for when
    the full one is too long for one Telegram message.
    """
    def person(participant_id) -> str:
        # A sharer missing from the directory should never happen (a foreign key requires the
        # row), but a balance must not vanish because a name couldn't be looked up.
        return names.get(participant_id, f"Person #{participant_id}")

    def unit_name(unit) -> str:
        kind, value = unit
        if kind == "person":
            return person(value)
        # A one-word label is a surname, so it reads better as "Tan family". A longer label
        # ("Alice and Bob", "Tan family") is already a phrase and is shown as stored.
        return f"{value} family" if len(value.split()) == 1 else value

    result = net_balances(entries)
    lines = []
    for currency in sorted(result.net):
        per_person = result.net[currency]
        units = by_family(per_person, families)
        if lines:
            lines.append("")
        lines.append(currency)
        # Families first, then people on their own; each group in name order.
        for unit in sorted(units, key=lambda unit: (unit[0], unit_name(unit).lower())):
            lines.append(_says(unit_name(unit), units[unit]))
            members = [pid for pid in per_person if unit_of(pid, families) == unit]
            if show_members and unit[0] == "family" and len(members) > 1:
                for pid in sorted(members, key=lambda pid: person(pid).lower()):
                    lines.append("    " + _says(person(pid), per_person[pid]))
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
