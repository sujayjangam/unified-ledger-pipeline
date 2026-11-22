"""Optional spreadsheet destination per trip.

Revision ID: 0012_trip_sheet_id
Revises: 0011_settlement_bills
Create Date: 2026-11-22
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_trip_sheet_id"
down_revision = "0011_settlement_bills"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add an optional id, rejecting URLs before they can become write targets."""
    op.add_column("trips", sa.Column("sheet_id", sa.Text(), nullable=True))
    op.create_check_constraint("ck_trips_sheet_id", "trips",
                               "sheet_id IS NULL OR sheet_id ~ '^[A-Za-z0-9_-]{20,}$'")


def downgrade() -> None:
    """Remove only the sheet destination and its validation."""
    op.drop_constraint("ck_trips_sheet_id", "trips", type_="check")
    op.drop_column("trips", "sheet_id")
