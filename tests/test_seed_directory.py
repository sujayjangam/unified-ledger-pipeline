"""The seed script's pure parts: reading the seed file and matching old card names (#53).

The database half (inserts, backfill, prompts) is checked by hand against a Neon branch; these
tests never open a connection. Names are fictional - this repo is public.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from app.services.participants import Account
from scripts.seed_directory import match_account, parse_seed

# The same accounts as the shared `directory` fixture in tests/conftest.py. A plain list here,
# because match_account takes the accounts the database holds, not a Directory.
ACCOUNTS = [
    Account(10, "Card A", 1, None, None),
    Account(11, "Card B", 2, None, None),
    Account(12, "Cash", None, None, None),
    Account(13, "YouTrip", None, 10, None),
    Account(14, "Old Card", 1, None, date(2026, 3, 31)),
]


def test_match_ignores_case_and_spaces():
    assert match_account(" CARD A ", date(2026, 5, 1), ACCOUNTS) == ("matched", [10])

def test_closed_account_matches_up_to_its_closed_on_date():
    assert match_account("Old Card", date(2026, 3, 31), ACCOUNTS) == ("matched", [14])

def test_closed_card_after_its_closed_on_date_needs_confirmation():
    # A late refund on a cancelled card is real; the script asks before linking it.
    assert match_account("Old Card", date(2026, 4, 1), ACCOUNTS) == ("closed", [14])

def test_a_replacement_card_with_the_same_name_is_always_asked():
    # Old Card (14) closed 2026-03-31; open replacement 15. Any row could be a late refund on 14.
    accounts = ACCOUNTS + [Account(15, "Old Card", 1, None, None)]
    assert match_account("Old Card", date(2026, 3, 31), accounts) == ("ambiguous", [14, 15])
    assert match_account("Old Card", date(2026, 4, 1), accounts) == ("ambiguous", [14, 15])

def test_unknown_or_missing_name_is_unmatched():
    assert match_account("Nope", date(2026, 5, 1), ACCOUNTS) == ("unmatched", [])
    assert match_account(None, date(2026, 5, 1), ACCOUNTS) == ("unmatched", [])

def test_seed_rejects_a_default_that_is_not_a_listed_account():
    with pytest.raises(ValueError, match="Alice"):
        parse_seed({"participants": [{"display_name": "Alice", "telegram_user_id": "111111", "default_account": "Nope"}], "accounts": []})

def test_seed_rejects_an_owner_who_is_not_a_listed_participant():
    with pytest.raises(ValueError, match="Card A"):
        parse_seed({"participants": [], "accounts": [{"name": "Card A", "owner": "Nobody"}]})

def test_the_example_seed_file_parses():
    parse_seed(json.loads(Path("directory_seed.example.json").read_text()))
