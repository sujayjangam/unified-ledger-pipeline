from sqlalchemy import text
from app.database import get_connection

def get_recent_entries(limit=5):
    """Fetches the latest transactions, including both expenses and transfers."""
    try:
        with get_connection() as conn:
            result = conn.execute(text('''
                SELECT date, description, amount, currency, category
                FROM transactions
                ORDER BY date DESC, transaction_id DESC
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
