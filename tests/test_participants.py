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
    OutsideAccountNotSetUp,
    Participant,
    WalletNotSetUp,
    load_directory,
    read_payer,
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
    replacement = Account(16, "Old Card", 1, None, None)
    d = replace(directory, accounts=directory.accounts + (replacement,))
    assert d.account_named("old card").id == 16
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
    t = resolve_payment({"payment_method": "old card", "currency": "SGD",
                         "description": "refund 20 on my OLD card"}, directory, directory.participant(2))
    assert (t["account_id"], t["account_owner"], t["closed_account"]) == (14, "Alice", True)

@pytest.mark.parametrize("message,expected,closed", [
    ("old test exp 25 cash", 12, False),   # an open card named in the text wins over the guess
    ("refund 25 my old one", 14, True),    # no open card named: the closed card and its warning stay
    ("cashback old 25", 14, True),         # whole words only: "cash" is not inside "cashback"
])
def test_a_closed_guess_yields_only_to_an_open_card_the_message_names(directory, message, expected, closed):
    """The model once put "Zebra test exp $25 cash" on a closed "Zebra Test Card"."""
    t = resolve_payment({"payment_method": "Old Card", "currency": "SGD", "description": message},
                        directory, directory.participant(2))
    assert (t["account_id"], t["closed_account"]) == (expected, closed)

def test_closed_card_named_across_extra_spaces_still_counts(directory):
    t = resolve_payment({"payment_method": "Old Card", "currency": "SGD", "description": "old   card 5"},
                        directory, directory.participant(2))
    assert (t["account_id"], t["closed_account"]) == (14, True)

def test_longest_open_name_wins(directory):
    d = replace(directory, accounts=directory.accounts + (Account(16, "Card A Plus", 1, None, None),))
    t = resolve_payment({"payment_method": "Old Card", "currency": "SGD", "description": "taxi 5 card a plus"},
                        d, d.participant(2))
    assert t["account_id"] == 16

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
    participants = MagicMock(all=lambda: [(1, "Alice", "111111", None, True)])  # no default account
    accounts = MagicMock(all=lambda: [(10, "Card A", 1, None, None)])
    conn = MagicMock()
    conn.execute.side_effect = [participants, accounts]
    with pytest.raises(DirectoryError, match="Alice"):
        load_directory(conn)


def test_two_open_accounts_that_differ_only_by_spaces_are_a_problem(directory):
    # The database's unique index compares lower(name) without trimming, so " Card A" can sit
    # beside "Card A". The bot treats them as one name and would silently pick one (final review).
    extra = replace(directory.account(10), id=16, name=" card a ")
    problems = validate_directory(replace(directory, accounts=directory.accounts + (extra,)))
    assert len(problems) == 1
    assert "10" in problems[0] and "16" in problems[0] and "Card A" in problems[0]

def test_a_closed_card_may_share_its_name_with_an_open_one(directory):
    replacement = replace(directory.account(14), id=16, closed_on=None)
    assert validate_directory(replace(directory, accounts=directory.accounts + (replacement,))) == []


@pytest.mark.parametrize("message,method,currency,account,payer,warning", [
    pytest.param("Lunch 40 on my Visa", "Card A", "SGD", 10, 1, False, id="sender-card"),
    pytest.param("Lunch 40 on Bob's Visa", "Card B", "SGD", 11, 2, False, id="other-card"),
    pytest.param("Bob paid 40 on his Visa", "Card B", "SGD", 11, 2, False, id="owner-agrees"),
    pytest.param("Bob paid 40 cash", "Cash", "SGD", 12, 2, False, id="household-cash"),
    pytest.param("Bob paid 40", None, "SGD", 11, 2, False, id="payer-default"),
    pytest.param("Lunch 40", None, "SGD", 10, 1, False, id="sender-default"),
    pytest.param("Lunch 40 AUD", None, "AUD", 13, 1, False, id="sender-wallet"),
    pytest.param("Bob paid 40 AUD", None, "AUD", 13, 2, False, id="payer-wallet"),
    pytest.param("Bob paid 40 on Alice's Visa", "Card A", "SGD", 10, 1, True, id="owner-wins"),
    pytest.param("Carol paid 40", None, "SGD", 15, 3, False, id="outside-default"),
    pytest.param("Carol paid 40 cash", "Cash", "SGD", 15, 3, False, id="outside-cash"),
    pytest.param("Carol paid 40 AUD", None, "AUD", 15, 3, False, id="outside-currency"),
    pytest.param("Carol paid 40 on my Visa", "Card A", "SGD", 15, 3, True, id="outside-owned"),
])
def test_plan_payment_rules(directory, message, method, currency, account, payer, warning):
    """Feed the same raw message to the pure reader and resolver, as the bot will."""
    sender = directory.participant(1)
    reading = read_payer(message, directory.participants, sender, directory.account_names())
    assert not reading.unclear
    t = resolve_payment({"description": message, "payment_method": method, "currency": currency,
                         "category": "Food"}, directory, sender, reading.payer)
    assert (t["account_id"], t["paid_by_participant_id"]) == (account, payer)
    assert t["paid_by"] == t["account_owner"] == directory.participant(payer).display_name
    assert bool(t["payer_warning"]) == warning
    if warning:
        assert reading.payer.display_name in t["payer_warning"]
        assert "Card A" in t["payer_warning"]


@pytest.mark.parametrize("message,payer,unclear", [
    ("paid by Bob", 2, False), ("paid for by BOB", 2, False),
    ("Bob paid", 2, False), ("bob pays", 2, False),
    ("I paid", 1, False), ("I pay", 1, False), ("paid by me", 1, False),
    ("Bob paid and Bob pays", 2, False), ("Bob paid and Carol paid", None, True),
    ("Bob paid and I paid", None, True),
    ("Lunch 40 paid by Card A", None, False), ("paid by cash", None, False),
    ("Lunch 40 paid by card", None, False), ("we paid 40", None, False),
    ("I just paid 40", None, False), ("already paid", None, False),
    ("Carroll paid 40", None, True), ("paid by Carroll", None, True),
    ("split with Carol 40", None, False), ("Bob's Visa 40", None, False),
    ("Bobcat paid", None, True), ("paid by Bobcat", None, True),
    ("paid by YouTrip", None, False), ("paid by Old Card", None, False),
    # The word before "paid by" is what was bought, so it never counts as an unknown name.
    ("Dinner paid by cash", None, False), ("Taxi paid for by card", None, False),
    ("Dinner paid by Carroll", None, True), ("Dinner paid by Bob", 2, False),
])
def test_read_payer_phrasings(directory, message, payer, unclear):
    reading = read_payer(message, directory.participants, directory.participant(1), directory.account_names())
    assert reading.payer == directory.participant(payer)
    assert reading.unclear == unclear


@pytest.mark.parametrize("message", ["paid by Alice Bob", "ALICE   BOB pays", "Alice Bob paid"])
def test_longest_multiword_name_wins(directory, message):
    # Both names are fictional; the shorter prefix must not make this ambiguous.
    longer = Participant(4, "Alice Bob", None, None)
    reading = read_payer(message, (directory.participant(1), directory.participant(2), longer), directory.participant(1))
    assert reading.payer == longer
    assert not reading.unclear


def test_sender_counts_even_when_not_in_candidates(directory):
    assert read_payer("I paid", (), directory.participant(1)).payer == directory.participant(1)
    assert read_payer("Carol paid", directory.household(), directory.participant(1)).unclear
    assert [p.id for p in directory.household()] == [1, 2]


def test_household_payer_without_default_falls_back_and_warns(directory):
    dave = Participant(4, "Dave", None, None, True)
    t = resolve_payment({"currency": "SGD"}, directory, directory.participant(1), dave)
    assert (t["account_id"], t["paid_by_participant_id"]) == (10, 1)
    assert "Dave" in t["payer_warning"] and "Alice" in t["payer_warning"]


@pytest.mark.parametrize("closed", [False, True])
def test_outside_account_must_exist_and_be_open(directory, closed):
    accounts = tuple(a for a in directory.accounts if a.id != 15)
    if closed:
        accounts += (replace(directory.account(15), closed_on=date(2026, 1, 1)),)
    d = replace(directory, accounts=accounts)
    assert validate_directory(d) == []  # Missing outside setup must not lock out the household.
    with pytest.raises(OutsideAccountNotSetUp):
        resolve_payment({}, d, d.participant(1), d.participant(3))


@pytest.mark.parametrize("method,expected", [(" paid BY someone else ", 10), ("Old Card", 14)])
def test_outside_account_cannot_be_chosen_by_model_or_scanning(directory, method, expected):
    message = "Lunch 40 paid by someone else"
    reading = read_payer(message, directory.participants, directory.participant(1), directory.account_names())
    assert reading.payer is None and not reading.unclear
    t = resolve_payment({"description": message, "payment_method": method, "currency": "SGD"},
                        directory, directory.participant(1), reading.payer)
    assert t["account_id"] == expected
    d = replace(directory, accounts=tuple(replace(a, name=" PAID BY SOMEONE ELSE ") if a.id == 15 else a
                                         for a in directory.accounts))
    assert len(d.account_names()) == 5


@pytest.mark.parametrize("message,warning", [("Carol paid on Old Card", True), ("Carol paid cash", False)])
def test_outside_warning_uses_closed_card_correction(directory, message, warning):
    t = resolve_payment({"description": message, "payment_method": "Old Card"},
                        directory, directory.participant(1), directory.participant(3))
    assert t["account_id"] == 15
    assert bool(t["payer_warning"]) == warning


@pytest.mark.parametrize("owned", [True, False])
def test_topup_precedes_outside_payer_and_uses_funding_owner_or_sender(directory, owned):
    d = replace(directory, accounts=tuple(replace(a, owner_participant_id=None) if a.id == 10 and not owned else a
                                         for a in directory.accounts))
    t = resolve_payment({"category": "YouTrip top-up"}, d, d.participant(2), d.participant(3))
    assert (t["account_id"], t["paid_by_participant_id"]) == (10, 1 if owned else 2)
    assert t["payer_warning"] is None


def test_unknown_model_account_uses_household_payers_default(directory):
    t = resolve_payment({"payment_method": "Unknown", "currency": "SGD"},
                        directory, directory.participant(1), directory.participant(2))
    assert (t["account_id"], t["paid_by_participant_id"]) == (11, 2)


def test_load_directory_reads_household_flag():
    conn = MagicMock()
    conn.execute.side_effect = [MagicMock(all=lambda: [(1, "Alice", "111111", 10, True),
                                                      (3, "Carol", None, None, False)]),
                                MagicMock(all=lambda: [(10, "Card A", 1, None, None)])]
    d = load_directory(conn)
    assert d.participant(1).is_household
    assert not d.participant(3).is_household
    assert "default_account_id, is_household FROM participants" in str(conn.execute.call_args_list[0].args[0])
