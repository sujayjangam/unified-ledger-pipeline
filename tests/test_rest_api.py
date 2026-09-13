"""Amount handling in the REST API's POST /transactions (app/main.py).

The endpoint used to convert dollars to cents with its own float copy of the logic, so it could
store different cents from the bot for the same input (#39). It now calls the shared
dollars_to_cents. The database is faked, as everywhere in this suite.
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


def _post(amount):
    return TestClient(main.app).post(
        "/transactions", json={"date": "2026-09-13", "description": "test entry", "amount": amount}
    )


def test_post_uses_the_shared_conversion(monkeypatch):
    conn = _fake_db(monkeypatch)

    response = _post(1.005)

    assert response.status_code == 200
    params = conn.execute.call_args.args[1]
    assert params["amount"] == 101  # half a cent rounds up, same as the bot
    assert params["base_amount"] == 101


@pytest.mark.parametrize("amount", [0, -5])
def test_post_rejects_an_amount_that_is_not_positive(monkeypatch, amount):
    # It used to save these. A bad amount is the caller's mistake, so 422, not 500.
    conn = _fake_db(monkeypatch)

    response = _post(amount)

    assert response.status_code == 422
    conn.execute.assert_not_called()
