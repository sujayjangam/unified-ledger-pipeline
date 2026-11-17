"""Record paybacks separately from spending (ADR-0046).

Revision ID: 0009_add_settlements
Revises: 0008_add_transaction_splits
Create Date: 2026-11-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_add_settlements"
down_revision = "0008_add_transaction_splits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Keep the debt cleared and the money handed over on the same record."""
    op.create_table(
        "settlements",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("trip_id", sa.BigInteger(), sa.ForeignKey("trips.id")),
        sa.Column("from_participant_id", sa.BigInteger(), sa.ForeignKey("participants.id"), nullable=False),
        sa.Column("to_participant_id", sa.BigInteger(), sa.ForeignKey("participants.id"), nullable=False),
        sa.Column("for_participant_id", sa.BigInteger(), sa.ForeignKey("participants.id")),
        sa.Column("clears_cents", sa.BigInteger(), nullable=False),
        sa.Column("clears_currency", sa.Text(), nullable=False),
        sa.Column("paid_cents", sa.BigInteger(), nullable=False),
        sa.Column("paid_currency", sa.Text(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("entered_by_participant_id", sa.BigInteger(), sa.ForeignKey("participants.id")),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.CheckConstraint("from_participant_id <> to_participant_id", name="ck_settlements_different_people"),
        sa.CheckConstraint("for_participant_id <> to_participant_id", name="ck_settlements_different_beneficiary"),
        sa.CheckConstraint("clears_cents > 0", name="ck_settlements_positive_clears"),
        sa.CheckConstraint("paid_cents > 0", name="ck_settlements_positive_paid"),
        sa.CheckConstraint("clears_currency <> paid_currency OR clears_cents = paid_cents",
                           name="ck_settlements_same_currency_amount"),
        sa.UniqueConstraint("idempotency_key", name="uq_settlements_idempotency_key"),
    )
    op.create_index("ix_settlements_trip_id", "settlements", ["trip_id"])


def downgrade() -> None:
    """Remove paybacks without changing any bill."""
    op.drop_table("settlements")
