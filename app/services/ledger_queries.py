from sqlalchemy import text
from app.database import get_connection

def get_recent_entries(limit=5):
    """Fetches the most recently *entered* transactions, including both expenses and transfers.

    Ordered by created_at (when the row was written), not date (when the spend happened) - see
    docs/SCHEMA.md. "Recent" means "what was just logged": once backdated entries exist (#15), an
    expense from last week that was logged a minute ago is still recent. transaction_id is a
    random UUID, so as a tiebreaker it carries no meaning of its own; it only keeps rows that
    share a created_at in a stable order, and rows that predate the column all share their
    date's midnight (see alembic/versions/0002_add_created_at.py).
    """
    try:
        with get_connection() as conn:
            result = conn.execute(text('''
                SELECT date, description, amount, currency, category
                FROM transactions
                ORDER BY created_at DESC, transaction_id DESC
                LIMIT :limit
            '''), {"limit": limit})
            return result.fetchall()
    except Exception as e:
        print(f"❌ Query Error: {e}")
        return []

def find_recent_duplicate(amount_cents: int, currency: str, within_minutes: int):
    """Returns the newest entry with exactly this amount and currency saved in the last
    `within_minutes` minutes, or None if there isn't one.

    Backs the duplicate warning (#58, ADR-0027). The bot calls this after extraction and before
    showing the confirmation card, so the user can decide while nothing has been saved yet.

    - Amount + currency only, never description: description is the raw input, and Whisper
      words the same spoken sentence differently each time, so a description match would rarely
      fire on voice notes.
    - Anyone's entry, not just the sender's: two people logging the same shared bill is exactly
      the case worth catching.
    - Windowed on created_at (write time), never date, which has no time of day - see
      docs/SCHEMA.md. Both now() and created_at come from the database clock, so the clock of
      whichever machine runs the bot never enters into it.

    The result is a dict of description, seconds_ago and entered_by_participant_id (#53; None
    for rows whose sender was never recorded - before #60, or written by the CLI/API). Any error also returns None, deliberately:
    a broken check must never stop an entry being saved, so the caller just shows a normal card.
    """
    try:
        with get_connection() as conn:
            row = conn.execute(text('''
                SELECT description,
                       CAST(EXTRACT(EPOCH FROM (now() - created_at)) AS integer) AS seconds_ago,
                       entered_by_participant_id
                FROM transactions
                WHERE amount = :amount
                  AND currency = :currency
                  AND created_at >= now() - make_interval(mins => CAST(:within_minutes AS integer))
                ORDER BY created_at DESC
                LIMIT 1
            '''), {"amount": amount_cents, "currency": currency, "within_minutes": within_minutes}).mappings().first()
            return dict(row) if row else None
    except Exception as e:
        print(f"❌ Duplicate check failed, showing a normal card: {e}")
        return None

def get_period_summary(start_date: str, end_date: str):
    """Fetches counts and totals grouped by currency, separating expenses and transfers."""
    try:
        with get_connection() as conn:
            # Outside people's money is not household spending. NULL means not recorded
            # (old/CLI/API rows), which still counts as household in both totals.
            # Get Expenses grouped by currency
            expenses = conn.execute(text('''
                SELECT currency, COUNT(*), SUM(amount)
                FROM transactions
                WHERE date BETWEEN :start_date AND :end_date AND category != 'Transfer'
                  AND (paid_by_participant_id IS NULL OR paid_by_participant_id IN
                       (SELECT id FROM participants WHERE is_household))
                GROUP BY currency
            '''), {"start_date": start_date, "end_date": end_date}).fetchall()

            # Get Transfers grouped by currency
            transfers = conn.execute(text('''
                SELECT currency, COUNT(*), SUM(amount)
                FROM transactions
                WHERE date BETWEEN :start_date AND :end_date AND category = 'Transfer'
                  AND (paid_by_participant_id IS NULL OR paid_by_participant_id IN
                       (SELECT id FROM participants WHERE is_household))
                GROUP BY currency
            '''), {"start_date": start_date, "end_date": end_date}).fetchall()

            return expenses, transfers

    except Exception as e:
        print(f"❌ Query Error: {e}")
        return [], []

def get_category_summary(start_date: str, end_date: str):
    """Household category totals: exclude outside payers, but retain old/CLI/API NULLs."""
    try:
        with get_connection() as conn:
            result = conn.execute(text('''
                SELECT category, currency, COUNT(*), SUM(amount)
                FROM transactions
                WHERE date BETWEEN :start_date AND :end_date AND category != 'Transfer'
                  AND (paid_by_participant_id IS NULL OR paid_by_participant_id IN
                       (SELECT id FROM participants WHERE is_household))
                GROUP BY category, currency
                ORDER BY category, currency
            '''), {"start_date": start_date, "end_date": end_date})

            return result.fetchall()

    except Exception as e:
        print(f"❌ Query Error: {e}")
        return []


def get_trip_settlements(trip_id: int):
    """Read paybacks oldest first; None means failure, not an already settled trip."""
    try:
        with get_connection() as conn:
            rows = conn.execute(text('''
                SELECT id, date, from_participant_id AS from_id, to_participant_id AS to_id,
                       for_participant_id AS for_id,
                       clears_cents AS cents, clears_currency AS currency, paid_cents, paid_currency
                FROM settlements WHERE trip_id = :trip_id
                ORDER BY date, created_at, id
            '''), {"trip_id": trip_id}).mappings().all()
            return [dict(row) for row in rows]
    except Exception as e:
        print(f"Could not read trip paybacks: {e}")
        return None


def get_trip_split_entries(trip_id: int):
    """Every split entry tagged to one trip, in the shape app/services/balances.py reads.

    Returns a list of dicts, oldest first:
      {transaction_id, date, description, amount, currency, paid_by, splits: {participant id: cents}}
    An empty list means the trip has no split entries. None means the read FAILED, so the
    caller can say so; a failure must never be shown as "nobody owes anything".

    Only entries with at least one row in transaction_splits come back (the inner JOIN): an
    entry with no split is the payer's alone and moves no balance.

    paid_by: who paid. When paid_by_participant_id is NULL ("not recorded": an old row given
    its splits by hand in SQL) it is read the way docs/SCHEMA.md says: the account's owner,
    or the sender for an unowned account such as Cash. It can still come back None, and
    unit_balances then hands that entry back instead of counting it.

    Money stays in the entry's own currency (amount, not base_amount): no FX conversion.
    """
    try:
        with get_connection() as conn:
            rows = conn.execute(text('''
                SELECT t.transaction_id, t.date, t.description, t.amount, t.currency,
                       COALESCE(t.paid_by_participant_id, a.owner_participant_id,
                                t.entered_by_participant_id) AS paid_by,
                       s.participant_id, s.share_cents
                FROM transactions t
                JOIN transaction_splits s ON s.transaction_id = t.transaction_id
                JOIN accounts a ON a.id = t.account_id
                WHERE t.trip_id = :trip_id
                ORDER BY t.date, t.created_at, t.transaction_id, s.participant_id
            '''), {"trip_id": trip_id}).fetchall()
    except Exception as e:
        print(f"❌ Query Error: {e}")
        return None

    # The query gives one row per share, so an entry split three ways arrives as three rows.
    # Fold them back into one dict per entry. A dict keeps insertion order, so the entries
    # stay in the order the query sorted them.
    entries = {}
    for transaction_id, date, description, amount, currency, paid_by, participant_id, share_cents in rows:
        entry = entries.setdefault(transaction_id, {
            "transaction_id": transaction_id, "date": date, "description": description,
            "amount": amount, "currency": currency, "paid_by": paid_by, "splits": {},
        })
        entry["splits"][participant_id] = share_cents
    return list(entries.values())
