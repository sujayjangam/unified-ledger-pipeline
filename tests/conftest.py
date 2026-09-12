import os

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key-not-real")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token-not-real")


@pytest.fixture(autouse=True)
def _no_real_database(monkeypatch):
    """Makes any attempt to open a database connection fail, in every test.

    The suite is meant to need no database. Locally, though, bot_core's load_dotenv() reads .env,
    whose DATABASE_URL is production - so a test that forgot to stub a query would quietly read
    (or write) the live ledger instead of failing. Patching get_engine covers every query, since
    get_connection() looks it up at call time.
    """
    import app.database

    def _refuse():
        raise RuntimeError("Tests must not open a database connection - stub the query instead.")

    monkeypatch.setattr(app.database, "get_engine", _refuse)
