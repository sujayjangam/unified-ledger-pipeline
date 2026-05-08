import truststore #ensure that this will work with NextDNS
truststore.inject_into_ssl()

import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# 1. Load Secrets. Define TOKEN constant
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# 2. Define Reflexes (Handlers)
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Answers to the /start command."""
    await update.message.reply_text("Unified Ledger Bot is online! 🌴 Send me a voice note.")

# 3. Build and Run the Engine
if __name__ == '__main__':
    if not TOKEN:
        raise ValueError("No token provided. Check your .env file!")
        
    print("Initializing bot engine...")
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    
    print("Bot is polling... (Press Ctrl+C to stop)")
    app.run_polling()