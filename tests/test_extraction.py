from datetime import date
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

import app.services.extraction as extraction

from app.services.extraction import (
    DateReference,
    ExpenseCategory,
    TransactionList,
    TransactionSchema,
)

# Every account name below is fictional, for the same reason spelled out in
# tests/test_bot_core.py: this repo is public, and the real account names
# (which cards this household actually holds) shouldn't be committed to it.


def test_valid_full_payload_parses():
    txn = TransactionSchema(
        amount=12.50,
        currency="SGD",
        transaction_type="Expense",
        payment_method="Bank A Platinum",
        category="Food",
        date_reference={"kind": "unstated"},
        needs_review=False,
    )
    assert txn.amount == 12.50
    assert txn.category == ExpenseCategory.FOOD


def test_amount_none_is_allowed():
    txn = TransactionSchema(
        amount=None,
        transaction_type="Expense",
        category="Other",
        date_reference={"kind": "unstated"},
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
            date_reference={"kind": "unstated"},
            needs_review=False,
        )


def test_invalid_transaction_type_rejected():
    with pytest.raises(ValidationError):
        TransactionSchema(
            amount=12.50,
            transaction_type="Refund",
            category="Food",
            date_reference={"kind": "unstated"},
            needs_review=False,
        )


def test_currency_defaults_to_sgd():
    txn = TransactionSchema(
        amount=12.50,
        transaction_type="Expense",
        category="Food",
        date_reference={"kind": "unstated"},
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
                "date_reference": {"kind": "unstated"},
                "needs_review": False,
            },
            {
                "amount": 5.00,
                "transaction_type": "Expense",
                "category": "Transport",
                "date_reference": {"kind": "unstated"},
                "needs_review": False,
            },
        ]
    )
    assert len(txn_list.transactions) == 2
    assert txn_list.transactions[1].category == ExpenseCategory.TRANSPORT


# --- DateReference: what the user said about the date (#15, ADR-0030) --------------------


def test_date_reference_parses_a_weekday_reference():
    ref = DateReference(kind="weekday", weekday="Tuesday", last_week=False)
    assert ref.weekday == "Tuesday"
    # The shapes that don't apply stay null - the resolver relies on only one being filled.
    assert ref.days_ago is None and ref.day is None


def test_date_reference_parses_a_calendar_reference_without_a_year():
    ref = DateReference(kind="calendar", day=3, month=7)
    assert (ref.day, ref.month, ref.year) == (3, 7, None)


def test_date_reference_rejects_an_unknown_kind():
    # kind decides which fields the resolver reads, so an unknown one must fail here
    # rather than reach the resolver and fall through to a silent default.
    with pytest.raises(ValidationError):
        DateReference(kind="next_week")


def test_date_reference_rejects_a_misspelled_weekday():
    with pytest.raises(ValidationError):
        DateReference(kind="weekday", weekday="Tues")


def test_transaction_requires_a_date_reference():
    # The old free-text `date` field is gone; a payload without date_reference is invalid.
    with pytest.raises(ValidationError):
        TransactionSchema(
            amount=12.50,
            transaction_type="Expense",
            category="Food",
            needs_review=False,
        )


# --- The payment-method list fed to the extraction prompt -----------------------------------
#
# It used to be built here from the ACCOUNT_OWNERS env var. Since #53 the caller passes the names
# from the database (Directory.account_names(), tested in tests/test_participants.py), so all this
# module has to get right is putting exactly those names in the prompt.


async def test_prompt_lists_exactly_the_accounts_passed_in(monkeypatch):
    # Raising from the fake stops the call before any network use, after the prompt is built.
    parse = AsyncMock(side_effect=RuntimeError("stop"))
    monkeypatch.setattr(extraction.openai_client.beta.chat.completions, "parse", parse)
    await extraction.extract_transactions("lunch 12", date(2026, 11, 11), ["Card A", "Cash"])
    system = parse.call_args.kwargs["messages"][0]["content"]
    assert "The only valid payment methods are: Card A, Cash." in system
