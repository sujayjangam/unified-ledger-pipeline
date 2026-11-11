from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uuid
from sqlalchemy import text
from app.database import get_connection
from app.add_expense import dollars_to_cents
from app.services.participants import load_directory

app = FastAPI()

# 📝 The updated "Contract"
class Transaction(BaseModel):
    date: str
    description: str
    amount: float
    category: str = "General"
    # The account that paid, by name (case ignored) - it must be an open row in the accounts
    # table (#53). Whose account it is follows from that row, so the caller no longer sends an
    # owner at all.
    account: str

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

    # Which account paid. Only an open one: a closed card on the bot asks "are you sure?", but an
    # API call has nobody to ask, so the caller must name an open account. Outside the try below
    # for the same reason as the amount - an unknown name is the caller's mistake (422).
    try:
        with get_connection() as conn:
            directory = load_directory(conn)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Couldn't read the accounts table: {e}")
    account = directory.open_account_named(item.account)
    if account is None:
        raise HTTPException(status_code=422, detail=f"Unknown or closed account: {item.account}")
    owner = directory.participant(account.owner_participant_id)

    try:
        transaction_id = str(uuid.uuid4())
        
        # 🏗️ The SQL query now handles the owner field
        query = text('''
            INSERT INTO transactions (
                transaction_id, date, description, amount,
                base_amount, category, account_desc, account_owner, account_id,
                reconciliation_status, source
            )
            VALUES (
                :transaction_id, :date, :description, :amount,
                :base_amount, :category, :account_desc, :account_owner, :account_id,
                :reconciliation_status, :source
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
                # account_desc and account_owner are the old text columns, still written until
                # they are dropped (#53). A shared account (Cash, the wallet) has no owner, and an
                # API call has no sender to fall back on, so it stays "Shared" as before.
                "account_desc": account.name,
                "account_owner": owner.display_name if owner else "Shared",
                "account_id": account.id,
                "reconciliation_status": 'unsettled',
                "source": 'API',
            })
            conn.commit()

        return {"status": "success", "transaction_id": transaction_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))