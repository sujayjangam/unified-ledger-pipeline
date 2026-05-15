import sqlite3
import os

# Make sure this points to your actual database file
DB_PATH = os.path.join('data', 'ledger.db')

try:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Execute the rename command
    cursor.execute("""
    ALTER TABLE transactions 
    ADD COLUMN account_desc TEXT;
                   """)
    conn.commit()
    
    print("✅ Column successfully renamed to 'currency'!")
except Exception as e:
    print(f"❌ Error: {e}")
finally:
    if 'conn' in locals():
        conn.close()