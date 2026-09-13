from decimal import Decimal

import pytest
from app.add_expense import dollars_to_cents, format_cents

def test_converts_dollars_to_cents():
    assert dollars_to_cents(12.50) == 1250

def test_converts_whole_dollar_and_single_cent_amounts():
    assert dollars_to_cents(100) == 10000
    assert dollars_to_cents(0.01) == 1

def test_rejects_zero_amount():
    with pytest.raises(ValueError):
        dollars_to_cents(0)

def test_rejects_negative_amount():
    with pytest.raises(ValueError):
        dollars_to_cents(-5)

def test_rejects_non_numeric_amount():
    with pytest.raises(ValueError):
        dollars_to_cents("abc")

def test_half_a_cent_rounds_up():
    # #39. This test used to pin the old answer, 100: float(1.005) * 100 is 100.49999999999999,
    # so round() dropped the half cent. Exact decimal maths with ROUND_HALF_UP gives 101.
    assert dollars_to_cents(1.005) == 101

@pytest.mark.parametrize("amount, expected", [
    ("1.005", 101),           # typed into the CLI
    (0.125, 13),              # banker's rounding would give 12
    (2.675, 268),             # a classic float trap: 2.675 is stored as 2.67499999...
    (0.005, 1),               # used to round to 0 and be rejected
    (5.555, 556),
    (Decimal("7.325"), 733),  # already a Decimal
])
def test_half_cents_round_up_whatever_the_input_type(amount, expected):
    assert dollars_to_cents(amount) == expected

@pytest.mark.parametrize("amount", [0.004, "0.001"])
def test_an_amount_that_rounds_to_zero_cents_is_rejected(amount):
    with pytest.raises(ValueError):
        dollars_to_cents(amount)

@pytest.mark.parametrize("amount", ["nan", float("nan"), float("inf"), "-inf", None, "", "1e30"])
def test_missing_non_finite_or_absurd_amounts_raise_valueerror(amount):
    # Always ValueError - never decimal.InvalidOperation or TypeError - because that's what
    # every caller catches. "1e30" is too large to hold to the cent.
    with pytest.raises(ValueError):
        dollars_to_cents(amount)

def test_every_two_decimal_amount_up_to_1000_converts_exactly():
    # The realistic range, as text (the CLI) and as a float (what the LLM's JSON becomes). The
    # #39 PR also checked every amount up to 1,000,000.00 once, offline - too slow for CI.
    for cents in range(1, 100_001):
        text = f"{cents // 100}.{cents % 100:02d}"
        assert dollars_to_cents(text) == cents
        assert dollars_to_cents(float(text)) == cents

def test_very_large_amounts_stay_exact():
    # The old float maths went wrong above about 10 trillion dollars.
    assert dollars_to_cents("99999999999999.99") == 9_999_999_999_999_999

@pytest.mark.parametrize("cents, text", [
    (101, "1.01"), (5, "0.05"), (100, "1.00"), (123456, "1234.56"), (-250, "-2.50"),
])
def test_format_cents(cents, text):
    assert format_cents(cents) == text
