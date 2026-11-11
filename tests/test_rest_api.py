"""Amount handling in the REST API's POST /transactions (app/main.py).

The endpoint used to convert dollars to cents with its own float copy of the logic, so it could
store different cents from the bot for the same input (#39). It now calls the shared
dollars_to_cents. Since #53 a caller names the account that paid, which is looked up in the
accounts table - load_directory is faked with the fictional `directory` fixture, and the database
is faked, as everywhere in this suite.
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import app.main as main


def _fake_db(monkeypatch):
    conn = MagicMock()
    context_manager = MagicMock()
    context_manager.__enter__.return_value = conn
    monkeypatch.setattr(main, "get_connection", lambda: context_manager)
    return conn


def _post(amount, account="Card A"):
    return TestClient(main.app).post(
        "/transactions",
        json={"date": "2026-09-13", "description": "test entry", "amount": amount, "account": account},
    )


def test_post_uses_the_shared_conversion(monkeypatch, directory):
    conn = _fake_db(monkeypatch)
    monkeypatch.setattr(main, "load_directory", lambda conn: directory)

    response = _post(1.005)

    assert response.status_code == 200
    params = conn.execute.call_args.args[1]
    assert params["amount"] == 101  # half a cent rounds up, same as the bot
    assert params["base_amount"] == 101


@pytest.mark.parametrize("amount", [0, -5])
def test_post_rejects_an_amount_that_is_not_positive(monkeypatch, directory, amount):
    # It used to save these. A bad amount is the caller's mistake, so 422, not 500.
    conn = _fake_db(monkeypatch)
    monkeypatch.setattr(main, "load_directory", lambda conn: directory)

    response = _post(amount)

    assert response.status_code == 422
    conn.execute.assert_not_called()


def test_post_resolves_the_account_name(monkeypatch, directory):
    conn = _fake_db(monkeypatch)
    monkeypatch.setattr(main, "load_directory", lambda conn: directory)

    response = _post(12, account="card a")  # case ignored, as in the bot

    assert response.status_code == 200
    params = conn.execute.call_args.args[1]
    assert params["account_id"] == 10
    # The old text columns are still written until they are dropped (#53).
    assert (params["account_desc"], params["account_owner"]) == ("Card A", "Alice")


def test_post_rejects_an_unknown_account(monkeypatch, directory):
    # A closed card counts as unknown here: there is nobody to ask "are you sure?", so the caller
    # has to name an open account.
    conn = _fake_db(monkeypatch)
    monkeypatch.setattr(main, "load_directory", lambda conn: directory)

    response = _post(12, account="Old Card")

    assert response.status_code == 422
    assert "Unknown or closed account: Old Card" in response.text
    conn.execute.assert_not_called()
