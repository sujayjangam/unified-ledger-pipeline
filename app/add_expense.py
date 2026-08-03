import argparse
import uuid
from datetime import datetime, timedelta, timezone
import sys
from sqlalchemy import text
from app.database import get_connection

def add_expense(date_str, description, amount_dollars, category, currency="SGD", transaction_type="Expense", account_desc=None, account_owner=None, source="Manual CLI", idempotency_key=None):
    try:
        # 1. Validation: Convert to Integer Cents (Mathematical Precision)
        # We convert to float first, then multiply by 100, then cast to int.
        amount_cents = int(round(float(amount_dollars) * 100))
        
        if amount_cents <= 0:
            print("❌ Error: Amount must be greater than zero.")
            return False # return False so caller knows it has failed

        # 2. Validation: Ensure date matches YYYY-MM-DD, if not, ValueError is raised, stopping code at this line
        datetime.strptime(date_str, '%Y-%m-%d')
        
        # 3. DB Insertion
        transaction_id = str(uuid.uuid4())

        # ON CONFLICT DO NOTHING: if idempotency_key is provided and already exists (a duplicate
        # save attempt for the same confirm prompt), the UNIQUE constraint silently skips the
        # insert instead of raising - rowcount tells us which happened. NULL idempotency_key
        # (CLI/API callers) never collides, since SQL treats every NULL as distinct.
        query = text('''
            INSERT INTO transactions (
                transaction_id,
                date,
                description,
                amount,
                currency,
                base_amount,
                category,
                transaction_type,
                account_desc,
                account_owner,
                reconciliation_status,
                source,
                idempotency_key
            )
            VALUES (
                :transaction_id, :date, :description, :amount, :currency,
                :base_amount, :category, :transaction_type, :account_desc,
                :account_owner, :reconciliation_status, :source, :idempotency_key
            )
            ON CONFLICT (idempotency_key) DO NOTHING
        ''')

        # Note: source is 'Manual' for this tool
        with get_connection() as conn:
            result = conn.execute(query, {
                "transaction_id": transaction_id,
                "date": date_str,
                "description": description,
                "amount": amount_cents,
                "currency": currency,
                "base_amount": amount_cents,
                "category": category,
                "transaction_type": transaction_type,
                "account_desc": account_desc,
                "account_owner": account_owner,
                "reconciliation_status": 'unsettled',
                "source": source,
                "idempotency_key": idempotency_key,
            })

            was_duplicate = result.rowcount == 0
            conn.commit()

        if was_duplicate:
            # Already saved by a prior/concurrent call with the same idempotency_key - the
            # transaction is safely in the DB either way, so this is a success, not an error.
            print(f"↩️ Duplicate save ignored (already recorded): {description} ({currency} {float(amount_dollars):.2f}) on {date_str}")
        else:
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