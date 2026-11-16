"""Per-person trip switches, read fresh when an entry is logged, not by its business date.

Functions take a connection so the bot owns I/O and tests can use fakes. The participant's
single active_trip_id survives restarts; its composite foreign key requires membership.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text


@dataclass(frozen=True)
class Trip:
    id: int
    name: str
    start_date: date
    end_date: date
    member_ids: tuple[int, ...] = ()
    families: tuple[tuple[int, str], ...] = ()


def trip_families(conn, trip_id: int) -> tuple[tuple[int, str], ...]:
    """Read members and family labels together for payer and split matching."""
    rows = conn.execute(text("""
        SELECT participant_id, family FROM trip_members WHERE trip_id = :trip_id
        ORDER BY participant_id
    """), {"trip_id": trip_id}).all()
    return tuple((row[0], row[1]) for row in rows)


def load_active_trip(conn, participant_id: int) -> Trip | None:
    """Read only this person's switch, so another member can log everyday expenses."""
    row = conn.execute(text("""
        SELECT t.id, t.name, t.start_date, t.end_date
        FROM participants p JOIN trips t ON t.id = p.active_trip_id
        WHERE p.id = :participant_id
    """), {"participant_id": participant_id}).first()
    return Trip(*row) if row else None


def member_trips(conn, participant_id: int, offset: int, limit: int) -> tuple[list[Trip], bool]:
    """Fetch one extra choice to decide whether the menu needs an Older button."""
    rows = conn.execute(text("""
        SELECT t.id, t.name, t.start_date, t.end_date
        FROM trips t JOIN trip_members m ON m.trip_id = t.id
        WHERE m.participant_id = :participant_id
        ORDER BY t.start_date DESC, t.id DESC LIMIT :limit OFFSET :offset
    """), {"participant_id": participant_id, "offset": offset, "limit": limit + 1}).all()
    return [Trip(*row) for row in rows[:limit]], len(rows) > limit


def switch_trip_on(conn, participant_id: int, trip_id: int) -> Trip | None:
    """Atomically check membership and change only this person's switch, then commit."""
    row = conn.execute(text("""
        UPDATE participants p SET active_trip_id = t.id
        FROM trips t JOIN trip_members m ON m.trip_id = t.id
        WHERE p.id = :participant_id AND m.participant_id = p.id AND t.id = :trip_id
        RETURNING t.id, t.name, t.start_date, t.end_date
    """), {"participant_id": participant_id, "trip_id": trip_id}).first()
    conn.commit()
    return Trip(*row) if row else None


def switch_trip_off(conn, participant_id: int) -> Trip | None:
    """Return the trip cleared by this update, without a separate read that could race."""
    row = conn.execute(text("""
        UPDATE participants p SET active_trip_id = NULL
        FROM trips t WHERE p.id = :participant_id AND t.id = p.active_trip_id
        RETURNING t.id, t.name, t.start_date, t.end_date
    """), {"participant_id": participant_id}).first()
    conn.commit()
    return Trip(*row) if row else None


def banner_message_id(conn, participant_id: int) -> int | None:
    """Remember the exact banner so switching never unpins an unrelated chat message."""
    return conn.execute(text("""
        SELECT trip_banner_message_id FROM participants WHERE id = :participant_id
    """), {"participant_id": participant_id}).scalar()


def set_banner_message_id(conn, participant_id: int, id_or_none: int | None):
    """Persist banner bookkeeping separately: Telegram failures must not undo a switch."""
    conn.execute(text("""
        UPDATE participants SET trip_banner_message_id = :message_id WHERE id = :participant_id
    """), {"participant_id": participant_id, "message_id": id_or_none})
    conn.commit()


def format_trip_dates(trip: Trip, today: date) -> str:
    """A trip's dates, short enough for a button: "3–9 Mar", "27 Apr–4 May".

    The year is left out when both dates are in this year, which is the usual case. Otherwise
    it is added: once at the end when the trip sits inside one year ("27 Apr–4 May 2027"), and
    on both dates when it crosses New Year ("28 Dec 2025–4 Jan 2026"), because a single year at
    the end would make the start read as the wrong December.
    """
    start, end = trip.start_date, trip.end_date
    # Month names spelled out here, not strftime("%b"), so the label doesn't change with the
    # server's locale.
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    end_text = f"{end.day} {months[end.month - 1]}"
    if start.year != end.year:
        return f"{start.day} {months[start.month - 1]} {start.year}–{end_text} {end.year}"
    start_text = str(start.day)
    if start.month != end.month:
        start_text += f" {months[start.month - 1]}"
    result = f"{start_text}–{end_text}"
    if end.year != today.year:
        result += f" {end.year}"
    return result
