"""add participants and accounts: the people and cards the bot knows about, as rows

Until now the household lived in three env vars (ALLOWED_TG_IDS, ACCOUNT_OWNERS,
PRIMARY_ACCOUNT_OWNER), baked into the Cloud Run revision at start-up. Adding a card was a
redeploy, nothing checked that the three agreed, and a shared payment method was only "shared"
because its name appeared under two people - so the first-match lookup always landed on the same
person (issue #53). This migration gives both a table of their own:

  - participants: one row per person. telegram_user_id is nullable so someone can be tracked for
    expense splitting (e.g. on a trip) without being allowed to use the bot.
  - accounts: one row per card, wallet or cash. owner_participant_id NULL means shared: a payment
    on it belongs to whoever sent the message. closed_on retires a card without deleting it, so old
    transactions keep pointing at the real card. funded_by_account_id records which card tops up a
    wallet such as YouTrip, replacing PRIMARY_ACCOUNT_OWNER. expires_on records a card's printed
    expiry as the last day it is valid.

Why ids rather than names: a name can be renamed or reused by a replacement card, an id can't.
GENERATED ALWAYS AS IDENTITY means Postgres hands out every id, never reuses one, and refuses a
hand-typed one.

No seed rows here, deliberately: this repo is public and the rows are real names and card labels.
They come from a gitignored file via scripts/seed_directory.py.

Also adds two nullable columns to transactions - account_id and entered_by_participant_id - which
the seed script backfills and migration 0005 later makes the only record of them. Everything here is
additive: already-deployed code names its columns and never sees the new ones, so this is safe to
apply before the code that uses it merges.

Revision ID: 0004_participants_accounts
Revises: 0003_add_entered_by
Create Date: 2026-11-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0004_participants_accounts"
down_revision: Union[str, None] = "0003_add_entered_by"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # participants first, with default_account_id as a bare column: the two tables point at each
    # other (a person defaults to an account, an account is owned by a person), so one of the two
    # foreign keys can only be added once both tables exist - step 3 below.
    op.create_table(
        "participants",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=False, unique=True),
        sa.Column("telegram_user_id", sa.Text(), nullable=True, unique=True),
        sa.Column("default_account_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "accounts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("owner_participant_id", sa.BigInteger(), sa.ForeignKey("participants.id"), nullable=True),
        sa.Column("funded_by_account_id", sa.BigInteger(), sa.ForeignKey("accounts.id"), nullable=True),
        sa.Column("closed_on", sa.Date(), nullable=True),
        # The last day the card is valid (printed 09/29 -> 2029-09-30). NULL for anything without
        # one: cash, PayNow, a bank account. Recorded only; nothing acts on it yet.
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_foreign_key(
        "fk_participants_default_account", "participants", "accounts", ["default_account_id"], ["id"]
    )

    # Two OPEN accounts can't share a name, ignoring case: the bot matches the name the model
    # returns case-insensitively, so "Card A" and "card a" both open would be ambiguous. Closed
    # accounts are left out, so a replacement card may reuse its predecessor's name.
    op.create_index(
        "uq_accounts_open_name_ci",
        "accounts",
        [sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("closed_on IS NULL"),
    )

    # Every foreign key keeps the default ON DELETE (no action): a participant or account that
    # anything points at can't be deleted. Closing an account is the only way to retire one.
    op.add_column(
        "transactions",
        sa.Column("account_id", sa.BigInteger(), sa.ForeignKey("accounts.id", name="fk_transactions_account"), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column(
            "entered_by_participant_id",
            sa.BigInteger(),
            sa.ForeignKey("participants.id", name="fk_transactions_entered_by_participant"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    # Reverse order. Dropping a column drops its foreign key with it.
    op.drop_column("transactions", "entered_by_participant_id")
    op.drop_column("transactions", "account_id")
    op.drop_index("uq_accounts_open_name_ci", table_name="accounts")
    op.drop_constraint("fk_participants_default_account", "participants", type_="foreignkey")
    op.drop_table("accounts")
    op.drop_table("participants")
