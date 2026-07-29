import os
from collections import deque
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from telegram import Update
from app.bot_core import get_application

# Fetch our bot engine
ptb_app = get_application()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")

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

# Define the Lifespan (Startup & Shutdown)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- STARTUP LOGIC (Runs when server wakes up) ---
    await ptb_app.initialize()
    await ptb_app.start()
    
    if WEBHOOK_URL:
        await ptb_app.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
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
    data = await request.json()
    update = Update.de_json(data, ptb_app.bot)

    if _is_duplicate_update(update.update_id):
        return {"status": "duplicate_ignored"}

    await ptb_app.process_update(update)

    return {"status": "ok"}