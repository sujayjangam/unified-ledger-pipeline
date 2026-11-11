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


@pytest.fixture
def directory():
    """A fictional Directory shared by the tests (this repo is public: never real names or cards).

    Alice and Bob use the bot; Carol is tracked for splitting only, so she has no Telegram ID and
    no default. Cash and YouTrip are shared; YouTrip is topped up from Card A. Old Card is Alice's,
    closed on 2026-03-31.
    """
    from datetime import date

    from app.services.participants import Account, Directory, Participant

    return Directory(
        participants=(
            Participant(1, "Alice", "111111", 10),
            Participant(2, "Bob", "222222", 11),
            Participant(3, "Carol", None, None),
        ),
        accounts=(
            Account(10, "Card A", 1, None, None),
            Account(11, "Card B", 2, None, None),
            Account(12, "Cash", None, None, None),
            Account(13, "YouTrip", None, 10, None),
            Account(14, "Old Card", 1, None, date(2026, 3, 31)),
        ),
    )
