import truststore
import os
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import tempfile
from openai import AsyncOpenAI
from add_expense import add_expense
from datetime import datetime, timedelta
from services.ledger_queries import get_recent_entries, get_period_summary

# use the local windows persmissions
truststore.inject_into_ssl()

# Load the secrets into memory FIRST
load_dotenv()

# NOW it is safe to import your custom services because the environment is ready
from services.transcription import transcribe_audio
from services.extraction import extract_transactions

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

async def recent_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gets just the past 5 transactions added by any user and displays them to user who called this command"""
    if not await is_authorized(update): return 
    entries = get_recent_entries(limit=5)
    
    if not entries:
        await update.message.reply_text("No transactions found. 📭")
        return

    lines = ["📊 **Recent Entries**\n"]
    for date, desc, amount_cents, cat in entries:
        lines.append(f"• `{date}`: {desc} (**${(amount_cents / 100.0):.2f}**)")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gets all transactions that have been recorded thus far today, and displays a summary of transactions"""
    if not await is_authorized(update): return 
    
    today_dt = datetime.now().strftime('%Y-%m-%d')
    entries, total = get_period_summary(today_dt, today_dt)
    
    lines = [f"📅 **Today's Summary** ({today_dt})\n"]
    for date, desc, amount_cents, cat in entries:
        lines.append(f"• {desc} (**${(amount_cents / 100.0):.2f}**)")
    lines.append(f"\n💰 **Total Spend: ${total:.2f}**")
    
    await update.message.reply_text("\n".join(lines) if entries else "No spending today! 🎉", parse_mode="Markdown")

async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gets all transactions that have been recorded thus far for this week (starts on Monday), and displays a summary of transactions"""
    if not await is_authorized(update): return 
    
    today = datetime.now()
    # Monday is 0, Sunday is 6. Subtract the weekday number to get back to Monday.
    start_of_week = today - timedelta(days=today.weekday())
    
    start_dt = start_of_week.strftime('%Y-%m-%d')
    end_dt = today.strftime('%Y-%m-%d')
    
    entries, total = get_period_summary(start_dt, end_dt)
    
    lines = [f"📆 **This Week** ({start_dt} to {end_dt})\n"]
    lines.append(f"Total Transactions: {len(entries)}")
    lines.append(f"💰 **Total Spend: ${total:.2f}**")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def month_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gets all transactions that have been recorded thus far for this month (starts on 1st of any month), and displays a summary of transactions"""
    if not await is_authorized(update): return 
    
    today = datetime.now()
    # Force the day to the 1st of the current month
    start_of_month = today.replace(day=1)
    
    start_dt = start_of_month.strftime('%Y-%m-%d')
    end_dt = today.strftime('%Y-%m-%d')
    
    entries, total = get_period_summary(start_dt, end_dt)
    
    lines = [f"🗓️ **This Month** ({start_dt} to {end_dt})\n"]
    lines.append(f"Total Transactions: {len(entries)}")
    lines.append(f"💰 **Total Spend: ${total:.2f}**")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# get voice message, transcribe, output summary and await confirmation to add to db
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Downloads the voice note, transcribes it, extracts structured data, and cleans up."""
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
        
        # Transcribe using our reusable service
        transcript_text = await transcribe_audio(temp_filepath)
        
        # Show the result to the user (with basic error handling)
        if transcript_text.startswith("ERROR:"):
            await status_msg.edit_text(f"⚠️ {transcript_text}")
            return # Stop execution if transcription fails
            
        # Update the user that we are moving to the next step
        await status_msg.edit_text(f"📝 *Transcript:* {transcript_text}\n\n🧠 Extracting data...", parse_mode="Markdown")
        
        # Extract structured data from the transcript
        structured_data = await extract_transactions(transcript_text)
        
        # Check if data exists AND if the 'transactions' list is present
        if not structured_data or not structured_data.get("transactions"):
            await status_msg.edit_text("❌ Failed to extract structured data from the transcript.")
            return
            
        # Extract the actual list from the wrapper
        transactions_list = structured_data["transactions"]
        
        # The V1 Gatekeeper to politely reject multiple expenses
        if len(transactions_list) > 1:
            await status_msg.edit_text(
                "⚠️ **Hold on!** I detected multiple expenses.\n\n"
                "To keep the ledger perfectly accurate for V1, please record just **one expense per voice note**. 🎙️", 
                parse_mode="Markdown"
            )
            return
            
        # Isolate the single approved transaction
        single_transaction = transactions_list[0]
        
        # Inject the raw, unedited transcript directly as the description
        single_transaction['description'] = transcript_text
        
        # Store the isolated transaction in memory, not the whole wrapper
        context.user_data['pending_transaction'] = single_transaction
        
        # Reference 'single_transaction' instead of 'structured_data' for the UI text
        summary_message = (
            f"Please confirm your expense:\n\n"
            f"💰 **Amount:** {float(single_transaction.get('amount')):.2f}\n"
            f"🏷️ **Category:** {single_transaction.get('category')}\n"
            f"📝 **Notes:** {single_transaction.get('description', 'None')}\n"
            f"📅 **Date:** {single_transaction.get('date')}\n"
        )
        
        # Create the interactive buttons
        keyboard = [
            [
                InlineKeyboardButton("✅ Confirm", callback_data="confirm_save"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_save")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Send the clean summary with the buttons attached
        await status_msg.edit_text(summary_message, reply_markup=reply_markup, parse_mode="Markdown")
            
    finally:
        # Clean up manually since we used delete=False
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)

# buttons!
async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes the Confirm or Cancel button presses."""
    query = update.callback_query
    await query.answer() 
    
    # when the user clicks on the confirm button, we get the pending transaction and save it to our database
    if query.data == "confirm_save":
        transaction_to_save = context.user_data.get('pending_transaction')
        
        if transaction_to_save:
            # await query.edit_message_text("✅ **Confirmed!** Transaction ready for the database.", parse_mode="Markdown")
            # save success will return True if done, or False if failed
            save_success = add_expense(
                date_str=transaction_to_save.get('date'),
                description=transaction_to_save.get('description'),
                amount_dollars=transaction_to_save.get('amount'),
                category=transaction_to_save.get('category'),
                currency=transaction_to_save.get('currency', 'SGD'),
                source="Telegram Bot" # CHANGED: Tag it so you know where it came from
            )

            if save_success: 
                # Reconstruct the full summary message to keep it in the chat history
                updated_summary = (
                    f"✅ **Saved to Ledger!**\n\n"
                    f"💰 **Amount:** {transaction_to_save.get('currency')} {float(transaction_to_save.get('amount')):.2f}\n"
                    f"🏷️ **Category:** {transaction_to_save.get('category')}\n"
                    f"📝 **Notes:** {transaction_to_save.get('description', 'None')}\n"
                    f"📅 **Date:** {transaction_to_save.get('date')}\n"
                )
                # Editing the text. By omitting 'reply_markup', Telegram automatically removes the buttons.
                await query.edit_message_text(updated_summary, parse_mode="Markdown")
            else:
                # CHANGED: Keep the summary visible even if the database fails, just change the header
                error_summary = (
                    f"❌ **Database Error! Could not save:**\n\n"
                    f"💰 **Amount:** {transaction_to_save.get('currency')} {float(transaction_to_save.get('amount')):.2f}\n"
                    f"🏷️ **Category:** {transaction_to_save.get('category')}\n"
                    f"📝 **Notes:** {transaction_to_save.get('description', 'None')}\n"
                    f"📅 **Date:** {transaction_to_save.get('date')}\n"
                )
                await query.edit_message_text(error_summary, parse_mode="Markdown")
            
            context.user_data.pop('pending_transaction', None)
            
        else:
            await query.edit_message_text("⚠️ Session expired or data lost. Please send the voice note again.")
            
    elif query.data == "cancel_save":
        await query.edit_message_text("❌ **Cancelled.** Nothing was saved to the Ledger.", parse_mode="Markdown")
        context.user_data.pop('pending_transaction', None)

# Engine Factory
def get_application():
    """Builds and returns the configured bot application."""
    if not TOKEN:
        raise ValueError("No token provided. Check your .env file!")
        
    app = ApplicationBuilder().token(TOKEN).build()
    
    # start handler
    app.add_handler(CommandHandler("start", start_command))

    # queries handler
    app.add_handler(CommandHandler("recent", recent_command))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("week", week_command))
    app.add_handler(CommandHandler("month", month_command))

    # voice handler
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    # handler for buttons
    app.add_handler(CallbackQueryHandler(handle_button_click))


    
    return app