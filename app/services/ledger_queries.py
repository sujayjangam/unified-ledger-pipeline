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
