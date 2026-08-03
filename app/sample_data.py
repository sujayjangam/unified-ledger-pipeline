from sqlalchemy import text
from app.database import get_connection
import uuid

def insert_sample_data():
    # Define a list of sample transactions (dicts keyed to the named binds below)
    samples = [
        {
            "transaction_id": str(uuid.uuid4()), "date": '2023-10-01',
            "description": 'NTUC FairPrice', "amount": 5000, "currency": 'SGD',
            "base_amount": 5000, "account_owner": 'Sujay', "benefit_of": 'Shared',
            "split_ratio": 0.5, "category": 'Groceries', "transaction_type": 'expense',
            "source": 'Manual', "reconciliation_status": 'unsettled',
        },
        {
            "transaction_id": str(uuid.uuid4()), "date": '2023-10-02',
            "description": 'Starbucks Coffee', "amount": 750, "currency": 'SGD',
            "base_amount": 750, "account_owner": 'Sujay', "benefit_of": 'Sujay',
            "split_ratio": 1.0, "category": 'Dining', "transaction_type": 'expense',
            "source": 'Manual', "reconciliation_status": 'settled',
        },
    ]

    # Columns are listed explicitly rather than relying on table column order - the previous
    # positional "INSERT INTO transactions VALUES (...)" silently depended on the declared order
    # and broke once account_desc and idempotency_key were added. Both are omitted here and left
    # NULL, which is what sample rows want anyway.
    query = text('''
        INSERT INTO transactions (
            transaction_id, date, description, amount, currency, base_amount,
            account_owner, benefit_of, split_ratio, category, transaction_type,
            source, reconciliation_status
        )
        VALUES (
            :transaction_id, :date, :description, :amount, :currency, :base_amount,
            :account_owner, :benefit_of, :split_ratio, :category, :transaction_type,
            :source, :reconciliation_status
        )
    ''')

    with get_connection() as conn:
        conn.execute(query, samples)
        conn.commit()

    print("💰 Sample data inserted successfully!")

if __name__ == "__main__":
    insert_sample_data()
