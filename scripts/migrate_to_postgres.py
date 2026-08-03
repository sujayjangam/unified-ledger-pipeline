"""
One-time data migration: SQLite (data/ledger.db) -> Neon Postgres.

Run once, from the repo root, AFTER `alembic upgrade head` has created the transactions table:

    python -m scripts.migrate_to_postgres

Safety properties:
  - Read-only against SQLite. The source file is never written to or deleted; keep it as the
    rollback path until you're satisfied the migration is good.
  - Safe to re-run. Rows are keyed on transaction_id (the primary key) with ON CONFLICT DO NOTHING,
    so a second run inserts nothing rather than duplicating. Note this dedupes on the PK, not on
    idempotency_key - that column is NULL for all pre-migration rows and is the *application's*
    dedupe mechanism, which is a different concern from this script's re-runnability.
  - All-or-nothing. Every insert happens inside a single transaction; any error rolls back the
    whole thing, leaving Postgres untouched.
  - Verifies rather than trusts. Row count, integer-cents checksums, and date range are compared
    between source and destination after the copy. Exits non-zero if anything disagrees.
"""
import os
import sqlite3
import sys

from sqlalchemy import text

# Import from the app so this script shares one definition of how to reach the database
# (URL, SSL, pool settings) instead of maintaining a second copy that can drift.
from app.database import get_engine

SQLITE_PATH = os.path.join("data", "ledger.db")

# Explicit column list, in the order defined by the Alembic baseline migration. Never rely on
# "SELECT *" or positional INSERTs here - implicit column order is exactly what caused the
# account_desc drift this migration is cleaning up.
COLUMNS = [
    "transaction_id",
    "date",
    "description",
    "amount",
    "currency",
    "base_amount",
    "account_owner",
    "benefit_of",
    "split_ratio",
    "category",
    "transaction_type",
    "source",
    "reconciliation_status",
    "account_desc",
    "idempotency_key",
]


def read_sqlite_rows():
    """Reads every transaction out of the SQLite file as a list of dicts."""
    if not os.path.exists(SQLITE_PATH):
        print(f"❌ Source database not found at {SQLITE_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(SQLITE_PATH)
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(f"SELECT {', '.join(COLUMNS)} FROM transactions")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def sqlite_stats():
    """Source-side totals used to verify the copy landed intact."""
    conn = sqlite3.connect(SQLITE_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*), SUM(amount), SUM(base_amount), MIN(date), MAX(date)
            FROM transactions
        ''')
        return cursor.fetchone()
    finally:
        conn.close()


def postgres_stats(conn):
    """Destination-side totals, computed the same way for comparison."""
    return conn.execute(text('''
        SELECT COUNT(*), SUM(amount), SUM(base_amount), MIN(date), MAX(date)
        FROM transactions
    ''')).fetchone()


def main():
    rows = read_sqlite_rows()
    print(f"📖 Read {len(rows)} row(s) from {SQLITE_PATH}")

    if not rows:
        print("Nothing to migrate.")
        return

    insert_sql = text(f'''
        INSERT INTO transactions ({", ".join(COLUMNS)})
        VALUES ({", ".join(f":{col}" for col in COLUMNS)})
        ON CONFLICT (transaction_id) DO NOTHING
    ''')

    engine = get_engine()

    # engine.begin() commits on clean exit and rolls back on any exception, so a failure part-way
    # through leaves Postgres exactly as it was.
    with engine.begin() as conn:
        conn.execute(insert_sql, rows)

    # --- Verification: compare source and destination independently of the insert above ---
    src_count, src_amount, src_base, src_min_date, src_max_date = sqlite_stats()

    with engine.connect() as conn:
        dst_count, dst_amount, dst_base, dst_min_date, dst_max_date = postgres_stats(conn)
        spot_check = conn.execute(text('''
            SELECT date, description, amount, currency, category
            FROM transactions
            ORDER BY date DESC, transaction_id DESC
            LIMIT 3
        ''')).fetchall()

    checks = [
        ("row count", src_count, dst_count),
        ("SUM(amount)", src_amount, dst_amount),
        ("SUM(base_amount)", src_base, dst_base),
        ("MIN(date)", src_min_date, dst_min_date),
        ("MAX(date)", src_max_date, dst_max_date),
    ]

    print("\n" + "=" * 60)
    print(f"{'Check':<20} | {'SQLite':<16} | {'Postgres':<16}")
    print("-" * 60)
    failures = []
    for label, src, dst in checks:
        status = "✅" if src == dst else "❌"
        print(f"{status} {label:<17} | {str(src):<16} | {str(dst):<16}")
        if src != dst:
            failures.append(label)
    print("=" * 60)

    print("\n🔍 Spot check - 3 most recent rows now in Postgres:")
    for date, description, amount, currency, category in spot_check:
        print(f"   {date} | {description[:30]:<30} | {currency} {amount / 100:>9.2f} | {category}")

    if failures:
        print(f"\n❌ MIGRATION VERIFICATION FAILED: {', '.join(failures)} did not match.")
        print(f"   {SQLITE_PATH} is untouched - investigate before relying on Postgres.")
        sys.exit(1)

    print(f"\n✅ Migration verified: all {dst_count} row(s) match the source.")
    print(f"   {SQLITE_PATH} has been left untouched as a rollback safety net.")


if __name__ == "__main__":
    main()
