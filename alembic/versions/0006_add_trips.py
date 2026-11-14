"""add trips: a named group of participants in families, at most one active at a time

Step 1 of group-trip splitting (ROADMAP.md, Phase 1). Three changes, all additive:

  - trips: one row per trip. `active` marks the trip new entries are tagged with. It is one
    setting shared by everyone who logs, not per sender, so both people logging on a trip tag to
    the same trip. A partial unique index lets at most one row be active: the database, not the
    bot, guarantees there is never a choice between two active trips.
  - trip_members: who is on each trip, and which family they belong to *on that trip*. Family is
    stored here rather than on participants because it is the unit that settles up within a
    trip (steps 3 and 4 - splits roll up to family, families settle by default), and a person's
    grouping can differ between trips.
  - transactions.trip_id: the trip an entry was tagged with when it was logged, or NULL for an
    everyday entry. Tagged by when the entry is logged, not by its date, so a booking paid
    before the trip still counts.

Additive only, so it is safe to apply before the code that uses it merges: deployed code names
its columns, never sees the new ones, and leaves trip_id NULL. The code that reads trips must
not deploy before this is applied, or the /trip command fails.

No seed rows here: trip and member names are real people, and this repo is public. They come
from the gitignored seed file via scripts/seed_directory.py.

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
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # `/trip on <name>` matches the name ignoring case, so two trips differing only in case
    # would be ambiguous.
    op.create_index("uq_trips_name_ci", "trips", [sa.text("lower(name)")], unique=True)

    # At most one active trip. Every active row has the same value (true) in the indexed column,
    # so a second one would be a duplicate; inactive rows are left out by the WHERE, so any
    # number of trips can be inactive.
    op.create_index(
        "uq_trips_one_active",
        "trips",
        ["active"],
        unique=True,
        postgresql_where=sa.text("active"),
    )

    op.create_table(
        "trip_members",
        sa.Column("trip_id", sa.BigInteger(), sa.ForeignKey("trips.id"), primary_key=True),
        sa.Column("participant_id", sa.BigInteger(), sa.ForeignKey("participants.id"), primary_key=True),
        # A free-text label ("Alice's family"), the same for everyone who settles together.
        sa.Column("family", sa.Text(), nullable=False),
    )

    op.add_column(
        "transactions",
        sa.Column("trip_id", sa.BigInteger(), sa.ForeignKey("trips.id", name="fk_transactions_trip"), nullable=True),
    )


def downgrade() -> None:
    # Reverse order. Dropping the column drops its foreign key with it.
    op.drop_column("transactions", "trip_id")
    op.drop_table("trip_members")
    op.drop_index("uq_trips_one_active", table_name="trips")
    op.drop_index("uq_trips_name_ci", table_name="trips")
    op.drop_table("trips")
