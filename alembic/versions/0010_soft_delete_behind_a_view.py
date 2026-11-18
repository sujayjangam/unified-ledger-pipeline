"""mark deleted entries, and hide them behind a view named transactions (ADR-0050, #115)

The table is renamed raw_transactions and gains two columns:
  - deleted_at: NULL means live. Set by a delete from /recent, cleared by its undo.
  - deleted_by_participant_id: who deleted it. Only a deleted row may have one.

A view named transactions then shows only the live rows, with exactly the old columns. Every
existing query, insert and join says "transactions", so it now reads the view and never sees
a deleted entry: the totals, /trip_bals and the duplicate check all drop it with no change.
Inserts go through the view too (a simple view is updatable in Postgres), and the unique
index on idempotency_key stays on the real table, so a deleted row still blocks a retry.

Only the delete and undo queries name raw_transactions.

Safe to apply before the code that uses it: old code reads and writes "transactions", which
still exists, and sees every row, since nothing is deleted yet. The rename and the view are in
one migration, and Postgres runs DDL in a transaction, so there is never a moment with no
"transactions" at all.

Foreign keys pointing at the table (transaction_splits.transaction_id) follow the rename by
themselves: Postgres links them to the table, not to its name.

Changing a column later: add it to raw_transactions, then to the view with CREATE OR REPLACE
VIEW. That command can only add columns at the end; dropping, renaming or retyping one means
DROP VIEW and CREATE VIEW in the same migration.

Revision ID: 0010_soft_delete
Revises: 0009_add_settlements
Create Date: 2026-11-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_soft_delete"
down_revision: Union[str, None] = "0009_add_settlements"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The columns the view shows: every column the table had before this migration, in the
# table's own order. deleted_at and deleted_by_participant_id are left out on purpose: a live
# row has no use for them, and leaving them out means no code can read or set them by
# accident through the view.
LIVE_COLUMNS = (
    "transaction_id",
    "date",
    "description",
    "amount",
    "currency",
    "base_amount",
    "benefit_of",
    "split_ratio",
    "category",
    "transaction_type",
    "source",
    "reconciliation_status",
    "idempotency_key",
    "created_at",
    "account_id",
    "entered_by_participant_id",
    "trip_id",
    "paid_by_participant_id",
)


def upgrade() -> None:
    """Rename the table, add the two delete columns, and put the live-rows view in its place."""
    op.rename_table("transactions", "raw_transactions")

    op.add_column("raw_transactions", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("raw_transactions", sa.Column("deleted_by_participant_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_raw_transactions_deleted_by", "raw_transactions", "participants",
        ["deleted_by_participant_id"], ["id"],
    )
    # A live row has no deleter. The reverse is allowed (deleted, deleter not recorded), so a
    # row marked deleted by hand in the Neon console still passes.
    op.create_check_constraint(
        "ck_raw_transactions_deleter_only_when_deleted", "raw_transactions",
        "deleted_at IS NOT NULL OR deleted_by_participant_id IS NULL",
    )

    # A plain SELECT ... WHERE from one table: Postgres makes that view updatable by itself, so
    # INSERT INTO transactions, ON CONFLICT and RETURNING keep working, and a column left out
    # of an insert (created_at) still gets the real table's default.
    op.execute(
        f"CREATE VIEW transactions AS SELECT {', '.join(LIVE_COLUMNS)} "
        "FROM raw_transactions WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    """Put the plain table back, but only if nothing is deleted.

    Dropping deleted_at would turn every deleted entry back into a live one, silently, and it
    would count in the totals and /trip_bals again. So a downgrade with deleted rows stops and
    says so; clear them first (the purge query in docs/LOCAL_TESTING.md) if that is meant.
    """
    deleted = op.get_bind().execute(
        sa.text("SELECT count(*) FROM raw_transactions WHERE deleted_at IS NOT NULL")
    ).scalar()
    if deleted:
        raise RuntimeError(
            f"{deleted} deleted entries would come back as live ones. Purge them first, "
            "or leave this migration in place."
        )

    op.execute("DROP VIEW transactions")
    op.drop_constraint("ck_raw_transactions_deleter_only_when_deleted", "raw_transactions", type_="check")
    op.drop_constraint("fk_raw_transactions_deleted_by", "raw_transactions", type_="foreignkey")
    op.drop_column("raw_transactions", "deleted_by_participant_id")
    op.drop_column("raw_transactions", "deleted_at")
    op.rename_table("raw_transactions", "transactions")
