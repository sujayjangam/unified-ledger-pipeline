from app.database import get_connection

def get_recent_entries(limit=5):
    """Fetches the latest transactions, including both expenses and transfers."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT date, description, amount, currency, category 
            FROM transactions 
            ORDER BY date DESC, transaction_id DESC 
            LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"❌ Query Error: {e}")
        return []

def get_period_summary(start_date: str, end_date: str):
    """Fetches counts and totals grouped by currency, separating expenses and transfers."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Get Expenses grouped by currency
        cursor.execute('''
            SELECT currency, COUNT(*), SUM(amount) 
            FROM transactions 
            WHERE date BETWEEN ? AND ? AND category != 'Transfer'
            GROUP BY currency
        ''', (start_date, end_date))
        expenses = cursor.fetchall()
        
        # Get Transfers grouped by currency
        cursor.execute('''
            SELECT currency, COUNT(*), SUM(amount) 
            FROM transactions 
            WHERE date BETWEEN ? AND ? AND category = 'Transfer'
            GROUP BY currency
        ''', (start_date, end_date))
        transfers = cursor.fetchall()
        
        conn.close()
        return expenses, transfers
        
    except Exception as e:
        print(f"❌ Query Error: {e}")
        return [], []

def get_category_summary(start_date: str, end_date: str):
    """Fetches category statistics grouped by category AND currency, excluding transfers."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT category, currency, COUNT(*), SUM(amount)
            FROM transactions 
            WHERE date BETWEEN ? AND ? AND category != 'Transfer'
            GROUP BY category, currency
            ORDER BY category, currency
        ''', (start_date, end_date))
        
        rows = cursor.fetchall()
        conn.close()
        return rows
        
    except Exception as e:
        print(f"❌ Query Error: {e}")
        return []