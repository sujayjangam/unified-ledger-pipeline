import argparse
import uuid
import datetime
import sys
from database import get_connection

def add_expense(date_str, description, amount_dollars, currency='SGD', category, source="Manual CLI"):
    try:
        # 1. Validation: Convert to Integer Cents (Mathematical Precision)
        # We convert to float first, then multiply by 100, then cast to int.
        amount_cents = int(round(float(amount_dollars) * 100))
        
        if amount_cents <= 0:
            print("❌ Error: Amount must be greater than zero.")
            return False # return False so caller knows it has failed

        # 2. Validation: Ensure date matches YYYY-MM-DD
        datetime.datetime.strptime(date_str, '%Y-%m-%d')
        
        # 3. DB Insertion
        transaction_id = str(uuid.uuid4())
        conn = get_connection()
        cursor = conn.cursor()
        
        query = '''
            INSERT INTO transactions (
                transaction_id, date, description, amount, 
                base_amount, category, reconciliation_status, source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        '''
        
        # Note: source is 'Manual' for this tool
        cursor.execute(query, (
            transaction_id, date_str, description, amount_cents, 
            amount_cents, category, 'unsettled', source
        ))
        
        conn.commit()
        conn.close()
        print(f"✅ Successfully added: {description} ({currency} {float(amount_dollars):.2f}) on {date_str}")
        return True

    except ValueError:
        print("❌ Error: Invalid input. Use YYYY-MM-DD for date and a number for amount (e.g., 15.50).")
    except Exception as e:
        print(f"❌ An unexpected error occurred: {e}")
        return False # return False on error so caller knows it has failed

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Ledger CLI: Manual Expense Entry')
    parser.add_argument('--date', required=True, help='Date (YYYY-MM-DD)')
    parser.add_argument('--desc', required=True, help='Description')
    parser.add_argument('--amount', required=True, help='Amount in dollars')
    parser.add_argument('--cat', default='General', help='Category (default: General)')

    args = parser.parse_args()
    add_expense(args.date, args.desc, args.amount, args.cat)