"""Link paybacks to bill shares and reserve reference-rate fields (ADR-0052/0053).

Revision ID: 0011_settlement_bills
Revises: 0010_soft_delete
Create Date: 2026-11-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_settlement_bills"
down_revision = "0010_soft_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Keep bill allocations with their payback, including after a bill is soft-deleted."""
    op.create_table(
        "settlement_bills",
        sa.Column("settlement_id", sa.BigInteger(), sa.ForeignKey("settlements.id"), nullable=False),
        # A foreign key needs the backing table, not the live-transactions view.
        sa.Column("transaction_id", sa.Text(), sa.ForeignKey("raw_transactions.transaction_id"), nullable=False),
        sa.Column("for_participant_id", sa.BigInteger(), sa.ForeignKey("participants.id"), nullable=False),
        sa.Column("cents", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("settlement_id", "transaction_id", "for_participant_id"),
        sa.CheckConstraint("cents > 0", name="ck_settlement_bills_positive_cents"),
    )
    op.create_index("ix_settlement_bills_transaction_id", "settlement_bills", ["transaction_id"])
    op.add_column("settlements", sa.Column("fx_rate", sa.Numeric()))
    op.add_column("settlements", sa.Column("fx_rate_date", sa.Date()))
    op.add_column("settlements", sa.Column("fx_source", sa.Text()))
    op.add_column("settlements", sa.Column("needs_statement_check", sa.Boolean(),
                                         nullable=False, server_default=sa.false()))
    op.create_check_constraint("ck_settlements_positive_fx_rate", "settlements",
                               "fx_rate IS NULL OR fx_rate > 0")
    op.create_check_constraint(
        "ck_settlements_fx_all_or_none", "settlements",
        "(fx_rate IS NULL AND fx_rate_date IS NULL AND fx_source IS NULL) OR "
        "(fx_rate IS NOT NULL AND fx_rate_date IS NOT NULL AND fx_source IS NOT NULL)",
    )


def downgrade() -> None:
    """Remove allocations and reserved rate fields without changing the paybacks."""
    op.drop_constraint("ck_settlements_fx_all_or_none", "settlements", type_="check")
    op.drop_constraint("ck_settlements_positive_fx_rate", "settlements", type_="check")
    for column in ("needs_statement_check", "fx_source", "fx_rate_date", "fx_rate"):
        op.drop_column("settlements", column)
    op.drop_table("settlement_bills")
