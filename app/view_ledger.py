from sqlalchemy import text
from app.database import get_connection

def view_transactions():
    """Fetches and displays transactions in a human-readable format."""
    # Query the database
    # We select specific columns to keep the view clean
    with get_connection() as conn:
        rows = conn.execute(text(
            "SELECT date, description, amount, category FROM transactions ORDER BY date DESC"
        )).fetchall()

    if not rows:
        print("\n📭 The ledger is currently empty.")
        return

    # Table Header
    print("\n" + "="*65)
    print(f"{'Date':<12} | {'Description':<22} | {'Amount':<11} | {'Category'}")
    print("-" * 65)

    # Table Rows
    for date, desc, amt_cents, cat in rows:
        # Convert cents back to dollars only for the display layer
        amt_dollars = amt_cents / 100
        print(f"{date:<12} | {desc:<22} | ${amt_dollars:>10.2f} | {cat}")

    print("="*65 + "\n")

if __name__ == "__main__":
    view_transactions()