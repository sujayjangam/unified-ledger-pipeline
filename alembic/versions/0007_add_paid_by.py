"""add household membership and the person who paid

  - participants.is_household defaults to false for outside trip members. Existing bot
    users are marked household before the check requires that for every Telegram user.
  - transactions.paid_by_participant_id records a person separately from the sender.
    NULL means "not recorded" (old rows, CLI, REST API), read as the account's owner,
    or the sender for an unowned account. No old transactions are backfilled.

Additive: apply before deploying code that selects the household column.

Revision ID: 0007_add_paid_by
Revises: 0006_add_trips
Create Date: 2026-11-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision: str = "0007_add_paid_by"
down_revision: Union[str, None] = "0006_add_trips"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("participants", sa.Column("is_household", sa.Boolean(), nullable=False, server_default=sa.false()))
    # Backfill first: the check must also accept bot users who already exist.
    op.execute("UPDATE participants SET is_household = true WHERE telegram_user_id IS NOT NULL")
    op.create_check_constraint(
        "ck_participants_bot_user_is_household", "participants", "telegram_user_id IS NULL OR is_household",
    )
    op.add_column("transactions", sa.Column("paid_by_participant_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_transactions_paid_by", "transactions", "participants", ["paid_by_participant_id"], ["id"],
    )


def downgrade() -> None:
    # Reverse dependency order before removing either column.
    op.drop_constraint("fk_transactions_paid_by", "transactions", type_="foreignkey")
    op.drop_column("transactions", "paid_by_participant_id")
    op.drop_constraint("ck_participants_bot_user_is_household", "participants", type_="check")
    op.drop_column("participants", "is_household")
