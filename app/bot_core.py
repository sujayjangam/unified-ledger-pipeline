import truststore
truststore.inject_into_ssl()

import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# Load and Parse Secrets
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

raw_allowed_ids = os.getenv("ALLOWED_TG_IDS", "")
ALLOWED_IDS = [int(i.strip()) for i in raw_allowed_ids.split(",") if i.strip()]

# The Gatekeeper (Authorization Check)
async def is_authorized(update: Update):
    """Check if the user is in our allowed list."""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_IDS:
        print(f"🚫 Unauthorized access attempt by ID: {user_id}")
        await update.message.reply_text("You are not authorized to use this ledger. 🛑")
        return False
    return True

# Define Reflexes (Handlers)
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Answers to the /start command."""
    if not await is_authorized(update): return 
    
    user_id = update.effective_user.id
    username = update.effective_user.username
    print(f"DEBUG: Connection from {username} (ID: {user_id})")
    
    await update.message.reply_text(f"Hello {username}! Your ID is authorized by admin. Send me a voice note to get started.")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Logic that triggers when a voice note is received."""
    if not await is_authorized(update): return 
    
    await update.message.reply_text("Voice note received! Extracting expense... ⏳")
    
    # 📝 FUTURE STEP: Download the file and send to Whisper
    voice_file = await context.bot.get_file(update.message.voice.file_id)
    print(f"File ID received: {update.message.voice.file_id}")

# Engine Factory
def get_application():
    """Builds and returns the configured bot application."""
    if not TOKEN:
        raise ValueError("No token provided. Check your .env file!")
        
    app = ApplicationBuilder().token(TOKEN).build()
    
    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    
    return app