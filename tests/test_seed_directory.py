"""The seed script's pure parts: reading the seed file, and the commit/rollback decision (#53).

The database half (the inserts) is checked by hand against a Neon branch; these
tests never open a connection. Names are fictional - this repo is public.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from unittest.mock import MagicMock

import scripts.seed_directory as seed_directory
from app.services.participants import DirectoryError
from scripts.seed_directory import parse_seed


def test_seed_rejects_a_default_that_is_not_a_listed_account():
    with pytest.raises(ValueError, match="Alice"):
        parse_seed({"participants": [{"display_name": "Alice", "telegram_user_id": "111111", "household": True, "default_account": "Nope"}], "accounts": []})

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
        "participants": [{"display_name": " Alice ", "telegram_user_id": "111111", "household": True, "default_account": "Card A "}],
        "accounts": [{"name": " Card A", "owner": "Alice"}],
    })
    assert seed.participants[0]["display_name"] == "Alice"
    assert seed.participants[0]["default_account"] == "Card A"
    assert seed.accounts[0]["name"] == "Card A"

def test_seed_rejects_a_bot_user_without_a_default():
    with pytest.raises(ValueError, match="Alice"):
        parse_seed({"participants": [{"display_name": "Alice", "telegram_user_id": "111111", "household": True}],
                    "accounts": [{"name": "Card A", "owner": "Alice"}]})

def test_seed_rejects_a_default_or_funder_that_is_only_a_closed_account():
    closed = {"name": "Old Card", "owner": "Alice", "closed_on": "2026-03-31"}
    with pytest.raises(ValueError, match="Alice"):
        parse_seed({"participants": [{"display_name": "Alice", "telegram_user_id": "111111", "household": True, "default_account": "Old Card"}],
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


# --- trips (migration 0006): families flattened to (person, family) pairs, checked before any write ---

def _with_trip(families, name="Beach Trip", people=("Alice", "Bob", "Carol")):
    return parse_seed({
        "participants": [{"display_name": p} for p in people],
        "accounts": [],
        "trips": [{"name": name, "start": "2026-12-01", "end": "2026-12-07", "families": families}],
    })

def test_a_trip_flattens_its_families_into_members():
    seed = _with_trip({"Alice and Bob": ["Alice", "Bob"], "Carol's": ["Carol"]})
    assert seed.trips == [{"name": "Beach Trip", "start_date": date(2026, 12, 1), "end_date": date(2026, 12, 7),
                           "members": [("Alice", "Alice and Bob"), ("Bob", "Alice and Bob"), ("Carol", "Carol's")]}]

def test_a_trip_member_must_be_a_listed_participant():
    with pytest.raises(ValueError, match="Beach Trip.*Zed"):
        _with_trip({"Fam": ["Alice", "Zed"]})

def test_a_person_can_be_in_only_one_family_per_trip():
    with pytest.raises(ValueError, match="Alice is in more than one family"):
        _with_trip({"One": ["Alice"], "Two": ["Alice", "Bob"]})

def test_two_trips_cannot_share_a_name_ignoring_case():
    with pytest.raises(ValueError, match="share this name"):
        parse_seed({"participants": [{"display_name": "Alice"}], "accounts": [],
                    "trips": [{"name": "Beach Trip", "start": "2026-12-01", "end": "2026-12-07", "families": {"F": ["Alice"]}},
                              {"name": "beach trip ", "start": "2026-12-01", "end": "2026-12-07", "families": {"F": ["Alice"]}}]})

def test_a_trip_with_no_members_is_refused():
    with pytest.raises(ValueError, match="no members"):
        _with_trip({})

def test_a_seed_file_without_trips_still_parses():
    assert parse_seed({"participants": [], "accounts": []}).trips == []


@pytest.mark.parametrize("fields", [
    {}, {"start": "2026-12-01"}, {"end": "2026-12-07"},
    {"start": "20261201", "end": "2026-12-07"},
    {"start": "2026-2-01", "end": "2026-12-07"},
    {"start": "2026-02-30", "end": "2026-12-07"},
    {"start": "2026-12-01", "end": "2026-11-30"},
    {"start": None, "end": "2026-12-07"},
    {"start": "2026-12-01", "end": 20261207},
])
def test_trip_dates_are_required_and_valid(fields):
    with pytest.raises(ValueError, match="Trip Beach Trip:"):
        parse_seed({"participants": [{"display_name": "Alice"}],
                    "trips": [{"name": "Beach Trip", "families": {"F": ["Alice"]}, **fields}]})


def test_single_day_trip_and_inserted_dates():
    seed = parse_seed({"participants": [{"display_name": "Alice"}],
                       "trips": [{"name": "Beach Trip", "families": {"F": ["Alice"]},
                                  "start": "2026-12-01", "end": "2026-12-01"}]})
    conn = MagicMock()
    conn.execute.return_value.rowcount = 1
    seed_directory._seed(conn, seed)
    call = next(c for c in conn.execute.call_args_list if "INSERT INTO trips" in str(c.args[0]))
    assert call.args[1] == {"name": "Beach Trip", "start_date": date(2026, 12, 1), "end_date": date(2026, 12, 1)}


@pytest.mark.parametrize("household", [None, "true", 1, 0, [], {}])
def test_household_flag_must_be_a_bool(household):
    with pytest.raises(ValueError, match="Alice.*household must be a bool"):
        parse_seed({"participants": [{"display_name": "Alice", "household": household}]})


@pytest.mark.parametrize("fields", [{}, {"household": False}])
def test_bot_user_must_explicitly_be_household(fields):
    with pytest.raises(ValueError, match="Alice.*household: true"):
        parse_seed({"participants": [{"display_name": "Alice", "telegram_user_id": "111111",
                                      "default_account": "Card A", **fields}],
                    "accounts": [{"name": "Card A", "owner": "Alice"}]})


def test_household_without_telegram_needs_no_default():
    seed = parse_seed({"participants": [{"display_name": "Dave", "household": True}]})
    assert seed.participants[0]["household"] is True


@pytest.mark.parametrize("name", ["Paid by someone else", " PAID BY SOMEONE ELSE "])
def test_outside_account_must_have_no_owner(name):
    with pytest.raises(ValueError, match="must have no owner"):
        parse_seed({"participants": [{"display_name": "Alice"}], "accounts": [{"name": name, "owner": "Alice"}]})
    assert parse_seed({"accounts": [{"name": name, "owner": None}]}).accounts[0]["owner"] is None


@pytest.mark.parametrize("updated", [0, 1])
def test_seed_sets_household_on_insert_and_only_promotes_existing_rows(updated):
    """A fake connection reports a promotion once, or zero on a repeat; no DB is opened."""
    seed = parse_seed({"participants": [{"display_name": "Alice", "household": True},
                                       {"display_name": "Bob", "household": False},
                                       {"display_name": "Carol"}]})
    conn = MagicMock()
    conn.execute.side_effect = lambda sql, params: MagicMock(
        rowcount=updated if "UPDATE participants" in str(sql) else 0)
    counts = seed_directory._seed(conn, seed)
    inserts = [c for c in conn.execute.call_args_list if "INSERT INTO participants" in str(c.args[0])]
    assert [c.args[1]["household"] for c in inserts] == [True, False, False]
    assert all("is_household" in str(c.args[0]) for c in inserts)
    updates = [c for c in conn.execute.call_args_list if "UPDATE participants" in str(c.args[0])]
    assert len(updates) == 1
    assert updates[0].args[1] == {"name": "Alice"}
    assert "SET is_household = true WHERE display_name = :name AND NOT is_household" in str(updates[0].args[0])
    assert counts["household_flags"] == updated


def test_seed_report_includes_household_promotions(monkeypatch, directory, capsys):
    _fake_run(monkeypatch, lambda conn: directory)
    monkeypatch.setattr(seed_directory, "_seed", lambda conn, seed: {"participants": 0, "accounts": 0, "household_flags": 2})
    assert seed_directory.main(["--seed", "directory_seed.example.json", "--write"]) == 0
    assert "Household flags set:   2" in capsys.readouterr().out
