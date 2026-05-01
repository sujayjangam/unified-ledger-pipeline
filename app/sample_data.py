from database import get_connection
import uuid

def insert_sample_data():
    conn = get_connection()
    cursor = conn.cursor()

    # Define a list of sample transactions (Tuples)
    # Fields: id, date, desc, amt, curr, base_amt, owner, benefit, split, cat, type, source, status
    samples = [
        (str(uuid.uuid4()), '2023-10-01', 'NTUC FairPrice', 5000, 'SGD', 5000, 'Sujay', 'Shared', 0.5, 'Groceries', 'expense', 'Manual', 'unsettled'),
        (str(uuid.uuid4()), '2023-10-02', 'Starbucks Coffee', 750, 'SGD', 750, 'Sujay', 'Sujay', 1.0, 'Dining', 'expense', 'Manual', 'settled')
    ]

    # The SQL command using ? as placeholders (Best Practice to prevent SQL injection)
    query = '''
        INSERT INTO transactions 
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    '''

    cursor.executemany(query, samples)
    conn.commit()
    conn.close()
    print("💰 Sample data inserted successfully!")

if __name__ == "__main__":
    insert_sample_data()