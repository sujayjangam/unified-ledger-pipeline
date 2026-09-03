import pytest
from pydantic import ValidationError

from app.services.extraction import ExpenseCategory, TransactionList, TransactionSchema


def test_valid_full_payload_parses():
    txn = TransactionSchema(
        amount=12.50,
        currency="SGD",
        transaction_type="Expense",
        payment_method="OCBC Infinity",
        category="Food",
        date="2026-09-02",
        needs_review=False,
    )
    assert txn.amount == 12.50
    assert txn.category == ExpenseCategory.FOOD


def test_amount_none_is_allowed():
    txn = TransactionSchema(
        amount=None,
        transaction_type="Expense",
        category="Other",
        date="2026-09-02",
        needs_review=True,
    )
    assert txn.amount is None
    assert txn.needs_review is True


def test_invalid_category_rejected():
    with pytest.raises(ValidationError):
        TransactionSchema(
            amount=12.50,
            transaction_type="Expense",
            category="Foods",
            date="2026-09-02",
            needs_review=False,
        )


def test_invalid_transaction_type_rejected():
    with pytest.raises(ValidationError):
        TransactionSchema(
            amount=12.50,
            transaction_type="Refund",
            category="Food",
            date="2026-09-02",
            needs_review=False,
        )


def test_currency_defaults_to_sgd():
    txn = TransactionSchema(
        amount=12.50,
        transaction_type="Expense",
        category="Food",
        date="2026-09-02",
        needs_review=False,
    )
    assert txn.currency == "SGD"


def test_transaction_list_wraps_multiple_transactions():
    txn_list = TransactionList(
        transactions=[
            {
                "amount": 12.50,
                "transaction_type": "Expense",
                "category": "Food",
                "date": "2026-09-02",
                "needs_review": False,
            },
            {
                "amount": 5.00,
                "transaction_type": "Expense",
                "category": "Transport",
                "date": "2026-09-02",
                "needs_review": False,
            },
        ]
    )
    assert len(txn_list.transactions) == 2
    assert txn_list.transactions[1].category == ExpenseCategory.TRANSPORT
