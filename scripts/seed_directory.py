"""Seeds the participants, accounts and trips tables (#53, group-trip splitting).

Migration 0004 created the tables empty on purpose: this repo is public, and the rows are real
names and card labels. They live in a gitignored file instead - directory_seed.local.json, in the
format of directory_seed.example.json - and this script copies them in.

Run from the repo root. Point DATABASE_URL at a Neon branch first and check the host it prints:

    python -m scripts.seed_directory            # dry run: does everything, then rolls back
    python -m scripts.seed_directory --write    # same, then commits

A dry run performs the real inserts inside a transaction and rolls them back, so its report is
exactly what --write would do. Before committing, the script reads everything back the way the bot
will, and rolls back instead if the bot would refuse everyone. Rerunning is always safe: rows that
exist are left alone, except empty defaults/funders and household flags that the file sets true.
Household flags (migration 0007) are never cleared. Bot users must be household; the shared
"Paid by someone else" account must have no owner.

It used to link old transactions to these tables too, by matching their account_desc and
entered_by text. Migration 0005 dropped those columns, so that part was removed; the
transaction_overrides in the seed file are still checked but no longer applied - they stay as the
record of which card each unlabelled row was given.

Trips (migration 0006) come from the same file: each trip names its families and the
participants in each, with start and end dates. The script never changes a person's trip
switch and leaves an existing member's family alone on a rerun, like every other existing row.
"""

import argparse
import calendar
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import text

from app.database import get_engine
from app.services.participants import OUTSIDE_PAYER_ACCOUNT_NAME, DirectoryError, load_directory

DEFAULT_SEED = "directory_seed.local.json"


def _key(name: str | None) -> str | None:
    # The same comparison the bot uses (app/services/participants.py): trimmed, case ignored.
    return name.strip().casefold() if name else None


@dataclass(frozen=True)
class SeedData:
    participants: list[dict]
    accounts: list[dict]
    transaction_overrides: dict[str, str]
    # Each trip has name, start_date, end_date and members [(display_name, family), ...].
    trips: list[dict]


def parse_expiry(printed: str) -> date:
    """A card's printed expiry, "MM/YY", as the last day the card is valid: a card printed 09/29
    still works on 30 Sep 2029. Exactly two digits each side, so "9/29" or "09/2029" is refused
    rather than guessed at. Raises ValueError."""
    if not re.fullmatch(r"\d{2}/\d{2}", printed or ""):
        raise ValueError(f"expires {printed!r} should be MM/YY, as printed on the card")
    month, year = int(printed[:2]), 2000 + int(printed[3:])
    if not 1 <= month <= 12:
        raise ValueError(f"expires {printed!r} has no month {month}")
    # monthrange gives (weekday of the 1st, days in the month); the second handles leap years.
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_seed(raw: dict) -> SeedData:
    """Checks the seed file before anything touches the database. Every name it refers to must be
    defined in the same file, so a typo fails here with the entry's name, not halfway through a
    write. Raises ValueError."""
    # Every name is trimmed here, once. The SQL below compares lower(name) and does not trim, so a
    # stray space in the file would otherwise leave a default unset - and the bot then turns
    # everyone away (found by the final review).
    def trimmed(entry, *fields):
        return {k: (v.strip() if k in fields and isinstance(v, str) else v) for k, v in entry.items()}

    participants = [trimmed(p, "display_name", "default_account") for p in raw.get("participants", [])]
    accounts = [trimmed(a, "name", "owner", "funded_by") for a in raw.get("accounts", [])]
    overrides = {t: name.strip() for t, name in raw.get("transaction_overrides", {}).items()}

    people = {p["display_name"] for p in participants}
    account_names = {_key(a["name"]) for a in accounts}
    # A default or a funding card must be open: the bot refuses to start a conversation otherwise.
    open_names = {_key(a["name"]) for a in accounts if a.get("closed_on") is None}

    for p in participants:
        if not isinstance(p.get("household", False), bool):
            raise ValueError(f"Participant {p['display_name']}: household must be a bool")
        if p.get("telegram_user_id") is not None and not p.get("household", False):
            raise ValueError(f"Participant {p['display_name']}: uses the bot, so needs household: true")
        default = p.get("default_account")
        if default is not None and _key(default) not in account_names:
            raise ValueError(f"Participant {p['display_name']}: default_account {default!r} is not a listed account")
        if default is not None and _key(default) not in open_names:
            raise ValueError(f"Participant {p['display_name']}: default_account {default!r} is closed")
        if p.get("telegram_user_id") is not None and default is None:
            raise ValueError(f"Participant {p['display_name']}: uses the bot, so needs a default_account")

    seen_open = set()
    for a in accounts:
        if _key(a["name"]) == _key(OUTSIDE_PAYER_ACCOUNT_NAME) and a.get("owner") is not None:
            raise ValueError(f"Account {a['name']}: outside-payer account must have no owner")
        if a.get("owner") is not None and a["owner"] not in people:
            raise ValueError(f"Account {a['name']}: owner {a['owner']!r} is not a listed participant")
        if a.get("funded_by") is not None and _key(a["funded_by"]) not in account_names:
            raise ValueError(f"Account {a['name']}: funded_by {a['funded_by']!r} is not a listed account")
        if a.get("funded_by") is not None and _key(a["funded_by"]) not in open_names:
            raise ValueError(f"Account {a['name']}: funded_by {a['funded_by']!r} is closed")
        # Converted once here, so the insert below gets a real date and a typo fails with the
        # card's name before anything touches the database.
        try:
            a["expires_on"] = parse_expiry(a["expires"]) if a.get("expires") is not None else None
        except ValueError as e:
            raise ValueError(f"Account {a['name']}: {e}") from None
        if a.get("closed_on") is not None:
            date.fromisoformat(a["closed_on"])  # raises ValueError on a malformed date
        elif _key(a["name"]) in seen_open:
            # The database would reject this too (uq_accounts_open_name_ci); say so earlier.
            raise ValueError(f"Account {a['name']}: two open accounts share this name")
        else:
            seen_open.add(_key(a["name"]))

    for transaction_id, name in overrides.items():
        if _key(name) not in account_names:
            raise ValueError(f"Override for {transaction_id}: {name!r} is not a listed account")

    trips = _parse_trips(raw.get("trips", []), people)
    return SeedData(participants, accounts, overrides, trips)


def _parse_trips(raw_trips: list, people: set) -> list[dict]:
    """Checks the trips section and flattens each trip's families into (person, family) pairs.

    In the file a trip reads {"name": "Beach Trip", "families": {"Alice's family": ["Alice"]}},
    because that is how a trip is planned. Every member must be a participant listed in the same
    file, and appear only once per trip: a person in two families would make it ambiguous who
    settles their share. Raises ValueError naming the trip."""
    trips, seen_names = [], set()
    for t in raw_trips:
        name = (t.get("name") or "").strip()
        if not name:
            raise ValueError("A trip has no name")
        if _key(name) in seen_names:
            # The database would reject this too (uq_trips_name_ci); say so earlier.
            raise ValueError(f"Trip {name}: two trips share this name")
        seen_names.add(_key(name))

        # fromisoformat also accepts compact dates; require the seed's explicit YYYY-MM-DD
        # spelling first so malformed or absent dates fail here, before any database access.
        dates = {}
        for field in ("start", "end"):
            value = t.get(field)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                raise ValueError(f"Trip {name}: {field} must be YYYY-MM-DD")
            try:
                dates[field] = date.fromisoformat(value)
            except ValueError:
                raise ValueError(f"Trip {name}: {field} must be a valid YYYY-MM-DD date") from None
        if dates["end"] < dates["start"]:
            raise ValueError(f"Trip {name}: end must be on or after start")

        members, placed = [], set()
        for family, people_in_family in (t.get("families") or {}).items():
            family = family.strip()
            if not family:
                raise ValueError(f"Trip {name}: a family has no name")
            for person in people_in_family:
                person = person.strip()
                if person not in people:
                    raise ValueError(f"Trip {name}: {person!r} is not a listed participant")
                if person in placed:
                    raise ValueError(f"Trip {name}: {person} is in more than one family")
                placed.add(person)
                members.append((person, family))
        if not members:
            raise ValueError(f"Trip {name}: no members")
        trips.append({"name": name, "start_date": dates["start"], "end_date": dates["end"], "members": members})
    return trips



def _seed(conn, seed: SeedData) -> dict:
    """Inserts the participants and accounts that don't exist yet, then wires up defaults and
    funding cards by name. Returns counts for the report."""
    counts = {"participants": 0, "accounts": 0, "trips": 0, "trip_members": 0, "household_flags": 0}

    for p in seed.participants:
        inserted = conn.execute(text("""
            INSERT INTO participants (display_name, telegram_user_id, is_household)
            SELECT :name, :tg, :household WHERE NOT EXISTS (SELECT 1 FROM participants WHERE display_name = :name)
        """), {"name": p["display_name"], "tg": p.get("telegram_user_id"), "household": p.get("household", False)}).rowcount
        counts["participants"] += inserted
        if p.get("household", False):
            # Reruns may promote an existing person, but must never clear a household flag.
            counts["household_flags"] += conn.execute(text("""
                UPDATE participants SET is_household = true WHERE display_name = :name AND NOT is_household
            """), {"name": p["display_name"]}).rowcount

    for a in seed.accounts:
        # An account is "the same row" when its name (case ignored) and closed_on both match, so a
        # closed card and its open replacement can share a name.
        inserted = conn.execute(text("""
            INSERT INTO accounts (name, owner_participant_id, closed_on, expires_on)
            SELECT :name, (SELECT id FROM participants WHERE display_name = :owner), CAST(:closed_on AS date),
                   CAST(:expires_on AS date)
            WHERE NOT EXISTS (
                SELECT 1 FROM accounts
                WHERE lower(name) = lower(:name) AND closed_on IS NOT DISTINCT FROM CAST(:closed_on AS date)
            )
        """), {"name": a["name"], "owner": a.get("owner"), "closed_on": a.get("closed_on"),
               "expires_on": a["expires_on"]}).rowcount
        counts["accounts"] += inserted

    # Names in the seed file refer to the open account when there is one (a default or a funding
    # card is always open - load_directory refuses otherwise).
    def id_for(name):
        return conn.execute(text("""
            SELECT id FROM accounts WHERE lower(name) = lower(:name)
            ORDER BY closed_on IS NOT NULL, closed_on DESC LIMIT 1
        """), {"name": name}).scalar()

    for p in seed.participants:
        if p.get("default_account"):
            conn.execute(text("""
                UPDATE participants SET default_account_id = :acct
                WHERE display_name = :name AND default_account_id IS NULL
            """), {"acct": id_for(p["default_account"]), "name": p["display_name"]})
    for a in seed.accounts:
        if a.get("funded_by"):
            conn.execute(text("""
                UPDATE accounts SET funded_by_account_id = :funder
                WHERE lower(name) = lower(:name) AND closed_on IS NOT DISTINCT FROM CAST(:closed_on AS date)
                  AND funded_by_account_id IS NULL
            """), {"funder": id_for(a["funded_by"]), "name": a["name"], "closed_on": a.get("closed_on")})

    # Trips last: members point at participants, which now all exist. Only the bot changes
    # participants' switches; seeding a trip must not start tagging anyone's entries.
    for t in seed.trips:
        counts["trips"] += conn.execute(text("""
            INSERT INTO trips (name, start_date, end_date)
            SELECT :name, :start_date, :end_date
            WHERE NOT EXISTS (SELECT 1 FROM trips WHERE lower(name) = lower(:name))
        """), {"name": t["name"], "start_date": t["start_date"], "end_date": t["end_date"]}).rowcount
        for person, family in t["members"]:
            counts["trip_members"] += conn.execute(text("""
                INSERT INTO trip_members (trip_id, participant_id, family)
                SELECT t.id, p.id, :family
                FROM trips t, participants p
                WHERE lower(t.name) = lower(:trip) AND p.display_name = :person
                ON CONFLICT (trip_id, participant_id) DO NOTHING
            """), {"trip": t["name"], "person": person, "family": family}).rowcount
    return counts



def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Seed the participants, accounts and trips tables")
    parser.add_argument("--seed", default=DEFAULT_SEED, help=f"seed file (default {DEFAULT_SEED})")
    parser.add_argument("--write", action="store_true", help="commit; without it everything is rolled back")
    args = parser.parse_args(argv)

    seed = parse_seed(json.loads(Path(args.seed).read_text(encoding="utf-8")))
    engine = get_engine()
    print(f"Database host: {engine.url.host}")
    print("WRITE - changes will be committed" if args.write else "DRY RUN - everything is rolled back at the end")

    with engine.connect() as conn:
        trans = conn.begin()
        counts = _seed(conn, seed)

        # Read everything back the way the bot will, inside the same transaction, before anything
        # is committed. A directory the bot would reject locks the whole household out, so a
        # --write that would produce one is rolled back instead (found by the final review).
        try:
            load_directory(conn)
        except DirectoryError as e:
            trans.rollback()
            print("\nNOT COMMITTED - the bot would refuse everyone with this directory:")
            for problem in str(e).split("; "):
                print(f"  - {problem}")
            return 1

        if args.write:
            trans.commit()
        else:
            trans.rollback()

    print(f"\nParticipants inserted: {counts['participants']}")
    print(f"Accounts inserted:     {counts['accounts']}")
    print(f"Household flags set:   {counts.get('household_flags', 0)}")
    # .get: a stubbed _seed in the tests returns only the first two counts.
    print(f"Trips inserted:        {counts.get('trips', 0)}")
    print(f"Trip members inserted: {counts.get('trip_members', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
