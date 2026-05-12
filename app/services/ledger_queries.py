from database import get_connection

def get_recent_entries(limit=5):
    """Fetches just the latest X transactions."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT date, description, amount, category 
            FROM transactions 
            ORDER BY date DESC, id DESC 
            LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"❌ Query Error: {e}")
        return []

def get_period_summary(start_date: str, end_date: str):
    """Fetches all transactions and the total sum between two YYYY-MM-DD dates."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            --SELECT date, description, amount, category 
            SELECT DISTINCT category, COUNT(*) AS num_rows, SUM(amount) AS sum_amount
            FROM transactions 
            WHERE date BETWEEN ? AND ?
            GROUP BY 1
            ORDER BY date DESC, id DESC
        ''', (start_date, end_date))
        rows = cursor.fetchall()
        
        cursor.execute('''
            SELECT SUM(amount) 
            FROM transactions 
            WHERE date BETWEEN ? AND ?
        ''', (start_date, end_date))
        total_cents = cursor.fetchone()[0] or 0
        
        conn.close()
        return rows, total_cents / 100.0
        
    except Exception as e:
        print(f"❌ Query Error: {e}")
        return [], 0.0