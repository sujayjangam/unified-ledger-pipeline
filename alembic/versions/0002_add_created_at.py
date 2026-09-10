"""add created_at write-time timestamp

Adds `created_at`, the time a row was *written*, alongside `date`, the business date a spend
*happened* (issue #9). Until now the two were conflated: `date` has no time component and
`transaction_id` is a random UUID, so nothing recorded the order rows were written in. See
docs/SCHEMA.md ("Business date vs. write time") and ADR-0024.

Backfill policy for pre-existing rows (issue #11)
------------------------------------------------
Existing rows get midnight Singapore time (UTC+8) on their own `date`:

    created_at = (date || ' 00:00:00+08')::timestamptz

Chosen over the two alternatives #11 listed:
  - the migration-run time (what DEFAULT now() does on its own) would give every existing row the
    same instant, so they would come back from ORDER BY created_at in arbitrary order among
    themselves;
  - NULL would contradict #10's NOT NULL requirement, and force NULLS LAST handling onto every
    query that orders by this column, forever.
It is accurate to the day: until backdated entries exist (#15), every row's `date` is the day it
was sent. The exact 00:00:00+08 is the marker that the time-of-day part is synthetic.

Checked before writing, read-only against production on 2026-09-10: all 47 rows had a well-formed
YYYY-MM-DD `date`, and the cast above succeeded on every one of them.

Why three steps instead of one add_column(..., server_default=now(), nullable=False): Postgres
fills every existing row with the default as the column is added, which would be the migration-run
time - the rejected option above. So the column is added nullable with no default, backfilled, and
only then made NOT NULL with the default.

Postgres runs the whole migration as one transaction, so if the backfill fails on any row, the
column addition rolls back with it rather than leaving a half-migrated table.

Revision ID: 0002_add_created_at
Revises: 0001_create_transactions
Create Date: 2026-09-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002_add_created_at"
down_revision: Union[str, None] = "0001_create_transactions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Nullable and no default, so existing rows briefly hold NULL rather than being stamped
    #    with the migration-run time.
    op.add_column(
        "transactions",
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # 2. Backfill: midnight SGT on each row's own date (policy in the module docstring).
    op.execute("UPDATE transactions SET created_at = (date || ' 00:00:00+08')::timestamptz")

    # 3. From here on the database sets it, and every row must have one. No application code
    #    ever supplies created_at - every writer names its columns and omits this one.
    op.alter_column(
        "transactions",
        "created_at",
        nullable=False,
        server_default=sa.text("now()"),
    )


def downgrade() -> None:
    op.drop_column("transactions", "created_at")
