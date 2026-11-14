"""require an account id and drop the superseded account and sender text columns

Migration 0004 introduced participants and accounts so transactions could point to stable ids
instead of duplicating names and Telegram user ids. The seed script linked older transactions to
those rows, and step 2 of issue #53 stops the bot, CLI and REST API writing the old text columns.
This contraction makes account_id required and removes account_desc, account_owner and entered_by,
leaving the directory as the source of their names and ownership.

Do not run this migration before the code that stops writing those columns (step 2) is deployed:
the old code still inserts into them and would fail after they are dropped. Take a pg_dump first
(gh workflow run backup.yml), and wait for the backup to complete successfully before upgrading.
The guard refuses to change the schema while any transaction still has no account_id. Give those
rows a card with the seed script's transaction_overrides, run from a commit before this one: this
revision's PR removed that part of the script, since it reads the columns dropped here.

Downgrade rebuilds the text from the current directory, not the original historical values. It
does not recover the old first-match account_owner guesses on shared accounts, nor the "Shared"
value CLI/API rows used. Shared accounts use the sender's display_name when known; otherwise
account_owner stays NULL. Renamed accounts or participants likewise use their current names.

Revision ID: 0005_drop_text_account_columns
Revises: 0004_participants_accounts
Create Date: 2026-11-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0005_drop_text_account_columns"
down_revision: Union[str, None] = "0004_participants_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    missing_accounts = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM transactions WHERE account_id IS NULL")
    ).scalar_one()
    if missing_accounts != 0:
        raise RuntimeError(
            f"{missing_accounts} transactions have NULL account_id; "
            "give each one a card first (see this revision's docstring)."
        )

    op.alter_column("transactions", "account_id", nullable=False)
    op.drop_column("transactions", "account_desc")
    op.drop_column("transactions", "account_owner")
    op.drop_column("transactions", "entered_by")


def downgrade() -> None:
    op.add_column("transactions", sa.Column("account_desc", sa.Text(), nullable=True))
    op.add_column("transactions", sa.Column("account_owner", sa.Text(), nullable=True))
    op.add_column("transactions", sa.Column("entered_by", sa.Text(), nullable=True))

    op.execute(sa.text("""
        UPDATE transactions AS t
        SET account_desc = a.name
        FROM accounts AS a
        WHERE t.account_id = a.id
    """))
    op.execute(sa.text("""
        UPDATE transactions AS t
        SET entered_by = p.telegram_user_id
        FROM participants AS p
        WHERE t.entered_by_participant_id = p.id
    """))
    op.execute(sa.text("""
        UPDATE transactions AS t
        SET account_owner = p.display_name
        FROM accounts AS a, participants AS p
        WHERE t.account_id = a.id
          AND p.id = CASE
              WHEN a.owner_participant_id IS NULL THEN t.entered_by_participant_id
              ELSE a.owner_participant_id
          END
    """))

    op.alter_column("transactions", "account_id", nullable=True)
