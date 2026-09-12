"""add entered_by: who sent the message that created the row

Adds `entered_by`, the Telegram user ID of the household member whose message created the row
(issue #60). Until now the sender was known at save time - process_expense_text looks it up in
ALLOWED_TG_IDS to pick payment defaults - and then thrown away. So no row could be traced back to
who entered it, and `account_owner` (whose card paid, inferred by first match) was the only
person column.

Why the Telegram user ID, and not the username or display name (ADR-0026):
  - Telegram's Bot API documents `id` as the user's unique identifier. `username` is optional,
    can be removed, and a released username can later be claimed by someone else.
  - A display name is whatever ALLOWED_TG_IDS says today; renaming someone would leave old rows
    spelled the old way. Names are looked up from the ID when displayed instead.
  - When #53 moves household members into Postgres, an ID maps onto that table exactly.

Stored as TEXT, in the same string form ALLOWED_TG_IDS uses for its keys.

Existing rows stay NULL, meaning "sender not recorded". They are deliberately NOT backfilled from
`account_owner`: a read-only check of production on 2026-09-10 found 16 of 47 rows name no person
there at all, and the first-match rule attributes shared payment methods to one member - so a
backfill would store guesses that look exactly like real values. Rows written by the CLI and the
REST API also leave it NULL; they aren't entered by a Telegram user.

Nullable, with no default and no backfill: on Postgres this only changes the table's metadata, and
already-deployed code (which names its columns) can't see it. It must still be applied BEFORE the
code that writes it deploys, or that code's INSERT would name a column that doesn't exist yet.

Revision ID: 0003_add_entered_by
Revises: 0002_add_created_at
Create Date: 2026-09-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003_add_entered_by"
down_revision: Union[str, None] = "0002_add_created_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("entered_by", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "entered_by")
