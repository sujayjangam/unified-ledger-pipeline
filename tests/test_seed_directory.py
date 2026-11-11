"""The seed script's pure parts: reading the seed file and matching old card names (#53).

The database half (inserts, backfill, prompts) is checked by hand against a Neon branch; these
tests never open a connection. Names are fictional - this repo is public.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from unittest.mock import MagicMock

import scripts.seed_directory as seed_directory
from app.services.participants import Account, DirectoryError
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


# --- found by the final review: a seed that would lock everyone out must never be committed ---

def test_seed_trims_names_so_the_database_matches_them():
    # The SQL compares lower(name) without trimming, so a stray space in the file used to leave a
    # default unset - and the bot then turns everyone away.
    seed = parse_seed({
        "participants": [{"display_name": " Alice ", "telegram_user_id": "111111", "default_account": "Card A "}],
        "accounts": [{"name": " Card A", "owner": "Alice"}],
    })
    assert seed.participants[0]["display_name"] == "Alice"
    assert seed.participants[0]["default_account"] == "Card A"
    assert seed.accounts[0]["name"] == "Card A"

def test_seed_rejects_a_bot_user_without_a_default():
    with pytest.raises(ValueError, match="Alice"):
        parse_seed({"participants": [{"display_name": "Alice", "telegram_user_id": "111111"}],
                    "accounts": [{"name": "Card A", "owner": "Alice"}]})

def test_seed_rejects_a_default_or_funder_that_is_only_a_closed_account():
    closed = {"name": "Old Card", "owner": "Alice", "closed_on": "2026-03-31"}
    with pytest.raises(ValueError, match="Alice"):
        parse_seed({"participants": [{"display_name": "Alice", "telegram_user_id": "111111", "default_account": "Old Card"}],
                    "accounts": [closed]})
    with pytest.raises(ValueError, match="YouTrip"):
        parse_seed({"participants": [{"display_name": "Alice"}],
                    "accounts": [closed, {"name": "YouTrip", "owner": None, "funded_by": "Old Card"}]})


# --- card expiry: written on the card as MM/YY, stored as the last day it's valid ---

def _one_card(**fields):
    return parse_seed({"participants": [], "accounts": [{"name": "Card A", "owner": None, **fields}]}).accounts[0]

def test_expiry_is_the_last_day_of_the_printed_month():
    # A card printed 09/29 still works on 30 Sep 2029.
    assert _one_card(expires="09/29")["expires_on"] == date(2029, 9, 30)

def test_expiry_in_february_of_a_leap_year():
    assert _one_card(expires="02/28")["expires_on"] == date(2028, 2, 29)

def test_no_expiry_is_none():
    # Cash, PayNow and bank accounts have no expiry.
    assert _one_card()["expires_on"] is None

@pytest.mark.parametrize("bad", ["2029-09", "13/29", "9/29", "00/29", "09/2029"])
def test_seed_rejects_a_malformed_expiry_naming_the_card(bad):
    with pytest.raises(ValueError, match="Card A"):
        _one_card(expires=bad)


def _fake_run(monkeypatch, directory_check):
    """Runs main(--write) with every database step faked; returns the fake transaction."""
    trans = MagicMock()
    conn = MagicMock()
    conn.begin.return_value = trans
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    monkeypatch.setattr(seed_directory, "get_engine", lambda: engine)
    monkeypatch.setattr(seed_directory, "_seed", lambda conn, seed: {"participants": 0, "accounts": 0})
    monkeypatch.setattr(seed_directory, "_backfill_accounts", lambda conn, seed, write: (0, []))
    monkeypatch.setattr(seed_directory, "_backfill_entered_by", lambda conn: 0)
    monkeypatch.setattr(seed_directory, "load_directory", directory_check)
    return trans

def test_write_refuses_to_commit_a_directory_the_bot_would_reject(monkeypatch):
    def broken(conn):
        raise DirectoryError("Participant 1 (Alice) uses the bot but has no default account")

    trans = _fake_run(monkeypatch, broken)
    assert seed_directory.main(["--seed", "directory_seed.example.json", "--write"]) == 1
    trans.commit.assert_not_called()
    trans.rollback.assert_called_once()

def test_write_commits_a_directory_the_bot_accepts(monkeypatch, directory):
    trans = _fake_run(monkeypatch, lambda conn: directory)
    assert seed_directory.main(["--seed", "directory_seed.example.json", "--write"]) == 0
    trans.commit.assert_called_once()
