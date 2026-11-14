"""add trips: dated groups of participants in families, and a trip-mode switch per person

Step 1 of group-trip splitting (ROADMAP.md, Phase 1; ADR-0035, ADR-0036). All changes additive:

  - trips: one row per trip, with start and end dates. The dates sort `/trip`'s buttons and
    label them; they never tag an entry.
  - trip_members: who is on each trip, and which family they belong to *on that trip*. Family is
    stored here rather than on participants because it is the unit that settles up within a trip,
    and a person's grouping can differ between trips.
  - participants.active_trip_id: that person's trip mode. One column holds one value, so a person
    has at most one trip on. A composite foreign key to trip_members means they can only switch on
    a trip they are a member of; NULL (trip mode off) passes it (MATCH SIMPLE).
  - participants.trip_banner_message_id: the message pinned in their chat while trip mode is on,
    kept so the bot unpins the right one.
  - transactions.trip_id: the trip an entry was tagged with when it was logged, or NULL. Tagged by
    when the entry is logged, not by its date, so a booking paid before the trip still counts.

Additive, so safe to apply before the code that uses it merges: deployed code names its columns
and never sees the new ones. The code that reads trips must not deploy before this is applied, or
`/trip` fails and every card shows the trip warning.

No seed rows here: trip and member names are real people, and this repo is public. They come from
the gitignored seed file via scripts/seed_directory.py.

Revision ID: 0006_add_trips
Revises: 0005_drop_text_account_columns
Create Date: 2026-11-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0006_add_trips"
down_revision: Union[str, None] = "0005_drop_text_account_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trips",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.CheckConstraint("end_date >= start_date", name="ck_trips_dates"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # Case-insensitive names prevent duplicate choices in the seed file.
    op.create_index("uq_trips_name_ci", "trips", [sa.text("lower(name)")], unique=True)

    op.create_table(
        "trip_members",
        sa.Column("trip_id", sa.BigInteger(), sa.ForeignKey("trips.id"), primary_key=True),
        sa.Column("participant_id", sa.BigInteger(), sa.ForeignKey("participants.id"), primary_key=True),
        # A free-text label ("Alice's family"), the same for everyone who settles together.
        sa.Column("family", sa.Text(), nullable=False),
    )

    # One column means at most one trip on per person; NULL means trip mode is off.
    op.add_column("participants", sa.Column("active_trip_id", sa.BigInteger(), nullable=True))
    op.add_column("participants", sa.Column("trip_banner_message_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_participants_active_trip_member", "participants", "trip_members",
        ["active_trip_id", "id"], ["trip_id", "participant_id"], match="SIMPLE",
    )

    op.add_column(
        "transactions",
        sa.Column("trip_id", sa.BigInteger(), sa.ForeignKey("trips.id", name="fk_transactions_trip"), nullable=True),
    )


def downgrade() -> None:
    # Reverse order. Dropping the column drops its foreign key with it.
    op.drop_column("transactions", "trip_id")
    op.drop_constraint("fk_participants_active_trip_member", "participants", type_="foreignkey")
    op.drop_column("participants", "trip_banner_message_id")
    op.drop_column("participants", "active_trip_id")
    op.drop_table("trip_members")
    op.drop_index("uq_trips_name_ci", table_name="trips")
    op.drop_table("trips")
