import truststore
truststore.inject_into_ssl()

import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# 1. Load and Parse Secrets
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Convert the string "123,456" into a Python list of integers [123, 456]
raw_allowed_ids = os.getenv("ALLOWED_TG_IDS", "")
ALLOWED_IDS = [int(i.strip()) for i in raw_allowed_ids.split(",") if i.strip()]

# 2. The Gatekeeper (Authorization Check)
async def is_authorized(update: Update):
    """Check if the user is in our allowed list."""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_IDS:
        print(f"🚫 Unauthorized access attempt by ID: {user_id}")
        await update.message.reply_text("You are not authorized to use this ledger. 🛑")
        return False
    return True

# 3. Define Reflexes (Handlers)
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Answers to the /start command and shows the user's ID."""
    if not await is_authorized(update): return # GATEKEEPER
    
    user_id = update.effective_user.id
    username = update.effective_user.username
    print(f"DEBUG: Connection from {username} (ID: {user_id})")
    
    # await update.message.reply_text(f"Hello {username}! Your ID is {user_id}. Send me a voice note.")
    await update.message.reply_text(f"Hello {username}! Your ID is authorized by admin. Send me a voice note to get started.")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Logic that triggers when a voice note is received."""
    if not await is_authorized(update): return # GATEKEEPER
    
    # We acknowledge receipt immediately (UX best practice)
    await update.message.reply_text("Voice note received! Extracting expense... ⏳")
    
    # 📝 FUTURE STEP: This is where we will download the file and send to Whisper
    voice_file = await context.bot.get_file(update.message.voice.file_id)
    print(f"File ID received: {update.message.voice.file_id}")

# 4. Build and Run the Engine
if __name__ == '__main__':
    if not TOKEN:
        raise ValueError("No token provided. Check your .env file!")
        
    app = ApplicationBuilder().token(TOKEN).build()
    
    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    
    print("Bot logic initialized. Waiting for connection...")
    app.run_polling()