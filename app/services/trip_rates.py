"""Trip-level agreed rates, plus pure parsing and display in the prompt's direction."""

import re
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import text

from app.database import get_connection


def get_trip_rates(trip_id):
    """Return currency-keyed rows; None means a failed read, never an empty trip."""
    try:
        with get_connection() as conn:
            rows = conn.execute(text('''
                SELECT currency, rate, rate_date, source, set_by_participant_id
                FROM trip_fx_rates WHERE trip_id = :trip_id AND paid_currency = 'SGD'
                ORDER BY currency
            '''), dict(trip_id=trip_id)).mappings().all()
        return {row['currency']: dict(row) for row in rows}
    except Exception as exc:
        print(f'Could not read trip rates: {type(exc).__name__}')
        return None


def upsert_trip_rate(trip_id, currency, rate, rate_date, source, set_by_participant_id=None):
    """Store an agreement; automatic inserts must not overwrite a concurrent typed rate."""
    try:
        with get_connection() as conn:
            conn.execute(text('''
                INSERT INTO trip_fx_rates
                    (trip_id, paid_currency, currency, rate, rate_date, source, set_by_participant_id)
                VALUES (:trip_id, 'SGD', :currency, :rate, :rate_date, :source, :setter)
                ON CONFLICT (trip_id, paid_currency, currency) DO UPDATE
                SET rate = EXCLUDED.rate, rate_date = EXCLUDED.rate_date,
                    source = EXCLUDED.source, set_by_participant_id = EXCLUDED.set_by_participant_id,
                    set_at = now()
                WHERE EXCLUDED.source = 'typed'
            '''), dict(trip_id=trip_id, currency=currency, rate=rate, rate_date=rate_date,
                       source=source, setter=set_by_participant_id))
            conn.commit()
        return True
    except Exception as exc:
        print(f'Could not store trip rate: {type(exc).__name__}')
        return False


def rate_equation(currency, rate):
    """Display a rate (not money), inverted for currencies worth more than SGD."""
    if rate >= 1:
        value = f'{rate:,.4f}'.rstrip('0').rstrip('.')
        return f'1 SGD = {value} {currency}'
    inverse = (Decimal(1) / rate).quantize(Decimal('.0001'), rounding=ROUND_HALF_UP)
    return f'1 {currency} = {inverse:.4f} SGD'


def rate_display(currency, row, names):
    """Keep provenance beside the number, using the stored date rather than today."""
    source = (f"ECB, {row['rate_date'].day} {row['rate_date']:%b}"
              if row['source'] == 'frankfurter' else
              f"typed by {names.get(row['set_by_participant_id'], 'unknown participant')}")
    return f"{rate_equation(currency, row['rate'])} ({source})"


def parse_rate(value, currency, prompt, stored=None):
    """Read only a positive decimal; the bot prompt determines the canonical direction."""
    if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?', value.strip()):
        raise ValueError('Reply with a positive number only, e.g. 14100.')
    rate = Decimal(value.strip().replace(',', ''))
    if rate <= 0:
        raise ValueError('The rate must be positive.')
    if f'how many SGD 1 {currency} cost' in prompt:
        rate = Decimal(1) / rate
    elif f'how many {currency} 1 SGD bought' not in prompt:
        raise ValueError('Open the rate prompt again.')
    if stored and stored['source'] == 'frankfurter' and not (
            stored['rate'] / 2 <= rate <= stored['rate'] * 2):
        # Show the ECB rate the same way round as the prompt asked for it (1 USD = 1.2834
        # SGD, 1 SGD = 14,080 IDR), so a reply typed the wrong way round is easy to spot.
        raise ValueError(f"That's far from the ECB rate {rate_equation(currency, stored['rate'])}. "
                         "Check which way round.")
    return rate
