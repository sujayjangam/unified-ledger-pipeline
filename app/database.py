import sqlite3
import os

# Define the path to our database file
DB_PATH = os.path.join('data', 'ledger.db')

def initialize_db():
    # 1. Connect to the database (it creates the file if it doesn't exist)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 2. Create the table based on SCHEMA.md v1.3
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id TEXT PRIMARY KEY,
            date TEXT NOT NULL,
            description TEXT,
            amount INTEGER NOT NULL,
            transaction_currency TEXT,
            base_amount INTEGER NOT NULL,
            account_owner TEXT,
            benefit_of TEXT,
            split_ratio REAL,
            category TEXT,
            transaction_type TEXT,
            source TEXT,
            reconciliation_status TEXT
        )
    ''')

    # 3. Save changes and close
    conn.commit()
    conn.close()
    print(f"✅ Database initialized at {DB_PATH}")

# without this block, everytime this file is imported, initialize_db() will be run, resetting the data in the database
if __name__ == "__main__":
    initialize_db()