from sqlalchemy import text
from app.database import get_connection

def get_recent_entries(limit=5):
    """Fetches the most recently *entered* transactions, including both expenses and transfers.

    Ordered by created_at (when the row was written), not date (when the spend happened) - see
    docs/SCHEMA.md. "Recent" means "what was just logged": once backdated entries exist (#15), an
    expense from last week that was logged a minute ago is still recent. transaction_id is a
    random UUID, so as a tiebreaker it carries no meaning of its own; it only keeps rows that
    share a created_at in a stable order, and rows that predate the column all share their
    date's midnight (see alembic/versions/0002_add_created_at.py).
    """
    try:
        with get_connection() as conn:
            result = conn.execute(text('''
                SELECT date, description, amount, currency, category
                FROM transactions
                ORDER BY created_at DESC, transaction_id DESC
                LIMIT :limit
            '''), {"limit": limit})
            return result.fetchall()
    except Exception as e:
        print(f"❌ Query Error: {e}")
        return []

def find_recent_duplicate(amount_cents: int, currency: str, within_minutes: int):
    """Returns the newest entry with exactly this amount and currency saved in the last
    `within_minutes` minutes, or None if there isn't one.

    Backs the duplicate warning (#58, ADR-0027). The bot calls this after extraction and before
    showing the confirmation card, so the user can decide while nothing has been saved yet.

    - Amount + currency only, never description: description is the raw input, and Whisper
      words the same spoken sentence differently each time, so a description match would rarely
      fire on voice notes.
    - Anyone's entry, not just the sender's: two people logging the same shared bill is exactly
      the case worth catching.
    - Windowed on created_at (write time), never date, which has no time of day - see
      docs/SCHEMA.md. Both now() and created_at come from the database clock, so the clock of
      whichever machine runs the bot never enters into it.

    The result is a dict of description, seconds_ago and entered_by (None for rows written
    before that column existed, or by the CLI/API). Any error also returns None, deliberately:
    a broken check must never stop an entry being saved, so the caller just shows a normal card.
    """
    try:
        with get_connection() as conn:
            row = conn.execute(text('''
                SELECT description,
                       CAST(EXTRACT(EPOCH FROM (now() - created_at)) AS integer) AS seconds_ago,
                       entered_by
                FROM transactions
                WHERE amount = :amount
                  AND currency = :currency
                  AND created_at >= now() - make_interval(mins => CAST(:within_minutes AS integer))
                ORDER BY created_at DESC
                LIMIT 1
            '''), {"amount": amount_cents, "currency": currency, "within_minutes": within_minutes}).mappings().first()
            return dict(row) if row else None
    except Exception as e:
        print(f"❌ Duplicate check failed, showing a normal card: {e}")
        return None

def get_period_summary(start_date: str, end_date: str):
    """Fetches counts and totals grouped by currency, separating expenses and transfers."""
    try:
        with get_connection() as conn:
            # Get Expenses grouped by currency
            expenses = conn.execute(text('''
                SELECT currency, COUNT(*), SUM(amount)
                FROM transactions
                WHERE date BETWEEN :start_date AND :end_date AND category != 'Transfer'
                GROUP BY currency
            '''), {"start_date": start_date, "end_date": end_date}).fetchall()

            # Get Transfers grouped by currency
            transfers = conn.execute(text('''
                SELECT currency, COUNT(*), SUM(amount)
                FROM transactions
                WHERE date BETWEEN :start_date AND :end_date AND category = 'Transfer'
                GROUP BY currency
            '''), {"start_date": start_date, "end_date": end_date}).fetchall()

            return expenses, transfers

    except Exception as e:
        print(f"❌ Query Error: {e}")
        return [], []

def get_category_summary(start_date: str, end_date: str):
    """Fetches category statistics grouped by category AND currency, excluding transfers."""
    try:
        with get_connection() as conn:
            result = conn.execute(text('''
                SELECT category, currency, COUNT(*), SUM(amount)
                FROM transactions
                WHERE date BETWEEN :start_date AND :end_date AND category != 'Transfer'
                GROUP BY category, currency
                ORDER BY category, currency
            '''), {"start_date": start_date, "end_date": end_date})

            return result.fetchall()

    except Exception as e:
        print(f"❌ Query Error: {e}")
        return []
