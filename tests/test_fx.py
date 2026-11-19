"""Reference-rate traffic is always intercepted by MockTransport."""
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest

from app.services import fx

ON = date(2026, 11, 14)
BODY = '{"amount":1.0,"base":"SGD","date":"2026-11-13","rates":{"INR":73.167}}'


@pytest.fixture(autouse=True)
def empty_cache():
    fx._cache.clear()
    yield
    fx._cache.clear()


async def test_rate_decimal_weekend_and_cache():
    requests = []

    def respond(request):
        requests.append(request)
        assert str(request.url) == 'https://api.frankfurter.dev/v1/2026-11-14?base=SGD&symbols=INR'
        assert request.extensions['timeout']['read'] == 3.0
        return httpx.Response(200, text=BODY)

    transport = httpx.MockTransport(respond)
    assert not fx.rate_cached('SGD', 'INR', ON)
    for _ in range(2):
        assert await fx.reference_rate('SGD', 'INR', ON, transport=transport) == (Decimal('73.167'), date(2026, 11, 13))
    assert len(requests) == 1
    assert fx.rate_cached('SGD', 'INR', ON)


@pytest.mark.parametrize('body,status', [
    (BODY, 503), ('not json', 200), ('{}', 200),
    (BODY.replace('INR', 'AUD'), 200), (BODY.replace('73.167', '-1'), 200),
    (BODY.replace('73.167', 'NaN'), 200), (BODY.replace('2026-11-13', '2026-11-15'), 200),
    (BODY.replace('"SGD"', '"AUD"'), 200), (BODY.replace('1.0', '2.0'), 200),
])
async def test_bad_responses_are_not_cached(body, status):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text=body))
    assert await fx.reference_rate('SGD', 'INR', ON, transport=transport) is None
    assert not fx._cache


async def test_timeout_then_success():
    def timeout(request):
        raise httpx.ReadTimeout('offline timeout')
    assert await fx.reference_rate('SGD', 'INR', ON, transport=httpx.MockTransport(timeout)) is None
    assert await fx.reference_rate('SGD', 'INR', ON, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text=BODY))) is not None


async def test_cache_is_bounded():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=BODY))
    for i in range(65):
        await fx.reference_rate('SGD', 'INR', ON + timedelta(days=i), transport=transport)
    assert len(fx._cache) == 64
    assert not fx.rate_cached('SGD', 'INR', ON)


def test_conversion_half_up_and_positive():
    assert fx.convert(10000, Decimal('64.52')) == 645200
    assert fx.convert(1, Decimal('1.5')) == 2
    for cents, rate in [(0, '1'), (-1, '1'), (1, '0'), (1, 'NaN'), (1, '.001')]:
        with pytest.raises(ValueError):
            fx.convert(cents, Decimal(rate))
