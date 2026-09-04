import pytest
from pydantic import ValidationError

from app.services.extraction import (
    ExpenseCategory,
    TransactionList,
    TransactionSchema,
    build_allowed_accounts,
)

# Every account name below is fictional, for the same reason spelled out in
# tests/test_bot_core.py: this repo is public, and the real ACCOUNT_OWNERS contents
# (which cards this household actually holds) shouldn't be committed to it.


def test_valid_full_payload_parses():
    txn = TransactionSchema(
        amount=12.50,
        currency="SGD",
        transaction_type="Expense",
        payment_method="Bank A Platinum",
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


# --- build_allowed_accounts: the payment-method list fed to the extraction prompt ---------


def test_allowed_accounts_flattens_every_owners_accounts():
    accounts = build_allowed_accounts(
        {"Owner One": ["Bank A Platinum", "Travel Wallet"], "Owner Two": ["Bank B Rewards"]}
    )
    # Every owner's cards must appear - the bug this replaces was a hardcoded list that
    # omitted one household member's card entirely, leaving the model no way to pick it.
    assert accounts == ["Bank A Platinum", "Travel Wallet", "Bank B Rewards", "Cash"]


def test_allowed_accounts_deduplicates_a_shared_payment_rail():
    # Shared rails (a national transfer service, say) legitimately sit under both owners,
    # so de-duplication is required rather than cosmetic - listing one twice in the prompt
    # implies to the model that they're two different things.
    accounts = build_allowed_accounts(
        {"Owner One": ["Bank A Platinum", "QuickPay"], "Owner Two": ["Bank B Rewards", "QuickPay"]}
    )
    assert accounts == ["Bank A Platinum", "QuickPay", "Bank B Rewards", "Cash"]


def test_allowed_accounts_deduplicates_case_insensitively():
    # bot_core's reverse-lookup matches payment methods case-insensitively, so the prompt
    # should treat these as one entry too. First-seen casing wins.
    accounts = build_allowed_accounts(
        {"Owner One": ["QuickPay"], "Owner Two": ["quickpay"]}
    )
    assert accounts == ["QuickPay", "Cash"]


def test_allowed_accounts_always_appends_cash():
    # Cash belongs to nobody so it never appears in ACCOUNT_OWNERS, but the bot handles it
    # explicitly - without this the model loses the ability to extract "paid with cash".
    assert "Cash" in build_allowed_accounts({"Owner One": ["Bank A Platinum"]})


def test_allowed_accounts_does_not_duplicate_cash_if_already_listed():
    accounts = build_allowed_accounts({"Owner One": ["Cash", "Bank A Platinum"]})
    assert accounts == ["Cash", "Bank A Platinum"]


def test_allowed_accounts_with_no_owners_falls_back_to_cash():
    # This is the CI-import guard: .github/workflows/ci.yml imports this module with no .env
    # loaded, so ACCOUNT_OWNERS parses to {}. It must degrade, not raise.
    assert build_allowed_accounts({}) == ["Cash"]
