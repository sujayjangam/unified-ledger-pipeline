"""Reference rates only: the request contains currency codes and a date, never ledger data."""

import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

import httpx

from app.services.currencies import ISO_4217

FX_TIMEOUT_SECONDS = 3.0
_CACHE_LIMIT = 64
_cache: dict[tuple[str, str, date], tuple[Decimal, date]] = {}


def rate_cached(paid: str, bills: str, on: date) -> bool:
    """Expose cache status for the bot's timing line without disclosing ledger data."""
    return (paid, bills, on) in _cache


async def reference_rate(paid: str, bills: str, on: date, *,
                         transport: httpx.AsyncBaseTransport | None = None) -> tuple[Decimal, date] | None:
    """Read a bounded, daily reference rate; failures are retryable and never cached.

    Transport injection keeps tests entirely offline. Decimal JSON parsing avoids
    introducing binary rounding before the whole-cent conversion.
    """
    try:
        if paid not in ISO_4217 or bills not in ISO_4217:
            raise ValueError("unknown currency")
        key = (paid, bills, on)
        if key in _cache:
            return _cache[key]
        async with httpx.AsyncClient(transport=transport, timeout=FX_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"https://api.frankfurter.dev/v1/{on:%Y-%m-%d}?base={paid}&symbols={bills}")
            response.raise_for_status()
        payload = json.loads(response.text, parse_float=Decimal)
        rate = Decimal(str(payload['rates'][bills]))
        rated_on = date.fromisoformat(payload['date'])
        if (payload['base'] != paid or payload['amount'] != 1 or rated_on > on
                or not rate.is_finite() or rate <= 0):
            raise ValueError("invalid rate response")
        _cache[key] = (rate, rated_on)
        while len(_cache) > _CACHE_LIMIT:
            del _cache[next(iter(_cache))]
        return _cache[key]
    except Exception as exc:
        # Do not print HTTP bodies or URLs: a short failure category is enough.
        print(f"Reference rate unavailable: {type(exc).__name__}")
        return None


def convert(paid_cents: int, rate: Decimal) -> int:
    """Convert positive whole cents, rounding a half cent up exactly once."""
    if type(paid_cents) is not int or paid_cents <= 0 or not rate.is_finite() or rate <= 0:
        raise ValueError("Conversion needs positive cents and rate")
    cents = int((Decimal(paid_cents) * rate).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if cents <= 0:
        raise ValueError("Converted amount must be positive")
    return cents
