import argparse
import uuid
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from sqlalchemy import text
from app.database import get_connection

_ONE_CENT = Decimal("0.01")

def dollars_to_cents(amount_dollars) -> int:
    """Converts a dollar amount to integer cents. Raises ValueError unless the result is a
    positive, finite number of cents.

    Exact decimal maths, never float arithmetic (#39, ADR-0028). The amount goes through str()
    first because Decimal(a_float) would carry the float's binary error along - Decimal(1.005) is
    1.00499999999999989... - whereas str(1.005) is '1.005': Python prints the shortest text that
    reads back as the same float, which is the number that was actually typed or extracted.

    Half a cent rounds UP: 1.005 -> 101, 0.125 -> 13. That's how IRAS rounds GST to the cent and
    what a person expects. It has to be asked for explicitly, because Decimal's own default is
    banker's rounding (ROUND_HALF_EVEN), which would turn 0.125 into 12. Real input almost never
    has a half cent, but this is the one conversion every path shares - the bot, the CLI, the REST
    API, and the statement parser to come.

    Never split an amount between people by rounding each share: 5.55 / 2 rounded is 278 + 278 =
    556, a cent more than was spent. Splits share out whole cents instead - see ADR-0028.
    """
    try:
        amount = Decimal(str(amount_dollars).strip())
        if not amount.is_finite():  # 'nan', 'inf'
            raise ValueError(f"Not a finite amount: {amount_dollars!r}")
        amount_cents = int(amount.quantize(_ONE_CENT, rounding=ROUND_HALF_UP) * 100)
    except InvalidOperation:
        # Not a number at all ('abc', None), or too large to hold to the cent.
        raise ValueError(f"Not a valid amount: {amount_dollars!r}") from None
    if amount_cents <= 0:
        raise ValueError("Amount must be greater than zero")
    return amount_cents

def format_cents(amount_cents: int) -> str:
    """Integer cents as a two-decimal string: 101 -> '1.01'. Whole-number maths only, so what's
    shown is exactly what's stored - use this for display rather than formatting a float."""
    sign = "-" if amount_cents < 0 else ""
    dollars, cents = divmod(abs(int(amount_cents)), 100)
    return f"{sign}{dollars}.{cents:02d}"

def add_expense(date_str, description, amount_dollars, category, currency="SGD", transaction_type="Expense", account_desc=None, account_owner=None, source="Manual CLI", idempotency_key=None, entered_by=None):
    try:
        # 1. Validation: Convert to Integer Cents (Mathematical Precision)
        # We call function dollars_to_cents to convert to cents for us
        amount_cents = dollars_to_cents(amount_dollars)     

        # 2. Validation: Ensure date matches YYYY-MM-DD, if not, ValueError is raised, stopping code at this line
        datetime.strptime(date_str, '%Y-%m-%d')
        
        # 3. DB Insertion
        transaction_id = str(uuid.uuid4())

        # ON CONFLICT DO NOTHING: if idempotency_key is provided and already exists (a duplicate
        # save attempt for the same confirm prompt), the UNIQUE constraint silently skips the
        # insert instead of raising - rowcount tells us which happened. NULL idempotency_key
        # (CLI/API callers) never collides, since SQL treats every NULL as distinct.
        # entered_by is the Telegram user ID of whoever sent the message (#60). The CLI and the
        # REST API don't pass one, so their rows leave it NULL.
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
                idempotency_key,
                entered_by
            )
            VALUES (
                :transaction_id, :date, :description, :amount, :currency,
                :base_amount, :category, :transaction_type, :account_desc,
                :account_owner, :reconciliation_status, :source, :idempotency_key,
                :entered_by
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
                "entered_by": entered_by,
            })

            was_duplicate = result.rowcount == 0
            conn.commit()

        if was_duplicate:
            # Already saved by a prior/concurrent call with the same idempotency_key - the
            # transaction is safely in the DB either way, so this is a success, not an error.
            print(f"↩️ Duplicate save ignored (already recorded): {description} ({currency} {format_cents(amount_cents)}) on {date_str}")
        else:
            print(f"✅ Successfully added: {description} ({currency} {format_cents(amount_cents)}) on {date_str}")
        return True

    except ValueError:
        print("❌ Error: Invalid input. Use YYYY-MM-DD for date and a number for amount (e.g., 15.50).")
        return False
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