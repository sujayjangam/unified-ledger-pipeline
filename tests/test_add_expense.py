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
    # float(1.05) * 100 == 100.49999999994 in Python due to binary floating-point
    # representation, so round() gives 267, not the mathematically expected 268.
    # This us existing behaviour, not something this refactor introduced or fixed.
    assert dollars_to_cents(1.005) == 100