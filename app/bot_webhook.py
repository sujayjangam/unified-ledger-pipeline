import hmac
import os
import re
from collections import deque
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from telegram import Update
from app.bot_core import get_application

# Fetch our bot engine
ptb_app = get_application()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")

# Shared secret that proves a request to /webhook came from Telegram. It is registered with
# Telegram through setWebhook's secret_token parameter at startup (see lifespan below), and
# Telegram then sends it back in the X-Telegram-Bot-Api-Secret-Token header of every delivery.
# Only Telegram and this service know it, so a request without it did not come from Telegram.
# That matters because is_authorized() trusts the sender ID *inside* an update - which is only
# trustworthy if Telegram wrote the update. Lives in Secret Manager, never in the repo. ADR-0025.
WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", "")
_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
# Telegram's documented format for secret_token: 1-256 characters, only A-Z, a-z, 0-9, _ and -.
_SECRET_TOKEN_FORMAT = re.compile(r"[A-Za-z0-9_-]{1,256}")

# Bounded dedup cache for Telegram update_ids: if a webhook delivery is retried (e.g. our first
# response was slow), the same update_id arrives again and must not be processed twice. The set
# gives O(1) "have we seen this?" lookups; the deque caps memory by evicting the oldest id once
# full, since we only need to catch near-term retries, not remember every id forever.
_SEEN_UPDATE_IDS_MAXLEN = 1000
_seen_update_ids: set[int] = set()
_seen_update_ids_order: deque[int] = deque(maxlen=_SEEN_UPDATE_IDS_MAXLEN)


def _is_duplicate_update(update_id: int) -> bool:
    """Returns True (and does nothing further) if this update_id was already processed;
    otherwise records it and returns False."""
    if update_id in _seen_update_ids:
        return True
    if len(_seen_update_ids_order) == _SEEN_UPDATE_IDS_MAXLEN:
        oldest = _seen_update_ids_order[0]  # about to be evicted by the deque's append below
        _seen_update_ids.discard(oldest)
    _seen_update_ids_order.append(update_id)
    _seen_update_ids.add(update_id)
    return False


def _is_from_telegram(header_value: str | None, expected: str) -> bool:
    """True only if a request carries the secret token registered with Telegram.

    Fails closed: with no token configured, nothing is accepted. hmac.compare_digest takes the
    same time however many leading characters match, so response timing can't be used to
    recover the token one character at a time the way a plain == comparison could allow.
    """
    if not expected or header_value is None:
        return False
    return hmac.compare_digest(header_value.encode(), expected.encode())


# Define the Lifespan (Startup & Shutdown)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- STARTUP LOGIC (Runs when server wakes up) ---
    # Refuse to start rather than run a webhook that every delivery would be rejected by - or
    # one registered without a secret at all. Checked before anything connects to Telegram.
    if WEBHOOK_URL and not _SECRET_TOKEN_FORMAT.fullmatch(WEBHOOK_SECRET_TOKEN):
        raise RuntimeError(
            "WEBHOOK_SECRET_TOKEN must be set whenever WEBHOOK_URL is: 1-256 characters of "
            "A-Z, a-z, 0-9, _ and - (Telegram's format). Generate one with: "
            "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )

    await ptb_app.initialize()
    await ptb_app.start()

    if WEBHOOK_URL:
        # Registered on every startup, so the token Telegram sends is always the one this process
        # checks against.
        await ptb_app.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook", secret_token=WEBHOOK_SECRET_TOKEN)
        print(f"✅ Webhook successfully set to {WEBHOOK_URL}/webhook")
    else:
        print("⚠️ WARNING: WEBHOOK_URL not set in environment.")

    yield # This tells FastAPI: "Pause here and run the web server now"

    # --- SHUTDOWN LOGIC (Runs when server spins down) ---
    await ptb_app.stop()
    await ptb_app.shutdown()

# Initialize FastAPI with the lifespan
app_fastapi = FastAPI(lifespan=lifespan)

# The Webhook Door
@app_fastapi.post("/webhook")
async def telegram_webhook(request: Request):
    """The specific 'door' that Telegram knocks on."""
    # Checked first - before the body is parsed or its update_id is recorded. A request that
    # didn't come from Telegram must not reach the bot, and must not be able to fill the dedupe
    # cache with update_ids that would make genuine deliveries look like duplicates.
    if not _is_from_telegram(request.headers.get(_SECRET_HEADER), WEBHOOK_SECRET_TOKEN):
        return JSONResponse(status_code=403, content={"status": "forbidden"})

    data = await request.json()
    update = Update.de_json(data, ptb_app.bot)

    if _is_duplicate_update(update.update_id):
        return {"status": "duplicate_ignored"}

    await ptb_app.process_update(update)

    return {"status": "ok"}
