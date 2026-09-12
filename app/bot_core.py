import json
import truststore
import os
import uuid
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import tempfile
from openai import AsyncOpenAI
from app.add_expense import add_expense
from app.services.utils import get_sgt_now, get_week_start, get_month_start
from app.services.ledger_queries import get_recent_entries, get_period_summary, get_category_summary

# use the local windows persmissions
truststore.inject_into_ssl()

# Load the secrets into memory FIRST
load_dotenv()

try:
    ALLOWED_TG_IDS = json.loads(os.getenv("ALLOWED_TG_IDS", "{}"))
    ACCOUNT_OWNERS = json.loads(os.getenv("ACCOUNT_OWNERS", "{}"))
except json.JSONDecodeError:
    print("❌ Error: Invalid JSON format in .env file.")
    ALLOWED_TG_IDS, ACCOUNT_OWNERS = {}, {}

# The ACCOUNT_OWNERS key that funds shared transfers (e.g. YouTrip top-ups), regardless of
# who sent the message. Configurable rather than a hardcoded name so this repo doesn't bake
# in one household's real name, and so renaming that key in ACCOUNT_OWNERS doesn't require
# a code change.
PRIMARY_ACCOUNT_OWNER = os.getenv("PRIMARY_ACCOUNT_OWNER")


# NOW it is safe to import  custom services because the environment is ready (load_dotenv() already ran)
from app.services.transcription import transcribe_audio # noqa: E402 - load_dotenv() is required before this import
from app.services.extraction import extract_transactions # noqa: E402 - load_dotenv() is required before this import

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Initialize OpenAI client (it automatically looks for OPENAI_API_KEY in your environment)
openai_client = AsyncOpenAI()

raw_allowed_ids = os.getenv("ALLOWED_TG_IDS", "")
ALLOWED_IDS = ALLOWED_TG_IDS.keys()

# The Gatekeeper (Authorization Check)
async def is_authorized(update: Update):
    """Check if the user is in our allowed list."""
    user_id = str(update.effective_user.id)
    if user_id not in ALLOWED_IDS:
        print(f"🚫 Unauthorized access attempt by ID: {user_id}")
        await update.message.reply_text("You are not authorized to use this ledger. 🛑")
        return False
    return True

# Define Reflexes (Handlers)
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Answers to the /start command."""
    if not await is_authorized(update):
        return 
    
    user_id = update.effective_user.id
    username = update.effective_user.username
    print(f"DEBUG: Connection from {username} (ID: {user_id})")
    
    await update.message.reply_text(f"Hello {username}! Your ID is authorized by admin. Send me a voice note to get started.")

def format_period_summary(title: str, expenses: list, transfers: list) -> str:
    """Helper function to build a mobile-readable summary for any date range."""
    lines = [f"{title}\n"]
    
    # 🛒 Calculate and format expenses
    tx_count = sum(count for _, count, _ in expenses) if expenses else 0
    lines.append(f"🛒 **Expenses:** {tx_count} transactions")
    
    if expenses:
        for curr, count, total_cents in expenses:
            lines.append(f"  • {curr}: **{total_cents / 100.0:.2f}**")
    else:
        lines.append("  • No expenses.")
        
    # 🔄 Format transfers separately
    lines.append("\n🔄 **Transfers:**")
    if transfers:
        for curr, count, total_cents in transfers:
            lines.append(f"  • {curr}: **{total_cents / 100.0:.2f}** ({count} tx)")
    else:
        lines.append("  • No transfers.")
        
    return "\n".join(lines)

def format_category_summary(title: str, category_data: list) -> str:
    """Helper function to group category data by currency for mobile viewing."""
    if not category_data:
        return f"{title}\n\nNo expenses found for this period. 🎉"

    # 1. Group the flat SQL data using a dictionary
    # Structure: { 'Food': [('SGD', 3, 4500), ('MYR', 1, 1500)] }
    grouped_data = {}
    for cat, curr, count, total_cents in category_data:
        if cat not in grouped_data:
            grouped_data[cat] = []
        grouped_data[cat].append((curr, count, total_cents))

    lines = [f"{title}\n"]

    # 2. Build the visual text block
    for cat, currencies in grouped_data.items():
        # Calculate the total number of transactions for the whole category
        cat_tx_count = sum(c for _, c, _ in currencies)
        lines.append(f"📁 **{cat}** ({cat_tx_count} tx)")

        # List each currency total underneath the category header
        for curr, count, total_cents in currencies:
            lines.append(f"  • {curr}: **{total_cents / 100.0:.2f}**")
            
        lines.append("") # Blank line for readability

    return "\n".join(lines).strip()

async def recent_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update):
        return 
    
    entries = get_recent_entries(limit=5)
    if not entries:
        await update.message.reply_text("No transactions found. 📭")
        return

    lines = ["📊 **Recent Entries**\n"]
    for date, desc, amount_cents, currency, cat in entries:
        amt = amount_cents / 100.0
        # Clearly label transfers vs standard expenses
        if cat == 'Transfer':
            lines.append(f"• 🔄 `{date}`: {desc} (**{currency} {amt:.2f}**) [Transfer]")
        else:
            lines.append(f"• 🛒 `{date}`: {desc} (**{currency} {amt:.2f}**)")
            
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update):
        return 
    
    today_str = get_sgt_now().strftime('%Y-%m-%d')
    expenses, transfers = get_period_summary(today_str, today_str)
    
    text = format_period_summary(f"📅 **Today's Summary** ({today_str})", expenses, transfers)
    await update.message.reply_text(text, parse_mode="Markdown")

async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update):
        return 
    
    today = get_sgt_now()
    start_of_week = get_week_start(today)

    start_str = start_of_week.strftime('%Y-%m-%d')
    end_str = today.strftime('%Y-%m-%d')
    
    expenses, transfers = get_period_summary(start_str, end_str)
    
    text = format_period_summary(f"📆 **This Week** ({start_str} to {end_str})", expenses, transfers)
    await update.message.reply_text(text, parse_mode="Markdown")

async def month_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update):
        return 
    
    today = get_sgt_now()
    start_of_month = get_month_start(today)

    start_str = start_of_month.strftime('%Y-%m-%d')
    end_str = today.strftime('%Y-%m-%d')
    
    expenses, transfers = get_period_summary(start_str, end_str)
    
    text = format_period_summary(f"🗓️ **This Month** ({start_str} to {end_str})", expenses, transfers)
    await update.message.reply_text(text, parse_mode="Markdown")

async def cat_today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update):
        return 
    
    today_str = get_sgt_now().strftime('%Y-%m-%d')
    cat_data = get_category_summary(today_str, today_str)
    
    text = format_category_summary(f"📊 **Today's Categories** ({today_str})", cat_data)
    await update.message.reply_text(text, parse_mode="Markdown")

async def cat_week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update):
        return 
    
    today = get_sgt_now()
    start_of_week = get_week_start(today)

    start_str = start_of_week.strftime('%Y-%m-%d')
    end_str = today.strftime('%Y-%m-%d')
    
    cat_data = get_category_summary(start_str, end_str)
    
    text = format_category_summary(f"📊 **This Week's Categories** ({start_str} to {end_str})", cat_data)
    await update.message.reply_text(text, parse_mode="Markdown")

async def cat_month_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update):
        return 
    
    today = get_sgt_now()
    start_of_month = get_month_start(today)

    start_str = start_of_month.strftime('%Y-%m-%d')
    end_str = today.strftime('%Y-%m-%d')
    
    cat_data = get_category_summary(start_str, end_str)
    
    text = format_category_summary(f"📊 **This Month's Categories** ({start_str} to {end_str})", cat_data)
    await update.message.reply_text(text, parse_mode="Markdown")

def apply_payment_defaults(transaction: dict, spender_name: str) -> dict:
    """Fills in payment_method and account_owner on an extracted transaction dict, mutating
    and returning it in place.

    Pulled out of process_expense_text() as a standalone, synchronous function (no Update/
    context, no `await`) specifically so this logic can be unit-tested directly on plain
    dicts, without needing to fake a full Telegram Update.
    """
    if transaction.get('category') == 'YouTrip top-up':
        # YouTrip top-ups are always paid from the primary account and are a balance
        # transfer, not a spend - so the extracted category always wins over whatever
        # payment method (if any) the LLM guessed.
        transaction['payment_method'] = ACCOUNT_OWNERS[PRIMARY_ACCOUNT_OWNER][0]
        transaction['transaction_type'] = 'Transfer'

    elif not transaction.get('payment_method'):
        # If no method was extracted, apply currency/user defaults
        if transaction.get('currency', 'SGD') != 'SGD':
            # Non-SGD spend with no stated payment method is almost always YouTrip in
            # practice (a multi-currency wallet), so default to it rather than asking.
            transaction['payment_method'] = 'YouTrip'
        else:
            # Dynamically pull the user's default card (Index 0)
            # Fallback to ["Cash"] if the user isn't in the dictionary
            user_accounts = ACCOUNT_OWNERS.get(spender_name, ["Cash"])
            transaction['payment_method'] = user_accounts[0]

    # now that we have the payment method, look up the account_owner - e.g. if one person
    # logs a transaction on another household member's card, the account_owner is whoever
    # owns that card, not whoever sent the message
    extracted_account = transaction.get('payment_method')

    if extracted_account and extracted_account.lower() == 'cash':
        # Cash isn't tied to any one account in ACCOUNT_OWNERS, so it's always attributed
        # to whoever sent the message, regardless of what accounts they have on file.
        transaction['account_owner'] = spender_name
    else:
        account_owner = "Unknown"
        for owner, accounts_list in ACCOUNT_OWNERS.items():
            if extracted_account.lower() in [account.lower() for account in accounts_list]:
                account_owner = owner
                break
        transaction['account_owner'] = account_owner

    return transaction

# --- Confirmation-card state -------------------------------------------------------------
#
# Both caches below live in context.user_data (per Telegram user, pure process memory - the
# bot configures no PTB persistence) and are keyed by the message_id of the card the buttons
# are attached to. Keying by card rather than keeping a single "current" transaction is what
# lets two cards be on screen and confirmable at the same time, in any order: today one
# message produces one card, but dropping the one-expense-per-message guardrail will have a
# single message fan out into several cards, and this handler needs no change for that.
#
# They are bounded for the same reason bot_webhook.py bounds its _seen_update_ids cache:
# user_data survives for the whole life of the process and nothing else ever prunes it, so
# an unbounded dict is a slow leak. Plain dicts are insertion-ordered (3.7+), so the oldest
# key is simply the first one.
_CARD_CACHE_MAXLEN = 20


def _bounded_put(cache: dict, key, value):
    """Stores key -> value, evicting oldest-first once the cache is over its bound."""
    cache[key] = value
    while len(cache) > _CARD_CACHE_MAXLEN:
        del cache[next(iter(cache))]  # first key = oldest insertion


def _remember_pending_card(context: ContextTypes.DEFAULT_TYPE, message_id: int, transaction: dict):
    """Records a transaction as awaiting confirmation on the card with this message_id."""
    _bounded_put(context.user_data.setdefault('pending_cards', {}), message_id, transaction)


def _take_pending_card(context: ContextTypes.DEFAULT_TYPE, message_id: int):
    """Removes and returns this card's pending transaction, or None if it has none.

    Removing on read is what stops the same card being saved twice: whichever call gets here
    first takes the transaction, and any later one sees None. It is a safety net rather than
    the primary guard - PTB processes updates one at a time here (concurrent_updates is never
    enabled, and add_expense is synchronous), and the real duplicate-write protection is the
    idempotency_key, per ADR-0011.
    """
    return context.user_data.get('pending_cards', {}).pop(message_id, None)


def _mark_card_resolved(context: ContextTypes.DEFAULT_TYPE, message_id: int, outcome: str):
    """Records how this card ended - 'saved' or 'cancelled' - so a later tap on it can say
    what actually happened instead of guessing that the entry was lost."""
    _bounded_put(context.user_data.setdefault('resolved_cards', {}), message_id, outcome)


def _card_outcome(context: ContextTypes.DEFAULT_TYPE, message_id: int):
    """Returns 'saved'/'cancelled' if this card is already finished with, else None."""
    return context.user_data.get('resolved_cards', {}).get(message_id)


def _confirm_keyboard() -> InlineKeyboardMarkup:
    """The Confirm/Cancel pair a pending card carries. Built by a function rather than held
    as a constant because the failure path re-attaches it after it has been swapped out."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data="confirm_save"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_save"),
    ]])


def _saving_keyboard() -> InlineKeyboardMarkup:
    """The placeholder shown while the save is in flight. It replaces Confirm/Cancel, so the
    user gets an immediate reaction and can no longer tap Confirm at all (issue #55); its
    callback_data exists only so an impatient tap on it can be answered with a toast."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⏳ Adding to ledger...", callback_data="saving_in_progress")
    ]])


# shared pipeline: raw text (transcript or typed) -> extraction -> pending confirmation card
async def process_expense_text(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str, status_msg):
    """Extracts structured data from raw text and presents it for confirmation.

    'raw_text' is a Whisper transcript when called from handle_voice and the message body
    when called from handle_text - past this point the two are indistinguishable, which is
    the whole reason this lives outside either handler.

    'status_msg' is passed in rather than created here because each transport shows a
    different message while it works (voice echoes the transcript back, text has nothing to
    echo). Every branch below edits that same message instead of sending a new one, so the
    user is always left with exactly one message per entry attempt.
    """
    # Extract structured data from the raw text
    structured_data = await extract_transactions(raw_text)

    # Check if data exists AND if the 'transactions' list is present
    if not structured_data or not structured_data.get("transactions"):
        await status_msg.edit_text("❌ Failed to extract structured data from your message.")
        return

    # Extract the actual list from the wrapper and the spender
    transactions_list = structured_data["transactions"]
    user_id = str(update.effective_user.id)
    spender_name = ALLOWED_TG_IDS.get(user_id, "Unknown")

    # The V1 Gatekeeper to politely reject multiple expenses
    if len(transactions_list) > 1:
        await status_msg.edit_text(
            "⚠️ **Hold on!** I detected multiple expenses.\n\n"
            "To keep the ledger perfectly accurate for V1, please send just **one expense at a time**. 🎙️",
            parse_mode="Markdown"
        )
        return

    # Isolate the single approved transaction
    single_transaction = transactions_list[0]

    # Inject the raw, unedited input directly as the description
    single_transaction['description'] = raw_text

    # handle scenarios where the amount spent was not picked up
    if single_transaction.get('amount') is None:
        await status_msg.edit_text("⚠️ I couldn't detect an exact amount. Could you try sending it again?")
        return # Stop processing this transaction

    # if the transaction is in non SGD currency, for now we automatically assume it's made with YouTrip, unless payment method already mentioned e.g. Cash
    single_transaction = apply_payment_defaults(single_transaction, spender_name)

    # One idempotency key per confirm prompt (not per save attempt) - reused on every
    # save attempt for this same prompt, so a double-tap or webhook retry can't insert twice.
    single_transaction['idempotency_key'] = str(uuid.uuid4())

    # Who sent this message, as their Telegram user ID - the same string form ALLOWED_TG_IDS uses
    # for its keys. Saved on the row as entered_by (#60) so an entry can always be traced back to
    # its sender; account_owner (whose card paid) is inferred and is not the same thing. The ID
    # rather than a display name because it never changes - names are looked up from it when shown.
    single_transaction['entered_by'] = user_id


    # Reference 'single_transaction', in future this will read 'transactions' when support for multiple txn is added
    summary_message = (
        f"Please confirm your **{single_transaction.get('transaction_type', 'Expense')}**:\n\n"
        f"💰 **Amount:** {single_transaction.get('currency')} {float(single_transaction.get('amount')):.2f}\n"
        f"🏷️ **Category:** {single_transaction.get('category')}\n"
        f"💳 **Account:** {single_transaction.get('payment_method', 'Unspecified')}\n"
        f"💳 **Account Owner:** {single_transaction.get('account_owner', 'Unspecified')}\n"
        f"📝 **Notes:** {single_transaction.get('description', 'None')}\n"
        f"📅 **Date:** {single_transaction.get('date')}\n"
    )

    # Send the clean summary with the Confirm/Cancel buttons attached
    await status_msg.edit_text(summary_message, reply_markup=_confirm_keyboard(), parse_mode="Markdown")

    # Stash the transaction against *this card's* message_id, only once the card exists - the
    # button press that comes back carries the message_id and nothing else, so that is the
    # only handle handle_button_click has for finding the right transaction again.
    _remember_pending_card(context, status_msg.message_id, single_transaction)

# get voice message, transcribe, then hand the transcript to the shared pipeline above
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Downloads the voice note, transcribes it, and passes the transcript on for extraction."""
    if not await is_authorized(update):
        return

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

        # From here on a transcript is just text - identical to a typed message
        await process_expense_text(update, context, transcript_text, status_msg)

    finally:
        # Clean up manually since we used delete=False
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)

# typed expense entry - no transcription step, the message body IS the input
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Passes a plain text message through the same pipeline a voice note uses."""
    if not await is_authorized(update):
        return

    status_msg = await update.message.reply_text("🧠 Extracting data...")
    await process_expense_text(update, context, update.message.text, status_msg)

# anything that is neither a voice note nor text - these used to be dropped silently
async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tells the user which input types the bot actually accepts, instead of ignoring them."""
    if not await is_authorized(update):
        return

    await update.message.reply_text(
        "⚠️ I can only read voice notes and text messages right now.\n"
        "Receipt photos are planned but not supported yet."
    )

# buttons!
async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes the Confirm/Cancel presses on a pending-transaction card.

    A callback_query_id can be answered exactly once - a second answer() on the same query
    fails outright - so every branch below works out what it wants to say *before* spending
    that single answer. Getting this wrong is what made a double-tap overwrite a correct
    "Saved to Ledger!" card with a false error (issue #29).
    """
    query = update.callback_query
    message_id = query.message.message_id

    # The placeholder button shown while a save is in flight. Reaching it means the user got
    # impatient, not that anything is wrong: say so in a toast and leave the card completely
    # alone, because the save that is still running owns it.
    if query.data == "saving_in_progress":
        await query.answer("⏳ Adding your expense, one moment...")
        return

    if query.data == "confirm_save":
        # A card whose outcome is already settled must not be processed again, and - just as
        # importantly - must not have its result message overwritten. This is the branch that
        # used to claim the session had expired when the entry had in fact just been saved.
        outcome = _card_outcome(context, message_id)
        if outcome == "saved":
            await query.answer("✅ Already saved to the ledger.")
            return
        if outcome == "cancelled":
            await query.answer("❌ This entry was cancelled.")
            return

        transaction_to_save = _take_pending_card(context, message_id)

        if transaction_to_save is None:
            # Genuinely nothing to save and no record of ever having saved it: the bot has
            # restarted since this card was sent (user_data is process memory only), or the
            # card is old enough to have been evicted from the cache.
            await query.answer()
            await query.edit_message_text(
                "⚠️ This entry is no longer available - the bot restarted after it was sent. "
                "Please send it again."
            )
            return

        # Answer first so the button's loading spinner clears, then swap Confirm/Cancel for a
        # non-actionable progress label. Both of these land *before* the blocking add_expense
        # call below, which is the whole point: the user sees an immediate reaction, and
        # Confirm is physically gone from the card so it cannot be tapped twice (issue #55).
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=_saving_keyboard())

        # save success will return True if done, or False if failed
        save_success = add_expense(
            date_str=transaction_to_save.get('date'),
            description=transaction_to_save.get('description'),
            amount_dollars=transaction_to_save.get('amount'),
            category=transaction_to_save.get('category'),
            currency=transaction_to_save.get('currency', 'SGD'),
            transaction_type=transaction_to_save.get('transaction_type', 'Expense'),
            account_desc=transaction_to_save.get('payment_method'),
            account_owner=transaction_to_save.get('account_owner'),
            source="Telegram Bot",
            idempotency_key=transaction_to_save.get('idempotency_key'),
            entered_by=transaction_to_save.get('entered_by')
        )

        if save_success:
            _mark_card_resolved(context, message_id, "saved")
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
            # Put the entry back and hand the buttons back with it, so a transient database
            # failure costs a retry rather than the whole entry. It keeps its original
            # idempotency_key, so retrying cannot write a second row even if the first attempt
            # actually reached Postgres before failing. Deliberately not marked resolved - the
            # card is live again.
            _remember_pending_card(context, message_id, transaction_to_save)
            # CHANGED: Keep the summary visible even if the database fails, just change the header
            error_summary = (
                f"❌ **Database Error! Could not save:**\n\n"
                f"💰 **Amount:** {transaction_to_save.get('currency')} {float(transaction_to_save.get('amount')):.2f}\n"
                f"🏷️ **Category:** {transaction_to_save.get('category')}\n"
                f"📝 **Notes:** {transaction_to_save.get('description', 'None')}\n"
                f"📅 **Date:** {transaction_to_save.get('date')}\n\n"
                f"Tap **✅ Confirm** to try again."
            )
            await query.edit_message_text(error_summary, parse_mode="Markdown", reply_markup=_confirm_keyboard())

    elif query.data == "cancel_save":
        # Same already-settled check as the confirm path, so a second tap on Cancel doesn't
        # relabel a card that was in fact saved.
        outcome = _card_outcome(context, message_id)
        if outcome == "saved":
            await query.answer("✅ This entry was already saved to the ledger.")
            return
        if outcome == "cancelled":
            await query.answer("❌ Already cancelled.")
            return

        await query.answer()
        _take_pending_card(context, message_id)
        _mark_card_resolved(context, message_id, "cancelled")
        await query.edit_message_text("❌ **Cancelled.** Nothing was saved to the Ledger.", parse_mode="Markdown")

    else:
        # An unrecognised callback_data - a card from an older deployment whose buttons this
        # version no longer knows about. Answer anyway: an unanswered query leaves the button
        # spinning on the user's screen indefinitely.
        await query.answer("⚠️ This button is no longer supported. Please send the entry again.")

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
    app.add_handler(CommandHandler("cat_today", cat_today_command))
    app.add_handler(CommandHandler("cat_week", cat_week_command))
    app.add_handler(CommandHandler("cat_month", cat_month_command))

    # voice handler
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    # text handler - ~filters.COMMAND keeps /recent, /today etc. on their CommandHandlers above
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # everything else (photo, video, sticker, document, location) - these matched no handler
    # at all before, so PTB dropped them silently and the user got no reply whatsoever
    app.add_handler(MessageHandler(~filters.VOICE & ~filters.TEXT, handle_unsupported))

    # handler for buttons
    app.add_handler(CallbackQueryHandler(handle_button_click))

    return app