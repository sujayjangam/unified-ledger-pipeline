"""Paybacks: validate money, write one record, and build plain-text cards."""
import re

from sqlalchemy import text

from app.add_expense import dollars_to_cents, format_cents
from app.database import get_connection
from app.services.currencies import ISO_4217


def add_settlement(*, from_id, to_id, clears_amount, clears_currency, paid_amount,
                   paid_currency, date, source, idempotency_key, trip_id=None,
                   entered_by_participant_id=None, note=None, for_id=None, bills=()) -> bool:
    """Insert once, returning False for a retry already saved; database errors propagate.

    Callers supply decimal amounts, as text where possible. Both pass through the same
    cents conversion as expenses, so the loader and the bot cannot round differently.
    Bill-row cents are whole cents computed from stored shares, never typed money, so
    they do not pass through dollars_to_cents. Any amount beyond the rows is an overpayment.
    """
    clears_cents = dollars_to_cents(clears_amount)
    paid_cents = dollars_to_cents(paid_amount)
    if from_id == to_id or for_id == to_id:
        raise ValueError("A payback needs two different people")
    if clears_currency == paid_currency and clears_cents != paid_cents:
        raise ValueError("Amounts in the same currency must match")
    bills = tuple(bills)
    seen = set()
    for bill in bills:
        if not isinstance(bill['cents'], int) or isinstance(bill['cents'], bool) or bill['cents'] <= 0:
            raise ValueError("Bill cents must be positive whole cents")
        key = (bill['transaction_id'], bill['for_id'])
        if key in seen:
            raise ValueError("A bill share cannot appear twice")
        seen.add(key)
    if sum(bill['cents'] for bill in bills) > clears_cents:
        raise ValueError("Bill shares exceed the amount cleared")
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
            ) ON CONFLICT (idempotency_key) DO NOTHING RETURNING id
        '''), params)
        inserted = result.rowcount == 1
        if inserted and bills:
            settlement_id = result.scalar_one()
            # A saved retry must not attach rows again; both inserts commit together.
            conn.execute(text('''
                INSERT INTO settlement_bills (settlement_id, transaction_id, for_participant_id, cents)
                VALUES (:settlement_id, :transaction_id, :for_id, :cents)
            '''), [dict(settlement_id=settlement_id, transaction_id=bill['transaction_id'],
                       for_id=bill['for_id'], cents=bill['cents']) for bill in bills])
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
        from_line += f", on behalf of {label}"
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
    # Any real currency is fine (INR, IDR, ...). A code already used on the trip is always
    # accepted too, so an unusual one in the bills can never be refused here.
    if currency not in ISO_4217 and currency not in currencies:
        raise ValueError(f"{currency} isn't a currency code. Use one like SGD, IDR or INR.")
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
