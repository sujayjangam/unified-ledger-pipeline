"""Agreed SGD rates per trip.

Revision ID: 0013_trip_fx_rates
Revises: 0012_trip_sheet_id
Create Date: 2026-11-22
"""
from alembic import op
import sqlalchemy as sa

revision = '0013_trip_fx_rates'
down_revision = '0012_trip_sheet_id'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add rates without changing any existing debts or settlements."""
    op.create_table(
        'trip_fx_rates',
        sa.Column('trip_id', sa.BigInteger(), sa.ForeignKey('trips.id'), nullable=False),
        sa.Column('paid_currency', sa.Text(), nullable=False),
        sa.Column('currency', sa.Text(), nullable=False),
        sa.Column('rate', sa.Numeric(), nullable=False),
        sa.Column('rate_date', sa.Date(), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('set_by_participant_id', sa.BigInteger(), sa.ForeignKey('participants.id')),
        sa.Column('set_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('trip_id', 'paid_currency', 'currency'),
        sa.CheckConstraint('rate > 0', name='ck_trip_fx_positive'),
        sa.CheckConstraint("source IN ('frankfurter', 'typed')", name='ck_trip_fx_source'),
        sa.CheckConstraint('currency <> paid_currency', name='ck_trip_fx_different'),
    )


def downgrade() -> None:
    """Remove only the trip agreements."""
    op.drop_table('trip_fx_rates')
