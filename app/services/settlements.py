"""Paybacks: validate money, write one record, and build plain-text cards."""
import re

from sqlalchemy import text

from app.add_expense import dollars_to_cents, format_cents
from app.database import get_connection


def add_settlement(*, from_id, to_id, clears_amount, clears_currency, paid_amount,
                   paid_currency, date, source, idempotency_key, trip_id=None,
                   entered_by_participant_id=None, note=None, for_id=None) -> bool:
    """Insert once, returning False for a retry already saved; database errors propagate.

    Callers supply decimal amounts, as text where possible. Both pass through the same
    cents conversion as expenses, so the loader and the bot cannot round differently.
    """
    clears_cents = dollars_to_cents(clears_amount)
    paid_cents = dollars_to_cents(paid_amount)
    if from_id == to_id or for_id == to_id:
        raise ValueError("A payback needs two different people")
    if clears_currency == paid_currency and clears_cents != paid_cents:
        raise ValueError("Amounts in the same currency must match")
    params = dict(for_id=for_id, trip_id=trip_id, from_id=from_id, to_id=to_id, clears_cents=clears_cents,
                  clears_currency=clears_currency, paid_cents=paid_cents,
                  paid_currency=paid_currency, date=date, source=source, note=note,
                  entered_by_participant_id=entered_by_participant_id, idempotency_key=idempotency_key)
    with get_connection() as conn:
        result = conn.execute(text('''
            INSERT INTO settlements (
                trip_id, from_participant_id, to_participant_id, for_participant_id, clears_cents, clears_currency,
                paid_cents, paid_currency, date, source, note, entered_by_participant_id, idempotency_key
            ) VALUES (
                :trip_id, :from_id, :to_id, :for_id, :clears_cents, :clears_currency,
                :paid_cents, :paid_currency, :date, :source, :note, :entered_by_participant_id, :idempotency_key
            ) ON CONFLICT (idempotency_key) DO NOTHING
        '''), params)
        inserted = result.rowcount == 1
        conn.commit()
    return inserted


def is_overpayment(clears_cents: int, suggested_cents: int) -> bool:
    """The warning compares the debt cleared, never the other currency handed over."""
    return clears_cents > suggested_cents


def settlement_card_text(card: dict) -> str:
    """Show both agreed amounts, the people, and an explicit warning for an overpayment."""
    payer = card['names'].get(card['from_id'], "Choose who paid")
    family = card.get('family')
    from_line = payer
    if card.get('for_id') is not None:
        label = (f"{family} family" if len(family.split()) == 1 else family) if family else card['names'][card['for_id']]
        from_line += f", paying for {label} (a gift: nobody will owe {payer})"
    elif family:
        label = f"{family} family" if len(family.split()) == 1 else family
        from_line += f" ({label})"
    lines = ["💸 Payback", f"Trip: {card['trip_name']}", f"From: {from_line}",
             f"To: {card['names'][card['to_id']]}",
             f"Clears: {format_cents(card['clears_cents'])} {card['clears_currency']}",
             f"Paid: {format_cents(card['paid_cents'])} {card['paid_currency']}",
             f"Date: {card['date'].day} {card['date']:%b}"]
    if is_overpayment(card['clears_cents'], card['suggested_cents']):
        lines.append(f"⚠️ This is more than the {format_cents(card['suggested_cents'])} "
                     f"{card['clears_currency']} owed. The extra will be owed back to {payer}.")
    return "\n".join(lines)


def parse_settlement_amount(value: str, default_currency: str, currencies) -> tuple[int, str]:
    """Read only a number and optional currency; never send a payback reply to the model."""
    # Rupiah amounts are big, so people type "600,000". Commas are only thousands
    # separators here (never a decimal point), so dropping them is safe.
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([A-Za-z]{3})?\s*", value.replace(",", ""))
    if not match:
        raise ValueError("Use an amount such as 250000 or 50 SGD")
    currency = (match[2] or default_currency).upper()
    if currency not in currencies:
        raise ValueError("Use a currency already on this trip, or SGD")
    return dollars_to_cents(match[1]), currency


def change_settlement_amount(card: dict, value: str, side: str) -> dict:
    """Return a changed copy, keeping same-currency sides equal without guessing an FX rate."""
    cents, currency = parse_settlement_amount(value, card[f'{side}_currency'], card['currencies'])
    changed = dict(card)
    if side == "clears":
        # A suggestion is denominated in one debt currency. Changing that would clear a
        # different debt and make the overpayment warning meaningless.
        if currency != card['clears_currency']:
            raise ValueError(f"Use {card['clears_currency']} for the amount it clears")
        changed['clears_cents'] = cents
        if card['paid_currency'] == currency:
            changed['paid_cents'] = cents
    else:
        changed.update(paid_cents=cents, paid_currency=currency)
        if currency == card['clears_currency']:
            changed['clears_cents'] = cents
    return changed
