"""create transactions table

Baseline schema for the Postgres migration. This reflects the *actual* live SQLite schema as of
the cutover, which is the CREATE TABLE in the old app/database.py plus the `account_desc` column
that was added later by a manual ALTER TABLE (run via the old root-level test_queries.py) and
never codified. Column order below matches the live table's real order.

Types are kept faithful to the SQLite original rather than "improved" here:
  - `date` stays TEXT because every query filters it with string range comparisons
    (WHERE date BETWEEN :start AND :end) on ISO-8601 'YYYY-MM-DD' values. Converting it to a real
    DATE column is a separate, deliberate change.
  - money columns are integer cents (see docs/SCHEMA.md), never floats.

Revision ID: 0001_create_transactions
Revises:
Create Date: 2026-08-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0001_create_transactions"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "transactions",
        sa.Column("transaction_id", sa.Text(), nullable=False),
        sa.Column("date", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=True),
        sa.Column("base_amount", sa.BigInteger(), nullable=False),
        sa.Column("account_owner", sa.Text(), nullable=True),
        sa.Column("benefit_of", sa.Text(), nullable=True),
        sa.Column("split_ratio", sa.Float(), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("transaction_type", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("reconciliation_status", sa.Text(), nullable=True),
        sa.Column("account_desc", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("transaction_id", name="pk_transactions"),
        # add_expense() relies on this constraint for its ON CONFLICT DO NOTHING dedupe.
        # NULLs never collide, so CLI/API callers that omit the key are unaffected.
        sa.UniqueConstraint("idempotency_key", name="uq_transactions_idempotency_key"),
    )


def downgrade() -> None:
    op.drop_table("transactions")
