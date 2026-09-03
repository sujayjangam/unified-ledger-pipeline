import pytest
from app.add_expense import dollars_to_cents

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

def test_documents_float_rounding_edge_case():
    # float(1.005) * 100 == 100.49999999999999 in Python due to binary floating-point
    # representation, so round() gives 100, not the mathematically expected 101.
    # This is existing behavior, not something this refactor introduced or fixed - see #39.
    assert dollars_to_cents(1.005) == 100

def test_converts_the_specific_amounts_named_in_issue_33():
    # #33 named these three values explicitly as "the cases floats get wrong" - none of
    # them actually break the current rounding (verified directly: 0.07 * 100 comes out
    # as 7.000000000000001, but that still rounds to 7 correctly), but they're kept here
    # as a literal, named regression check since the issue called them out by value.
    assert dollars_to_cents(12.10) == 1210
    assert dollars_to_cents(0.07) == 7
    assert dollars_to_cents(1234.56) == 123456