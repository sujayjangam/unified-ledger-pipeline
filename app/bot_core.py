import truststore
import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import tempfile
from openai import AsyncOpenAI
from services.transcription import transcribe_audio

truststore.inject_into_ssl()

# Load and Parse Secrets
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Initialize OpenAI client (it automatically looks for OPENAI_API_KEY in your environment)
openai_client = AsyncOpenAI()

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
    """Downloads the voice note, transcribes it, and cleans up."""
    if not await is_authorized(update): return 
    
    status_msg = await update.message.reply_text("Voice note received! Transcribing... 🎙️")
    voice_file = await context.bot.get_file(update.message.voice.file_id)
    
    # Create a temp file and IMMEDIATELY close it to release the Windows lock
    temp_audio = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
    temp_filepath = temp_audio.name
    temp_audio.close() # 🔓 Unlocks the file for Telegram to use
    
    try:
        # Telegram downloads and writes to the unlocked file
        await voice_file.download_to_drive(custom_path=temp_filepath)
        
        # Transcribe using our new reusable service (Replacing the hardcoded Whisper block)
        transcript_text = await transcribe_audio(temp_filepath)
            
        # Show the result to the user (with basic error handling)
        if transcript_text.startswith("ERROR:"):
            await status_msg.edit_text(f"⚠️ {transcript_text}")
        else:
            await status_msg.edit_text(f"📝 *Transcription:*\n{transcript_text}", parse_mode="Markdown")
    
    finally:
        # Clean up manually since we used delete=False
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)

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