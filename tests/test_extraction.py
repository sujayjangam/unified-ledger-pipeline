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


def test_split_count_fields_and_old_defaults():
    """New count fields are optional so existing references keep their even semantics."""
    old = extraction.SplitReference(kind='named', include_sender=False,
                                    people=[{'name': 'Carol', 'is_family': False}],
                                    except_people=[])
    assert old.people[0].count is None
    assert old.default_count is None and old.count_unit is None
    counted = extraction.SplitReference(kind='everyone', include_sender=True,
        people=[{'name': 'Carol', 'is_family': True, 'count': 4}],
        except_people=[], default_count=5, count_unit='nights')
    assert counted.people[0].count == 4
    assert counted.default_count == 5 and counted.count_unit == 'nights'


def test_valid_full_payload_parses():
    txn = TransactionSchema(
        amount=12.50,
        currency="SGD",
        transaction_type="Expense",
        payment_method="Bank A Platinum",
        category="Food",
        date_reference={"kind": "unstated"},
        split_reference={"kind": "none", "include_sender": False, "people": [], "except_people": []},
        payer_name=None,
    )
    assert txn.amount == 12.50
    assert txn.category == ExpenseCategory.FOOD


def test_amount_none_is_allowed():
    txn = TransactionSchema(
        amount=None,
        transaction_type="Expense",
        category="Other",
        date_reference={"kind": "unstated"},
        split_reference={"kind": "none", "include_sender": False, "people": [], "except_people": []},
        payer_name=None,
    )
    assert txn.amount is None


def test_invalid_category_rejected():
    with pytest.raises(ValidationError):
        TransactionSchema(
            amount=12.50,
            transaction_type="Expense",
            category="Foods",
            date_reference={"kind": "unstated"},
            split_reference={"kind": "none", "include_sender": False, "people": [], "except_people": []},
            payer_name=None,
        )


def test_invalid_transaction_type_rejected():
    with pytest.raises(ValidationError):
        TransactionSchema(
            amount=12.50,
            transaction_type="Refund",
            category="Food",
            date_reference={"kind": "unstated"},
            split_reference={"kind": "none", "include_sender": False, "people": [], "except_people": []},
            payer_name=None,
        )


def test_currency_defaults_to_sgd():
    txn = TransactionSchema(
        amount=12.50,
        transaction_type="Expense",
        category="Food",
        date_reference={"kind": "unstated"},
        split_reference={"kind": "none", "include_sender": False, "people": [], "except_people": []},
        payer_name=None,
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
                "split_reference": {"kind": "none", "include_sender": False, "people": [], "except_people": []},
                "payer_name": None,
            },
            {
                "amount": 5.00,
                "transaction_type": "Expense",
                "category": "Transport",
                "date_reference": {"kind": "unstated"},
                "split_reference": {"kind": "none", "include_sender": False, "people": [], "except_people": []},
                "payer_name": None,
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
            split_reference={"kind": "none", "include_sender": False, "people": [], "except_people": []},
            payer_name=None,
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


async def test_extraction_runs_at_temperature_zero(monkeypatch):
    """The most likely reading every time, not a random draw (#120). Without it the API
    default is 1, and the same message can read differently from one try to the next."""
    parse = AsyncMock(side_effect=RuntimeError("stop"))
    monkeypatch.setattr(extraction.openai_client.beta.chat.completions, "parse", parse)
    await extraction.extract_transactions("lunch 12", date(2026, 11, 11), ["Card A"])
    assert parse.call_args.kwargs["temperature"] == 0


# --- What the model reads with every message (#120) -----------------------------------------
#
# The prompt nearly doubled while splits were built, and price-first amounts started slipping
# soon after. These tests keep that growth visible: they can't say whether the model reads
# well (only scripts/check_extraction.py can), but they fail when the text grows past the cap,
# so raising it is a deliberate change in a reviewed diff.

def _descriptions(node):
    """Every description string in a JSON schema, at any depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                yield value
            else:
                yield from _descriptions(value)
    elif isinstance(node, list):
        for value in node:
            yield from _descriptions(value)


def test_no_model_has_a_docstring_sent_to_the_model():
    """Pydantic sends a model class's docstring as its schema description, so a note for
    developers there is read by the model with every message. Notes go in # comments."""
    schema = TransactionList.model_json_schema()
    described = [name for name, model in schema["$defs"].items() if "description" in model]
    assert described == []
    assert "description" not in {k for k in schema if k != "properties"}


def test_prompt_text_has_no_indentation():
    """Indentation inside the strings is sent to the model too."""
    texts = [*_descriptions(TransactionList.model_json_schema()),
             extraction.system_prompt(date(2026, 11, 11), ["Card A"])]
    assert not [t for t in texts if "  " in t]


def test_prompt_stays_under_its_size_cap():
    """Descriptions plus system prompt: about 4,800 characters after #120, down from 8,900
    (1,900 of that was indentation). The whole request, schema structure included, went from
    about 12,600 to 8,300. Raise the cap only with a check_extraction run showing the new
    text is worth it."""
    size = (sum(len(t) for t in _descriptions(TransactionList.model_json_schema()))
            + len(extraction.system_prompt(date(2026, 11, 11), ["Card A"])))
    assert size <= 5_200
