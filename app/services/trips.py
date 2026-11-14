"""Trips: which trip, if any, new entries are tagged with (group-trip splitting, step 1).

A trip is a named group of participants in families (migration 0006). At most one trip is
active at a time - the database enforces it with a partial unique index - and it is one setting
for everyone who logs, switched with `/trip on <name>` and `/trip off` in the bot.

An entry is tagged by when it is *logged*: process_expense_text reads the active trip for every
message, the same way authorize() reads the directory, so a /trip switch takes effect on the
next message and a booking paid weeks before the trip still counts as the trip's.

Like participants.py, these functions take a connection and do nothing else, so tests can hand
them a fake one; bot_core owns opening the connection.
"""

from dataclasses import dataclass

from sqlalchemy import text


@dataclass(frozen=True)
class Trip:
    id: int
    name: str


def load_active_trip(conn) -> Trip | None:
    """The active trip, or None when no trip is on."""
    row = conn.execute(text("SELECT id, name FROM trips WHERE active")).first()
    return Trip(*row) if row else None


def trip_names(conn) -> list[str]:
    """Every trip's name, oldest first: the choices `/trip on` offers when a name doesn't match."""
    return [row[0] for row in conn.execute(text("SELECT name FROM trips ORDER BY id")).all()]


def switch_trip_on(conn, name: str) -> Trip | None:
    """Makes the trip with this name (case and outer spaces ignored) the active one, turning off
    whichever trip was active before, and commits. Returns the trip now active, or None - with
    nothing changed - when no trip has that name.

    Two UPDATEs, not one: Postgres checks a unique index row by row as an UPDATE goes, so a single
    statement that turns one trip off and another on could briefly see two active rows and fail.
    Turning the old one off first, inside the same transaction, means the switch either happens
    completely or not at all.
    """
    row = conn.execute(
        text("SELECT id, name FROM trips WHERE lower(name) = lower(:name)"), {"name": name.strip()}
    ).first()
    if row is None:
        return None
    trip = Trip(*row)
    conn.execute(text("UPDATE trips SET active = false WHERE active AND id <> :id"), {"id": trip.id})
    conn.execute(text("UPDATE trips SET active = true WHERE id = :id"), {"id": trip.id})
    conn.commit()
    return trip


def switch_trip_off(conn) -> Trip | None:
    """Turns the active trip off and commits. Returns the trip that was on, or None if none was."""
    row = conn.execute(text("UPDATE trips SET active = false WHERE active RETURNING id, name")).first()
    conn.commit()
    return Trip(*row) if row else None
