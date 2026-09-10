"""Tests for the webhook's check that a delivery really came from Telegram (app/bot_webhook.py).

Telegram sends the secret token registered via setWebhook in the X-Telegram-Bot-Api-Secret-Token
header of every delivery; /webhook rejects anything without it. No network: the bot is a mock,
and TestClient is used without `with`, so the FastAPI lifespan (which would contact Telegram)
never runs. The lifespan tests drive it directly against the same mock.
"""

from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import app.bot_webhook as bot_webhook

TOKEN = "test-secret-token_123"
HEADER = "X-Telegram-Bot-Api-Secret-Token"


@pytest.fixture
def fake_bot_app(monkeypatch):
    """Swaps the real PTB application for a mock, with a known token and an empty dedupe cache."""
    fake = MagicMock()
    for name in ("process_update", "initialize", "start", "stop", "shutdown"):
        setattr(fake, name, AsyncMock())
    fake.bot.set_webhook = AsyncMock()
    monkeypatch.setattr(bot_webhook, "ptb_app", fake)
    monkeypatch.setattr(bot_webhook, "WEBHOOK_SECRET_TOKEN", TOKEN)
    monkeypatch.setattr(bot_webhook, "_seen_update_ids", set())
    monkeypatch.setattr(bot_webhook, "_seen_update_ids_order",
                        deque(maxlen=bot_webhook._SEEN_UPDATE_IDS_MAXLEN))
    return fake


@pytest.fixture
def client(fake_bot_app):
    return TestClient(bot_webhook.app_fastapi)


# --- the request check ---

def test_request_without_the_header_is_rejected_unprocessed(client, fake_bot_app):
    response = client.post("/webhook", json={"update_id": 1})

    assert response.status_code == 403
    fake_bot_app.process_update.assert_not_awaited()


def test_request_with_a_wrong_token_is_rejected(client, fake_bot_app):
    response = client.post("/webhook", json={"update_id": 2}, headers={HEADER: "not-the-token"})

    assert response.status_code == 403
    fake_bot_app.process_update.assert_not_awaited()


def test_request_with_the_right_token_is_processed(client, fake_bot_app):
    response = client.post("/webhook", json={"update_id": 3}, headers={HEADER: TOKEN})

    assert response.status_code == 200
    fake_bot_app.process_update.assert_awaited_once()


def test_rejected_request_cannot_make_a_real_delivery_look_like_a_duplicate(client, fake_bot_app):
    # A forged request is turned away before its update_id is recorded, so a genuine
    # delivery that happens to share that id is still processed.
    forged = client.post("/webhook", json={"update_id": 42})
    genuine = client.post("/webhook", json={"update_id": 42}, headers={HEADER: TOKEN})

    assert forged.status_code == 403
    assert genuine.status_code == 200
    fake_bot_app.process_update.assert_awaited_once()


def test_with_no_token_configured_nothing_is_accepted(client, fake_bot_app, monkeypatch):
    monkeypatch.setattr(bot_webhook, "WEBHOOK_SECRET_TOKEN", "")

    response = client.post("/webhook", json={"update_id": 4}, headers={HEADER: ""})

    assert response.status_code == 403
    fake_bot_app.process_update.assert_not_awaited()


@pytest.mark.parametrize(
    "header, expected, accepted",
    [
        (TOKEN, TOKEN, True),
        ("wrong", TOKEN, False),
        (TOKEN[:-1], TOKEN, False),  # a prefix is not a match
        (None, TOKEN, False),        # header absent
        ("", "", False),             # fails closed when unconfigured
    ],
)
def test_is_from_telegram(header, expected, accepted):
    assert bot_webhook._is_from_telegram(header, expected) is accepted


# --- startup ---

@pytest.mark.parametrize("bad_token", ["", "has a space", "x" * 257])
async def test_startup_refuses_without_a_valid_token(fake_bot_app, monkeypatch, bad_token):
    monkeypatch.setattr(bot_webhook, "WEBHOOK_URL", "https://example.invalid")
    monkeypatch.setattr(bot_webhook, "WEBHOOK_SECRET_TOKEN", bad_token)

    with pytest.raises(RuntimeError, match="WEBHOOK_SECRET_TOKEN"):
        async with bot_webhook.lifespan(bot_webhook.app_fastapi):
            pass

    # Refused before anything connected to Telegram.
    fake_bot_app.initialize.assert_not_awaited()
    fake_bot_app.bot.set_webhook.assert_not_awaited()


async def test_startup_registers_the_token_with_telegram(fake_bot_app, monkeypatch):
    monkeypatch.setattr(bot_webhook, "WEBHOOK_URL", "https://example.invalid")

    async with bot_webhook.lifespan(bot_webhook.app_fastapi):
        pass

    fake_bot_app.bot.set_webhook.assert_awaited_once()
    assert fake_bot_app.bot.set_webhook.await_args.kwargs["secret_token"] == TOKEN
