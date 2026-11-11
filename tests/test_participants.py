"""Rules over the participants/accounts Directory (#53). All pure: no database, no Telegram.

The `directory` fixture (tests/conftest.py) is fictional - Alice, Bob, Carol, Card A, Card B.
"""

from dataclasses import replace
from datetime import date
from unittest.mock import MagicMock

import pytest

from app.services.participants import (
    Account,
    DirectoryError,
    WalletNotSetUp,
    load_directory,
    resolve_payment,
    validate_directory,
    whose_card_paid,
)


def test_open_account_named_ignores_case_and_spaces(directory):
    assert directory.open_account_named("  card a ").id == 10
    assert directory.open_account_named("CARD A").id == 10

def test_open_account_named_skips_closed_accounts(directory):
    assert directory.open_account_named("Old Card") is None

def test_account_names_lists_open_then_closed_accounts(directory):
    assert directory.account_names() == ["Card A", "Card B", "Cash", "YouTrip", "Old Card"]

def test_account_named_prefers_an_open_account_with_the_same_name(directory):
    replacement = Account(15, "Old Card", 1, None, None)
    d = replace(directory, accounts=directory.accounts + (replacement,))
    assert d.account_named("old card").id == 15
    assert d.account_names().count("Old Card") == 1

def test_account_is_open_on_its_closed_on_date_but_not_after(directory):
    old = directory.account(14)
    assert old.is_open_on(date(2026, 3, 31))
    assert not old.is_open_on(date(2026, 4, 1))

def test_shared_account_resolves_to_the_sender(directory):
    bob = directory.participant(2)
    assert whose_card_paid(directory.account(12), bob, directory) == bob

def test_single_owner_card_resolves_to_its_owner_whoever_sends(directory):
    bob = directory.participant(2)
    assert whose_card_paid(directory.account(10), bob, directory).display_name == "Alice"

def test_shared_account_with_no_sender_is_not_recorded(directory):
    assert whose_card_paid(directory.account(12), None, directory) is None

def test_topup_is_paid_from_the_wallets_funding_card_whoever_sends(directory):
    t = resolve_payment({"category": "YouTrip top-up", "currency": "SGD"}, directory, directory.participant(2))
    assert (t["account_id"], t["payment_method"], t["account_owner"], t["transaction_type"]) == (10, "Card A", "Alice", "Transfer")

def test_named_card_is_kept(directory):
    t = resolve_payment({"payment_method": "card b", "currency": "SGD"}, directory, directory.participant(1))
    assert (t["account_id"], t["payment_method"], t["account_owner"]) == (11, "Card B", "Bob")

def test_missing_card_in_sgd_uses_the_senders_default(directory):
    t = resolve_payment({"payment_method": None, "currency": "SGD"}, directory, directory.participant(2))
    assert t["account_id"] == 11

def test_missing_card_in_foreign_currency_uses_the_wallet(directory):
    t = resolve_payment({"payment_method": None, "currency": "MYR"}, directory, directory.participant(2))
    assert (t["account_id"], t["account_owner"]) == (13, "Bob")

def test_unknown_card_falls_back_to_the_default_rule(directory):
    t = resolve_payment({"payment_method": "Made Up Card", "currency": "SGD"}, directory, directory.participant(1))
    assert (t["account_id"], t["payment_method"], t["closed_account"]) == (10, "Card A", False)

def test_closed_card_is_kept_and_flagged(directory):
    t = resolve_payment({"payment_method": "old card", "currency": "SGD"}, directory, directory.participant(2))
    assert (t["account_id"], t["account_owner"], t["closed_account"]) == (14, "Alice", True)

@pytest.mark.parametrize("wallet_change", ["closed", "no_funder", "missing"])
def test_topup_is_refused_when_the_wallet_is_not_set_up(directory, wallet_change):
    youtrip = directory.account(13)
    changed = {"closed": (replace(youtrip, closed_on=date(2026, 1, 1)),),
               "no_funder": (replace(youtrip, funded_by_account_id=None),),
               "missing": ()}[wallet_change]
    d = replace(directory, accounts=directory.accounts[:3] + changed + directory.accounts[4:])
    with pytest.raises(WalletNotSetUp):
        resolve_payment({"category": "YouTrip top-up", "currency": "SGD"}, d, d.participant(1))

def test_valid_directory_has_no_problems(directory):
    assert validate_directory(directory) == []

def test_validation_lists_every_problem_and_names_each_row(directory):
    broken = replace(directory, participants=(
        replace(directory.participant(1), default_account_id=14),   # closed default
        replace(directory.participant(2), default_account_id=None), # no default
    ), accounts=directory.accounts[:3] + (replace(directory.account(13), funded_by_account_id=14),) + directory.accounts[4:])
    problems = validate_directory(broken)
    assert len(problems) == 3
    assert any("Alice" in p and "1" in p for p in problems)
    assert any("Bob" in p for p in problems)
    assert any("YouTrip" in p and "13" in p for p in problems)

def test_participant_without_telegram_id_needs_no_default(directory):
    assert directory.participant(3).default_account_id is None
    assert validate_directory(directory) == []

def test_load_directory_raises_with_the_problems():
    participants = MagicMock(all=lambda: [(1, "Alice", "111111", None)])  # no default account
    accounts = MagicMock(all=lambda: [(10, "Card A", 1, None, None)])
    conn = MagicMock()
    conn.execute.side_effect = [participants, accounts]
    with pytest.raises(DirectoryError, match="Alice"):
        load_directory(conn)


def test_two_open_accounts_that_differ_only_by_spaces_are_a_problem(directory):
    # The database's unique index compares lower(name) without trimming, so " Card A" can sit
    # beside "Card A". The bot treats them as one name and would silently pick one (final review).
    extra = replace(directory.account(10), id=15, name=" card a ")
    problems = validate_directory(replace(directory, accounts=directory.accounts + (extra,)))
    assert len(problems) == 1
    assert "10" in problems[0] and "15" in problems[0] and "Card A" in problems[0]

def test_a_closed_card_may_share_its_name_with_an_open_one(directory):
    replacement = replace(directory.account(14), id=15, closed_on=None)
    assert validate_directory(replace(directory, accounts=directory.accounts + (replacement,))) == []
