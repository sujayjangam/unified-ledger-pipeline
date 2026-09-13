from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uuid
from sqlalchemy import text
from app.database import get_connection
from app.add_expense import dollars_to_cents

app = FastAPI()

# 📝 The updated "Contract"
class Transaction(BaseModel):
    date: str
    description: str
    amount: float
    category: str = "General"
    # if account_owner is not defined, setting as "shared" to protect other users' privacy
    account_owner: str = "Shared" 

@app.get("/")
def read_root():
    return {"message": "Ledger API is online"}

@app.get("/transactions")
def get_transactions():
    """Fetches all transactions including the owner."""
    # for now we select only the following fields from our database
    query = text("SELECT date, description, amount, category, account_owner FROM transactions")
    with get_connection() as conn:
        rows = conn.execute(query).fetchall()


    # Mapping the DB data to API response
    return [
        {
            "date": r[0], 
            "description": r[1], 
            "amount": r[2]/100, 
            "category": r[3],
            "account_owner": r[4]
        } for r in rows
    ]

@app.post("/transactions")
def add_transaction(item: Transaction):
    """Adds a new transaction via the API."""
    # The same conversion the bot and CLI use (#39). This endpoint used to keep its own float
    # copy, so the two paths could store different cents for the same input. Done before the
    # try below, because that block turns every exception - an HTTPException included - into a
    # 500, and a bad amount is the caller's mistake (422), not a server error.
    try:
        amount_cents = dollars_to_cents(item.amount)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    try:
        transaction_id = str(uuid.uuid4())
        
        # 🏗️ The SQL query now handles the owner field
        query = text('''
            INSERT INTO transactions (
                transaction_id, date, description, amount,
                base_amount, category, account_owner, reconciliation_status, source
            )
            VALUES (
                :transaction_id, :date, :description, :amount,
                :base_amount, :category, :account_owner, :reconciliation_status, :source
            )
        ''')

        with get_connection() as conn:
            conn.execute(query, {
                "transaction_id": transaction_id,
                "date": item.date,
                "description": item.description,
                "amount": amount_cents,
                "base_amount": amount_cents,
                "category": item.category,
                "account_owner": item.account_owner,
                "reconciliation_status": 'unsettled',
                "source": 'API',
            })
            conn.commit()

        return {"status": "success", "transaction_id": transaction_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))