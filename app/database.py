import os

from dotenv import load_dotenv
from sqlalchemy import create_engine

# Loaded here rather than relying on callers: view_ledger/sample_data/main.py never call
# load_dotenv() themselves, and they all need DATABASE_URL to reach the database.
load_dotenv()

# The engine is created lazily so that merely importing this module (e.g. `--help` on the CLI,
# or Alembic loading app code) doesn't open a connection pool or hard-fail on a missing .env.
_engine = None


def get_engine():
    """Returns the process-wide SQLAlchemy engine, creating it on first use."""
    global _engine
    if _engine is None:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "DATABASE_URL is not set. Add it to your .env file "
                "(see .env.example for the expected format)."
            )
        _engine = create_engine(
            database_url,
            # Small pool: this is a two-person household ledger, not a high-traffic service.
            pool_size=5,
            max_overflow=2,
            # Neon's free tier suspends the compute after ~5 minutes idle. Without pre_ping the
            # first query after a suspend hits a dead socket instead of transparently reconnecting.
            pool_pre_ping=True,
            pool_recycle=300,
        )
    return _engine


def get_connection():
    """
    Returns a pooled connection. Use as a context manager so it is always returned to the pool:

        with get_connection() as conn:
            conn.execute(...)
            conn.commit()   # writes only; SQLAlchemy does not autocommit

    Exiting the block rolls back any uncommitted transaction and releases the connection.
    """
    return get_engine().connect()
