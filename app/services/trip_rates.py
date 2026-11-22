"""Trip-level agreed rates, plus pure parsing and display in the prompt's direction."""

import re
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import text

from app.database import get_connection

# How far a typed rate may be from the ECB rate. Real card, Wise or money-changer rates sit
# within a few percent of it, while a rate typed the wrong way round is far outside even for
# currencies close to SGD (USD: 0.78 vs 1.28, 64% apart; AUD: 1.15 vs 0.87, 32% apart). Half
# to double, the first try, let a wrong-way-round USD rate through.
TYPED_LOW, TYPED_HIGH = Decimal('0.8'), Decimal('1.25')


def get_trip_rates(trip_id):
    """Return currency-keyed rows; None means a failed read, never an empty trip."""
    try:
        with get_connection() as conn:
            rows = conn.execute(text('''
                SELECT currency, rate, rate_date, source, set_by_participant_id, ecb_rate
                FROM trip_fx_rates WHERE trip_id = :trip_id AND paid_currency = 'SGD'
                ORDER BY currency
            '''), dict(trip_id=trip_id)).mappings().all()
        return {row['currency']: dict(row) for row in rows}
    except Exception as exc:
        print(f'Could not read trip rates: {type(exc).__name__}')
        return None


def upsert_trip_rate(trip_id, currency, rate, rate_date, source, set_by_participant_id=None):
    """Store an agreement; automatic inserts must not overwrite a concurrent typed rate.

    A looked-up rate is also kept as `ecb_rate`. A typed rate replaces `rate` but never
    `ecb_rate` (COALESCE keeps the old one), so the within-25% check in parse_rate
    still has the ECB rate to compare with on the second and later typed rates.
    """
    ecb_rate = rate if source == 'frankfurter' else None
    try:
        with get_connection() as conn:
            conn.execute(text('''
                INSERT INTO trip_fx_rates
                    (trip_id, paid_currency, currency, rate, ecb_rate, rate_date, source,
                     set_by_participant_id)
                VALUES (:trip_id, 'SGD', :currency, :rate, :ecb_rate, :rate_date, :source, :setter)
                ON CONFLICT (trip_id, paid_currency, currency) DO UPDATE
                SET rate = EXCLUDED.rate, rate_date = EXCLUDED.rate_date,
                    source = EXCLUDED.source, set_by_participant_id = EXCLUDED.set_by_participant_id,
                    ecb_rate = COALESCE(EXCLUDED.ecb_rate, trip_fx_rates.ecb_rate),
                    set_at = now()
                WHERE EXCLUDED.source = 'typed'
            '''), dict(trip_id=trip_id, currency=currency, rate=rate, ecb_rate=ecb_rate,
                       rate_date=rate_date, source=source, setter=set_by_participant_id))
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
    # Compare with the ECB rate kept on the row, even when someone has typed a rate since:
    # otherwise only the first typed rate would be checked. No ECB rate (the lookup
    # failed) means nothing to compare with, so any positive number is accepted.
    ecb = stored.get('ecb_rate') if stored else None
    if ecb and not (ecb * TYPED_LOW <= rate <= ecb * TYPED_HIGH):
        # Show the ECB rate the same way round as the prompt asked for it (1 USD = 1.2834
        # SGD, 1 SGD = 14,080 IDR), so a reply typed the wrong way round is easy to spot.
        raise ValueError(f"That's far from the ECB rate {rate_equation(currency, ecb)}. "
                         "Check which way round.")
    return rate
