"""add whole-cent shares for each person on a split entry

No rows means the entry is the payer's alone. Shares and the entry are written together,
so a failed save cannot leave half a split behind.

Additive: apply before deploying code that writes split shares.

Revision ID: 0008_add_transaction_splits
Revises: 0007_add_paid_by
Create Date: 2026-11-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_add_transaction_splits"
down_revision: Union[str, None] = "0007_add_paid_by"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Keep one nonnegative share per person, removed when its entry is removed."""
    op.create_table(
        "transaction_splits",
        sa.Column("transaction_id", sa.Text(), sa.ForeignKey("transactions.transaction_id", ondelete="CASCADE"), nullable=False),
        sa.Column("participant_id", sa.BigInteger(), sa.ForeignKey("participants.id"), nullable=False),
        sa.Column("share_cents", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("share_cents >= 0", name="ck_transaction_splits_share_not_negative"),
        sa.PrimaryKeyConstraint("transaction_id", "participant_id"),
    )


def downgrade() -> None:
    """Remove shares without changing the entries they belonged to."""
    op.drop_table("transaction_splits")
