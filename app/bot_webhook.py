import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from telegram import Update
from bot_core import get_application

# Fetch our bot engine
ptb_app = get_application()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")

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
    
    await ptb_app.process_update(update)
    
    return {"status": "ok"}