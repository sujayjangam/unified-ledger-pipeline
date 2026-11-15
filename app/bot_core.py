import truststore
import os
import uuid
from dataclasses import replace
from dotenv import load_dotenv
from telegram import BotCommand, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.helpers import escape_markdown
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import tempfile
import time
from app.add_expense import add_expense, dollars_to_cents, format_cents
from app.services.utils import get_sgt_now, get_week_start, get_month_start
from app.services.ledger_queries import get_recent_entries, get_period_summary, get_category_summary, find_recent_duplicate
from app.services.dates import apply_date_resolution
from app.database import get_connection
from app.services.participants import (
    CLOSED_CARD_WARNING, NO_WALLET_MESSAGE, Directory, WalletNotSetUp, load_directory, resolve_payment,
    NO_OUTSIDE_ACCOUNT_MESSAGE, PAYER_UNCLEAR_WARNING, OutsideAccountNotSetUp, read_payer,
)
from app.services.trips import (
    Trip, load_active_trip, trip_member_ids, switch_trip_off, switch_trip_on, member_trips,
    banner_message_id, set_banner_message_id, format_trip_dates,
)

# use the local windows persmissions
truststore.inject_into_ssl()

# Load the secrets into memory FIRST
load_dotenv()

# NOW it is safe to import  custom services because the environment is ready (load_dotenv() already ran)
from app.services.transcription import transcribe_audio # noqa: E402 - load_dotenv() is required before this import
from app.services.extraction import extract_transactions # noqa: E402 - load_dotenv() is required before this import

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

DIRECTORY_DOWN_MESSAGE = "Couldn't reach the ledger, try again in a minute. 🛠️"


def _load_directory() -> Directory:
    """Reads the participants and accounts tables (#53, ADR-0032).

    Read fresh on every message rather than cached at start-up, so a card added to the accounts
    table is usable on the very next message with no redeploy. It is two small SELECTs, next to an
    OpenAI call that takes seconds. Kept as its own function so tests can swap it for a fixture -
    they must never open a real connection (tests/conftest.py).
    """
    with get_connection() as conn:
        return load_directory(conn)


def _load_active_trip(participant_id: int) -> Trip | None:
    """Reads which trip, if any, is on. Read fresh for every entry, like the directory, so a
    /trip switch by this person applies to the very next message. Its own function so tests can
    swap it out without a database."""
    with get_connection() as conn:
        trip = load_active_trip(conn, participant_id)
        # Read members in this connection, without another checkout for payer inference.
        return replace(trip, member_ids=trip_member_ids(conn, trip.id)) if trip else None


# Shown on the card when the trip couldn't be read. Not a refusal: missing a trip tag can be put
# right in SQL afterwards, but a bot that refuses entries mid-trip means nothing gets logged.
TRIP_UNKNOWN_WARNING = "⚠️ Couldn't check the active trip, so this entry isn't tagged to one."


# The Gatekeeper (Authorization Check)
async def authorize(update: Update) -> Directory | None:
    """Lets a message through only if its sender is a participant with this Telegram ID.

    Returns the Directory for the handler to use, or None after replying - the handler then
    stops. This fails CLOSED: if the tables can't be read (Neon down, or the rows fail
    validation) nobody gets in, because the allowlist itself lives in those tables and letting
    everyone through whenever it can't be checked would make it worthless.
    """
    try:
        directory = _load_directory()
    except Exception as e:
        # Logged in full for the Cloud Run logs; the user just gets "try again".
        print(f"❌ Could not load participants/accounts: {e}")
        await update.effective_message.reply_text(DIRECTORY_DOWN_MESSAGE)
        return None

    user_id = str(update.effective_user.id)
    if directory.participant_by_telegram_id(user_id) is None:
        print(f"🚫 Unauthorized access attempt by ID: {user_id}")
        await update.effective_message.reply_text("You are not authorized to use this ledger. 🛑")
        return None
    return directory

# Define Reflexes (Handlers)
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Answers to the /start command."""
    if await authorize(update) is None:
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
    if await authorize(update) is None:
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
    if await authorize(update) is None:
        return 
    
    today_str = get_sgt_now().strftime('%Y-%m-%d')
    expenses, transfers = get_period_summary(today_str, today_str)
    
    text = format_period_summary(f"📅 **Today's Summary** ({today_str})", expenses, transfers)
    await update.message.reply_text(text, parse_mode="Markdown")

async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await authorize(update) is None:
        return 
    
    today = get_sgt_now()
    start_of_week = get_week_start(today)

    start_str = start_of_week.strftime('%Y-%m-%d')
    end_str = today.strftime('%Y-%m-%d')
    
    expenses, transfers = get_period_summary(start_str, end_str)
    
    text = format_period_summary(f"📆 **This Week** ({start_str} to {end_str})", expenses, transfers)
    await update.message.reply_text(text, parse_mode="Markdown")

async def month_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await authorize(update) is None:
        return 
    
    today = get_sgt_now()
    start_of_month = get_month_start(today)

    start_str = start_of_month.strftime('%Y-%m-%d')
    end_str = today.strftime('%Y-%m-%d')
    
    expenses, transfers = get_period_summary(start_str, end_str)
    
    text = format_period_summary(f"🗓️ **This Month** ({start_str} to {end_str})", expenses, transfers)
    await update.message.reply_text(text, parse_mode="Markdown")

async def cat_today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await authorize(update) is None:
        return 
    
    today_str = get_sgt_now().strftime('%Y-%m-%d')
    cat_data = get_category_summary(today_str, today_str)
    
    text = format_category_summary(f"📊 **Today's Categories** ({today_str})", cat_data)
    await update.message.reply_text(text, parse_mode="Markdown")

async def cat_week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await authorize(update) is None:
        return 
    
    today = get_sgt_now()
    start_of_week = get_week_start(today)

    start_str = start_of_week.strftime('%Y-%m-%d')
    end_str = today.strftime('%Y-%m-%d')
    
    cat_data = get_category_summary(start_str, end_str)
    
    text = format_category_summary(f"📊 **This Week's Categories** ({start_str} to {end_str})", cat_data)
    await update.message.reply_text(text, parse_mode="Markdown")

async def cat_month_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await authorize(update) is None:
        return 
    
    today = get_sgt_now()
    start_of_month = get_month_start(today)

    start_str = start_of_month.strftime('%Y-%m-%d')
    end_str = today.strftime('%Y-%m-%d')
    
    cat_data = get_category_summary(start_str, end_str)
    
    text = format_category_summary(f"📊 **This Month's Categories** ({start_str} to {end_str})", cat_data)
    await update.message.reply_text(text, parse_mode="Markdown")

# --- Confirmation-card state -------------------------------------------------------------
#
def _md(name: str) -> str:
    """A name escaped for Telegram's Markdown, so a trip called "Beach_2026" shows as typed. An
    unescaped _ or * starts a format that never closes, and Telegram then rejects the whole
    message - on a card, that would stop every entry while the trip is on. Only for text sent with
    parse_mode="Markdown"; button labels and toasts are plain text and need none."""
    return escape_markdown(name, version=1)


BOT_COMMANDS = [
    BotCommand("start", "Start using the ledger"),
    BotCommand("recent", "Recent entries"),
    BotCommand("today", "Today's summary"),
    BotCommand("week", "This week's summary"),
    BotCommand("month", "This month's summary"),
    BotCommand("cat_today", "Today's categories"),
    BotCommand("cat_week", "This week's categories"),
    BotCommand("cat_month", "This month's categories"),
    BotCommand("trip", "Change your trip mode"),
]


async def register_commands(bot):
    """A command-menu failure must not prevent either transport from starting."""
    try:
        await bot.set_my_commands(BOT_COMMANDS)
    except Exception as e:
        print(f"Could not register bot commands: {e}")


TRIP_PAGE_SIZE = 3
TRIP_CHANGE_ERROR = "❌ Couldn't change trip mode, try again."
TRIP_BANNER_WARNING = "⚠️ Couldn't update the pinned banner."


def _trip_status(trip, today):
    """Use the same escaped status in the menu and the pinned reminder."""
    if trip:
        return f"🧳 Trip mode is on: **{_md(trip.name)}** ({format_trip_dates(trip, today)})"
    return "🏠 Trip mode is off."


def _trip_keyboard(choices, active, offset, more, today):
    """One shared layout for commands and taps; callback payloads contain only ids."""
    if not choices and offset == 0:
        return None
    rows = [[InlineKeyboardButton(
        ("✅ " if active and trip.id == active.id else "")
        + f"{trip.name} · {format_trip_dates(trip, today)}",
        callback_data=f"trip:on:{trip.id}",
    )] for trip in choices[:TRIP_PAGE_SIZE]]
    paging = []
    if offset > 0:
        paging.append(InlineKeyboardButton("◂ Newer", callback_data=f"trip:page:{max(0, offset - TRIP_PAGE_SIZE)}"))
    if more:
        paging.append(InlineKeyboardButton("Older ▸", callback_data=f"trip:page:{offset + TRIP_PAGE_SIZE}"))
    if paging:
        rows.append(paging)
    if active:
        rows.append([InlineKeyboardButton("🏠 Turn off trip mode", callback_data="trip:off")])
    return InlineKeyboardMarkup(rows)


def _trip_menu(active, choices, more, today):
    """Page zero is also the reset destination after changing a switch."""
    status = _trip_status(active, today)
    if not choices:
        status += "\n\nYou're not on any trips yet."
    return status, _trip_keyboard(choices, active, 0, more, today)


def _ms(start, end) -> str:
    """Elapsed time between two time.perf_counter() readings, for the timing log lines."""
    return f"{(end - start) * 1000:.0f}ms"


async def trip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Always show this sender's buttons, including when old-style arguments were supplied."""
    started = time.perf_counter()
    directory = await authorize(update)
    if directory is None:
        return
    authed = time.perf_counter()
    sender = directory.participant_by_telegram_id(str(update.effective_user.id))
    try:
        with get_connection() as conn:
            active = load_active_trip(conn, sender.id)
            choices, more = member_trips(conn, sender.id, 0, TRIP_PAGE_SIZE)
        status, keyboard = _trip_menu(active, choices, more, get_sgt_now().date())
    except Exception as e:
        print(f"Could not load trip menu: {e}")
        await update.effective_message.reply_text(TRIP_CHANGE_ERROR)
        return
    db_done = time.perf_counter()
    await update.effective_message.reply_text(status, parse_mode="Markdown", reply_markup=keyboard)
    print(f"/trip menu: auth {_ms(started, authed)}, db {_ms(authed, db_done)}, "
          f"send {_ms(db_done, time.perf_counter())}")


async def _remove_old_banner(bot, chat_id, message_id, previous) -> bool:
    """Gets rid of the banner from the trip that was on before this switch. Returns False only
    when the banner couldn't be removed or retired at all.

    Deleting it is the first choice: once trip mode is off, a message saying it is on is only
    clutter, and deleting a pinned message also unpins it. But Telegram lets a bot delete a
    message only within 48 hours of sending it, and a trip usually runs longer. Then the banner
    is unpinned and edited to say the trip ended, so the chat history doesn't still claim trip
    mode is on.
    """
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except Exception as e:
        # Expected for a banner older than 48 hours; fall through to retiring it instead.
        print(f"Could not delete old trip banner, retiring it: {e}")
    name = f" for **{_md(previous.name)}**" if previous else ""
    try:
        await bot.unpin_chat_message(chat_id=chat_id, message_id=message_id)
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=f"🏠 Trip mode{name} ended.", parse_mode="Markdown")
        return True
    except Exception as e:
        print(f"Could not retire old trip banner: {e}")
        return False


async def _update_trip_banner(bot, chat_id, participant_id, active, previous, today) -> bool:
    """Swaps the pinned banner after a committed switch: removes the old trip's banner, then pins
    a new one if a trip is now on. Returns False when any step failed, so the menu can warn.

    'chat_id' is always the participant's private chat with the bot (their Telegram user id),
    never the chat the tap came from: Telegram message ids only mean something inside one chat,
    so a stored banner id used against another chat could delete an unrelated message there.

    'previous' is the trip that was on before the switch (None if none), used to word a banner
    that can no longer be deleted. Each Telegram step is tried on its own, so a missing old
    message can't stop the new banner being sent. A sent message's id is stored even if pinning
    it fails, so the next switch can still clean it up.
    """
    ok = True
    try:
        with get_connection() as conn:
            old_id = banner_message_id(conn, participant_id)
    except Exception as e:
        print(f"Could not read trip banner id: {e}")
        old_id, ok = None, False
    if old_id is not None and not await _remove_old_banner(bot, chat_id, old_id, previous):
        ok = False
    new_id = None
    if active:
        try:
            message = await bot.send_message(
                chat_id=chat_id,
                text=_trip_status(active, today) + ". New entries are tagged to it. Send /trip to change.",
                parse_mode="Markdown",
            )
            new_id = message.message_id
            await bot.pin_chat_message(chat_id=chat_id, message_id=new_id, disable_notification=True)
        except Exception as e:
            print(f"Could not send/pin trip banner: {e}")
            ok = False
    try:
        with get_connection() as conn:
            set_banner_message_id(conn, participant_id, new_id)
    except Exception as e:
        print(f"Could not store trip banner id: {e}")
        ok = False
    return ok


async def handle_trip_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Acknowledge once, after deciding the outcome; expense-card toggles use another handler."""
    query = update.callback_query
    started = time.perf_counter()
    directory = await authorize(update)
    if directory is None:
        await query.answer()
        return
    authed = time.perf_counter()
    sender = directory.participant_by_telegram_id(str(update.effective_user.id))
    today = get_sgt_now().date()
    changed = False
    toast = None
    try:
        parts = query.data.split(":")
        action = parts[1]
        value = int(parts[2]) if len(parts) == 3 else None
        if not (action == "off" and len(parts) == 2 or
                action in ("on", "page") and value is not None and 0 <= value <= 2**63 - 1):
            raise ValueError("Invalid trip callback")
        offset = value if action == "page" else 0
        with get_connection() as conn:
            active = load_active_trip(conn, sender.id)
            # The trip on before this tap, for wording an old banner that can't be deleted.
            previous = active
            if action == "on" and active and active.id == value:
                toast = "Already on."
                choices, more = [], False
            else:
                # Read the menu before committing: a failed read must not leave a changed
                # switch behind a 'couldn't change' toast. No trip queries follow the commit.
                choices, more = member_trips(conn, sender.id, offset, TRIP_PAGE_SIZE)
                if action == "on":
                    selected = switch_trip_on(conn, sender.id, value)
                    if selected is None:
                        toast = "You're not on that trip."
                    else:
                        active, changed, toast = selected, True, "Trip mode is on."
                elif action == "off":
                    previous = switch_trip_off(conn, sender.id)
                    active = None
                    changed = previous is not None
                    toast = "Trip mode is off." if changed else "Trip mode was already off."
    except Exception as e:
        print(f"Could not change trip mode: {e}")
        await query.answer(TRIP_CHANGE_ERROR)
        return

    db_done = time.perf_counter()

    # Answer as soon as the outcome is known: the button keeps spinning until answer() runs,
    # and the banner swap below is several Telegram round trips. A banner failure is shown in
    # the message text instead, since a query can only be answered once. A failed answer (a tap
    # Telegram already considers stale) must not skip the redraw or banner of a committed switch.
    try:
        await query.answer(toast)
    except Exception as e:
        print(f"Could not answer trip tap: {e}")
    answered = time.perf_counter()

    status, keyboard = _trip_menu(active, choices, more, today)
    if toast == "Already on.":
        # An older menu tapped for the trip that is already on: collapse it like any other.
        try:
            # _trip_status, not the menu's status: no trip list was read for this tap.
            await query.edit_message_text(
                "✅ " + _trip_status(active, today), parse_mode="Markdown", reply_markup=None)
        except Exception as e:
            print(f"Could not redraw trip menu: {e}")
        return
    if action != "page" and toast != "You're not on that trip.":
        # A switch was made (or off was already off): the menu has done its job, so it becomes a
        # plain status line with no buttons, saving screen space. This is an edit, not a delete,
        # so Telegram's 48-hour limit on deleting messages doesn't apply.
        status = ("✅ " + status) if active else status
        keyboard = None
    try:
        if action == "page":
            await query.edit_message_reply_markup(
                reply_markup=_trip_keyboard(choices, active, offset, more, today))
        else:
            await query.edit_message_text(status, parse_mode="Markdown", reply_markup=keyboard)
    except Exception as e:
        # Telegram redraws cannot undo a committed switch or prevent its banner update.
        print(f"Could not redraw trip menu: {e}")
    redrawn = time.perf_counter()
    if changed:
        if not await _update_trip_banner(context.bot, int(sender.telegram_user_id), sender.id, active, previous, today):
            try:
                await query.edit_message_text(
                    status + "\n\n" + TRIP_BANNER_WARNING, parse_mode="Markdown", reply_markup=keyboard)
            except Exception as e:
                print(f"Could not show trip banner warning: {e}")
    # Where the time goes, so slowness is measured rather than guessed (Cloud Run logs this too).
    print(f"Trip tap {action}: auth {_ms(started, authed)}, db {_ms(authed, db_done)}, "
          f"answer {_ms(db_done, answered)}, redraw {_ms(answered, redrawn)}, "
          f"banner {_ms(redrawn, time.perf_counter())}")


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
    the primary guard: the real duplicate-write protection is the idempotency_key, per
    ADR-0011. Polling runs updates one at a time, but the webhook calls process_update once per
    request, so two taps can overlap in production - which is why the trip toggle re-checks
    that its entry is still pending before changing it.
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


# --- Duplicate warning (#58) -------------------------------------------------------------
#
# An entry with the same amount and currency as one saved in the last few minutes - by anyone
# in the household - gets a warning card instead of a normal one. The check runs before the
# card is shown rather than inside the insert, because by the time add_expense runs the user
# has already tapped Confirm and can no longer decide. See ADR-0027.
DUPLICATE_WINDOW_MINUTES = 5


def _find_duplicate_of(transaction: dict):
    """Returns the recently saved entry this one looks like a repeat of, or None.

    Never raises. An amount that can't be converted to cents (the save would reject it anyway)
    and a failed query both come back as None, meaning "show a normal card" - a broken check
    must never stop an entry being saved.
    """
    try:
        amount_cents = dollars_to_cents(transaction.get('amount'))
    except (TypeError, ValueError):
        return None
    # Same currency default as the add_expense call in handle_button_click, so the check looks
    # for exactly what would be saved.
    return find_recent_duplicate(amount_cents, transaction.get('currency', 'SGD'), DUPLICATE_WINDOW_MINUTES)


def _display_amount(transaction: dict) -> str:
    """The amount as a card shows it, e.g. 'SGD 1.01'.

    Worked out from the same cents value add_expense will save, so a card can never show one
    amount and save another. Formatting the float directly used to show 1.005 as '1.00' while the
    save rounded it to 101 cents (#39). An amount the save would reject anyway (zero, negative)
    falls back to the raw value, so the card still appears and Confirm fails as it always has.
    """
    currency = transaction.get('currency')
    try:
        return f"{currency} {format_cents(dollars_to_cents(transaction.get('amount')))}"
    except ValueError:
        return f"{currency} {float(transaction.get('amount')):.2f}"


def _account_lines(transaction: dict) -> str:
    """The card's Account and Paid by lines, shared by every card for one entry (#92).

    The account is the field most likely to be wrong - it is inferred (a named card, the wallet
    for foreign currency, or the sender's default) - so the message left in the chat after a save
    must still say which card the entry went against and whose it was. One helper keeps the
    confirm, saved and error cards from drifting apart. Both values come from the pending entry,
    so no database read is needed.
    """
    return (
        f"💳 **Account:** {transaction.get('payment_method', 'Unspecified')}\n"
        f"👤 **Paid by:** {_md(transaction.get('paid_by') or transaction.get('account_owner') or 'Unspecified')}\n"
    )


def _live_account_lines(transaction: dict) -> str:
    """Show the payer warning below Paid by on live cards, never the saved card.

    This display-only warning never changes buttons or becomes a stored column.
    Escape it because payment rules can include participant and account names.
    """
    lines = _account_lines(transaction)
    if transaction.get('payer_warning'):
        lines += f"{_md(transaction['payer_warning'])}\n"
    return lines


def _date_lines(transaction: dict) -> str:
    """The card's date line, plus a ⚠️ line straight under it if the date looks wrong (#74).

    'date_warning' is set by apply_date_resolution: None for a normal date, otherwise the
    text to show (in the future, more than 60 days back, or couldn't be worked out). It is
    a line of text and nothing more - the buttons and the save path are untouched, so a
    flagged date can always still be saved (ADR-0030). .get() because an entry built any
    other way (older tests, a card stashed before this shipped) has no such key.
    """
    lines = f"📅 **Date:** {transaction.get('date')}\n"
    if transaction.get('date_warning'):
        lines += f"⚠️ {transaction['date_warning']}\n"
    return lines


def _format_age(seconds: int) -> str:
    """How long ago the earlier entry was saved: 'just now', '40 seconds ago', '2 minutes ago'."""
    seconds = max(0, int(seconds))  # clamped so a rounding quirk can never read "-1 seconds ago"
    if seconds == 0:
        return "just now"
    if seconds < 60:
        return "1 second ago" if seconds == 1 else f"{seconds} seconds ago"
    minutes = seconds // 60
    return "1 minute ago" if minutes == 1 else f"{minutes} minutes ago"


def _duplicate_banner(duplicate: dict, amount_text: str, sender_id: int, directory: Directory) -> str:
    """The warning that goes above a flagged card's summary.

    Only the participant id is stored (ADR-0033), so the name is looked up in the directory here,
    at display time. It says "You" when the same person logged the earlier entry, and names
    nobody when the earlier row has no entered_by_participant_id (written before #60, or by the
    CLI/API) or that participant no longer exists - an id itself is never shown.
    """
    entered_by = duplicate.get('entered_by_participant_id')
    earlier_sender = directory.participant(entered_by)
    age = _format_age(duplicate.get('seconds_ago', 0))

    if entered_by is not None and entered_by == sender_id:
        who = f"You logged {amount_text} {age}."
    elif earlier_sender is not None:
        who = f"{earlier_sender.display_name} logged {amount_text} {age}."
    else:
        who = f"{amount_text} was logged {age}."

    return (
        f"⚠️ **Possible duplicate.** {who}\n"
        f"📝 **Earlier entry:** {duplicate.get('description')}\n\n"
    )


def _duplicate_keyboard() -> InlineKeyboardMarkup:
    """A flagged card's buttons. They send the same callback_data as the normal pair, so
    "Save anyway" goes through exactly the same confirm path - progress label, double-tap
    guard, idempotency key - and only the labels differ."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⚠️ Save anyway", callback_data="confirm_save"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_save"),
    ]])


def _is_flagged(transaction: dict) -> bool:
    """True when the card needs a second look before saving: a possible duplicate (#58), or a
    card that is already closed (#53) - a late refund on a cancelled card is real, but the user
    should say so. .get() because a card stashed before #53 has no closed_account key."""
    return bool(transaction.get('suspected_duplicate') or transaction.get('closed_account'))


def _keyboard_for(transaction: dict) -> InlineKeyboardMarkup:
    """The buttons a pending card carries: the warning pair if it was flagged, else Confirm/Cancel,
    and under them the trip toggle when a trip was on as the entry was logged."""
    main = _duplicate_keyboard() if _is_flagged(transaction) else _confirm_keyboard()
    offer = transaction.get('trip_offer')
    if not offer:
        return main
    # One button that flips between the two states, so a mis-tap on "Not trip" can be undone.
    # The callback_data is fixed and short: Telegram allows only 64 bytes, and the tap finds the
    # entry by the card's message_id anyway.
    label = "🏠 Not trip" if transaction.get('trip_id') else f"🧳 Tag to {offer['name']}"
    return InlineKeyboardMarkup(
        [*main.inline_keyboard, [InlineKeyboardButton(label, callback_data="trip_toggle")]]
    )


def _trip_lines(transaction: dict) -> str:
    """The card's Trip line. Nothing when no trip was on, so everyday cards look as they always
    have. .get() because a card stashed before trips shipped has none of these keys."""
    if transaction.get('trip_warning'):
        return f"{transaction['trip_warning']}\n"
    offer = transaction.get('trip_offer')
    if not offer:
        return ""
    if transaction.get('trip_id'):
        return f"🧳 **Trip:** {_md(offer['name'])}\n"
    return "🧳 **Trip:** not a trip entry\n"


def _pending_card_text(transaction: dict) -> str:
    """The text of a card waiting for Confirm. Built from the pending entry alone, so the trip
    toggle can redraw the card without the directory or the model: the banner (duplicate and
    closed-card warnings) is worked out once, when the entry is logged, and kept on the entry."""
    return transaction.get('card_banner', "") + (
        f"Please confirm your **{transaction.get('transaction_type', 'Expense')}**:\n\n"
        f"💰 **Amount:** {_display_amount(transaction)}\n"
        f"🏷️ **Category:** {transaction.get('category')}\n"
        f"{_live_account_lines(transaction)}"
        f"{_trip_lines(transaction)}"
        f"📝 **Notes:** {transaction.get('description', 'None')}\n"
        f"{_date_lines(transaction)}"
    )


# shared pipeline: raw text (transcript or typed) -> extraction -> pending confirmation card
async def process_expense_text(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str, status_msg,
                               directory: Directory):
    """Extracts structured data from raw text and presents it for confirmation.

    'raw_text' is a Whisper transcript when called from handle_voice and the message body
    when called from handle_text - past this point the two are indistinguishable, which is
    the whole reason this lives outside either handler.

    'status_msg' is passed in rather than created here because each transport shows a
    different message while it works (voice echoes the transcript back, text has nothing to
    echo). Every branch below edits that same message instead of sending a new one, so the
    user is always left with exactly one message per entry attempt.

    'directory' is the participants and accounts authorize() just read - the same snapshot is
    used for the prompt's account list, the payment rules and the card, so they can't disagree.
    """
    # One "today" for this whole entry, in Singapore time: the prompt reads the message
    # against it and the date resolver counts back from it, so they can't disagree (#15).
    today = get_sgt_now().date()

    # Extract structured data from the raw text
    structured_data = await extract_transactions(raw_text, today, directory.account_names())

    # Check if data exists AND if the 'transactions' list is present
    if not structured_data or not structured_data.get("transactions"):
        await status_msg.edit_text("❌ Failed to extract structured data from your message.")
        return

    # Extract the actual list from the wrapper and the spender
    transactions_list = structured_data["transactions"]
    user_id = str(update.effective_user.id)
    # authorize() already checked this sender is a participant, so this is never None.
    sender = directory.participant_by_telegram_id(user_id)

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

    # Turn what the user said about the date ("yday", "last tues", "3/7") into the date itself,
    # counting back from the same 'today' the prompt was given (#15, ADR-0030).
    single_transaction = apply_date_resolution(single_transaction, today)

    # Which trip, if any, this entry is tagged to: the one on right now, whatever the entry's date,
    # so a booking paid before the trip still counts. Copied onto the entry here, so a /trip
    # switch while this card waits doesn't change it, and the "Not trip" tap never reads the
    # database. trip_offer remembers the trip even after "Not trip", so the tap can be undone.
    try:
        active_trip = _load_active_trip(sender.id)
    except Exception as e:
        print(f"❌ Could not read the active trip: {e}")
        active_trip = None
        single_transaction['trip_warning'] = TRIP_UNKNOWN_WARNING
    if active_trip:
        single_transaction['trip_offer'] = {'id': active_trip.id, 'name': active_trip.name}
    single_transaction['trip_id'] = active_trip.id if active_trip else None

    # Trip candidates are exact membership; a failed read falls back to household only.
    candidates = (tuple(p for p in directory.participants if p.id in active_trip.member_ids)
                  if active_trip else directory.household())
    reading = read_payer(raw_text, candidates, sender, directory.account_names())

    # Which account paid, and whose it is (app/services/participants.py has the rules). A top-up
    # with no wallet set up is refused outright rather than guessed at: nothing is stashed, so
    # there is no card to confirm.
    try:
        single_transaction = resolve_payment(single_transaction, directory, sender, text_payer=reading.payer)
    except WalletNotSetUp:
        await status_msg.edit_text(NO_WALLET_MESSAGE)
        return
    except OutsideAccountNotSetUp:
        await status_msg.edit_text(NO_OUTSIDE_ACCOUNT_MESSAGE)
        return
    if reading.unclear:
        single_transaction['payer_warning'] = PAYER_UNCLEAR_WARNING

    # One idempotency key per confirm prompt (not per save attempt) - reused on every
    # save attempt for this same prompt, so a double-tap or webhook retry can't insert twice.
    single_transaction['idempotency_key'] = str(uuid.uuid4())

    # The participant id is the only record of the sender, so names can change without
    # rewriting entries. account_owner is only whose card paid, not who sent the message.
    single_transaction['entered_by_participant_id'] = sender.id

    # Does this look like a repeat of something saved moments ago, by anyone? (#58) Kept on the
    # pending entry itself because a button tap carries only the card's message_id - this is how
    # handle_button_click later tells a flagged card from a normal one on Cancel or a failed save.
    single_transaction['suspected_duplicate'] = _find_duplicate_of(single_transaction)

    amount_text = _display_amount(single_transaction)
    banner = ""
    if single_transaction['suspected_duplicate']:
        banner = _duplicate_banner(single_transaction['suspected_duplicate'], amount_text, sender.id, directory)
    if single_transaction.get('closed_account'):
        # Under the duplicate banner when both apply - one card, every reason to look twice.
        banner += f"{CLOSED_CARD_WARNING}\n\n"
    # Kept on the entry so the trip toggle can redraw the card exactly as it was (see
    # _pending_card_text). The duplicate banner names people, which needs the directory.
    single_transaction['card_banner'] = banner

    # Send the clean summary with its buttons attached - Confirm/Cancel, or Save anyway/Cancel if
    # flagged, plus the trip toggle when a trip is on
    await status_msg.edit_text(_pending_card_text(single_transaction), reply_markup=_keyboard_for(single_transaction),
                               parse_mode="Markdown")

    # Stash the transaction against *this card's* message_id, only once the card exists - the
    # button press that comes back carries the message_id and nothing else, so that is the
    # only handle handle_button_click has for finding the right transaction again.
    _remember_pending_card(context, status_msg.message_id, single_transaction)

# get voice message, transcribe, then hand the transcript to the shared pipeline above
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Downloads the voice note, transcribes it, and passes the transcript on for extraction."""
    directory = await authorize(update)
    if directory is None:
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
        await process_expense_text(update, context, transcript_text, status_msg, directory)

    finally:
        # Clean up manually since we used delete=False
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)

# typed expense entry - no transcription step, the message body IS the input
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Passes a plain text message through the same pipeline a voice note uses."""
    directory = await authorize(update)
    if directory is None:
        return

    status_msg = await update.message.reply_text("🧠 Extracting data...")
    await process_expense_text(update, context, update.message.text, status_msg, directory)

# anything that is neither a voice note nor text - these used to be dropped silently
async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tells the user which input types the bot actually accepts, instead of ignoring them."""
    if await authorize(update) is None:
        return

    await update.message.reply_text(
        "⚠️ I can only read voice notes and text messages right now.\n"
        "Receipt photos are planned but not supported yet."
    )

async def delete_pin_notice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deletes the "Bot pinned a message" notice Telegram adds when the trip banner is pinned.

    The banner itself is the reminder; the notice under it only repeats it. Only notices for the
    bot's own pins are touched - a pin the user made is theirs to keep. Done the moment the notice
    arrives, well inside Telegram's 48-hour limit on deleting. No authorize(): the bot is only
    tidying up its own action, and nothing is read or written. A failure just leaves the notice.
    """
    message = update.effective_message
    if message is None or message.from_user is None or message.from_user.id != context.bot.id:
        return
    try:
        await message.delete()
    except Exception as e:
        print(f"Could not delete pin notice: {e}")


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
            source="Telegram Bot",
            idempotency_key=transaction_to_save.get('idempotency_key'),
            # The ids the card was built from (#53). Never re-read from the directory here: what
            # the user confirmed is what gets saved.
            account_id=transaction_to_save.get('account_id'),
            entered_by_participant_id=transaction_to_save.get('entered_by_participant_id'),
            # The trip as the card showed it, after any "Not trip" tap. None for an everyday entry.
            trip_id=transaction_to_save.get('trip_id'),
            paid_by_participant_id=transaction_to_save.get('paid_by_participant_id'),
        )

        if save_success:
            _mark_card_resolved(context, message_id, "saved")
            # Reconstruct the full summary message to keep it in the chat history
            updated_summary = (
                f"✅ **Saved to Ledger!**\n\n"
                f"💰 **Amount:** {_display_amount(transaction_to_save)}\n"
                f"🏷️ **Category:** {transaction_to_save.get('category')}\n"
                # The account stays on record in the chat after the buttons go (#92)
                f"{_account_lines(transaction_to_save)}"
                f"{_trip_lines(transaction_to_save)}"
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
            # The retry hint names whichever button the card is about to get back
            retry_label = "⚠️ Save anyway" if _is_flagged(transaction_to_save) else "✅ Confirm"
            # CHANGED: Keep the summary visible even if the database fails, just change the header
            error_summary = (
                f"❌ **Database Error! Could not save:**\n\n"
                f"💰 **Amount:** {_display_amount(transaction_to_save)}\n"
                f"🏷️ **Category:** {transaction_to_save.get('category')}\n"
                # The card is live again, so it shows the account a retry would save against
                f"{_live_account_lines(transaction_to_save)}"
                f"{_trip_lines(transaction_to_save)}"
                f"📝 **Notes:** {transaction_to_save.get('description', 'None')}\n"
                # Same date lines as the first card, ⚠️ included: this card is live again,
                # so it should still say what was odd about the date being retried.
                f"{_date_lines(transaction_to_save)}\n"
                f"Tap **{retry_label}** to try again."
            )
            # _keyboard_for, not _confirm_keyboard: a flagged card gets its warning buttons back
            await query.edit_message_text(error_summary, parse_mode="Markdown", reply_markup=_keyboard_for(transaction_to_save))

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
        cancelled = _take_pending_card(context, message_id)
        _mark_card_resolved(context, message_id, "cancelled")
        # A flagged card says which kind of cancel this was (#58). cancelled can be None (a card
        # from before a restart), which just gets the plain wording.
        if cancelled and cancelled.get('suspected_duplicate'):
            cancel_text = "❌ **Duplicate transaction cancelled.** Nothing was saved to the Ledger."
        else:
            cancel_text = "❌ **Cancelled.** Nothing was saved to the Ledger."
        await query.edit_message_text(cancel_text, parse_mode="Markdown")

    elif query.data == "trip_toggle":
        # Flips the pending entry between "tagged to the trip" and "not a trip entry", then redraws
        # the card. Only the in-memory entry changes: nothing is saved, and the database isn't read.
        outcome = _card_outcome(context, message_id)
        if outcome == "saved":
            await query.answer("✅ Already saved to the ledger.")
            return
        if outcome == "cancelled":
            await query.answer("❌ This entry was cancelled.")
            return

        # Looked up, not taken: the entry stays pending for Confirm. Missing while a save is in
        # flight (Confirm takes it out), after a restart, or on a card from before trips.
        pending = context.user_data.get('pending_cards', {}).get(message_id)
        if pending is None or not pending.get('trip_offer'):
            await query.answer("⚠️ This entry can't be changed now.")
            return

        offer = pending['trip_offer']
        new_trip_id = None if pending.get('trip_id') else offer['id']
        # Redraw from a copy first, and change the pending entry only once the card shows the new
        # state. If the redraw fails, the card still shows the old trip line, so the entry must
        # keep it too: Confirm saves exactly what the card showed.
        toggled = {**pending, 'trip_id': new_trip_id}
        try:
            await query.edit_message_text(_pending_card_text(toggled), reply_markup=_keyboard_for(toggled),
                                          parse_mode="Markdown")
        except Exception as e:
            print(f"❌ Trip toggle redraw failed: {e}")
            await query.answer("⚠️ Couldn't change the trip, try again.")
            return
        # The webhook runs updates concurrently, so a Confirm or Cancel tap can have taken this
        # entry while the redraw above was waiting. Then the entry is no longer this card's to
        # change: leave it alone (Confirm saves, and redraws the card with, what it took).
        if context.user_data.get('pending_cards', {}).get(message_id) is not pending:
            await query.answer("⏳ This entry is already being saved or was cancelled.")
            return
        pending['trip_id'] = new_trip_id
        # Answered last, so the single answer() can say what actually happened. Plain text, so
        # the name needs no escaping here.
        await query.answer("🏠 Not a trip entry." if new_trip_id is None else f"🧳 Tagged to {offer['name']}.")

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
    app.add_handler(CommandHandler("trip", trip_command))

    # voice handler
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    # text handler - ~filters.COMMAND keeps /recent, /today etc. on their CommandHandlers above
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # everything else (photo, video, sticker, document, location) - these matched no handler
    # at all before, so PTB dropped them silently and the user got no reply whatsoever
    # Service messages (StatusUpdate) are left out: pinning the trip banner makes Telegram send a
    # "pinned a message" update whose sender is the bot itself, and authorize() would answer it
    # with "You are not authorized" in the user's chat.
    app.add_handler(MessageHandler(~filters.VOICE & ~filters.TEXT & ~filters.StatusUpdate.ALL, handle_unsupported))

    # the "Bot pinned a message" notice under the trip banner - deleted, see delete_pin_notice
    app.add_handler(MessageHandler(filters.StatusUpdate.PINNED_MESSAGE, delete_pin_notice))

    # handler for buttons
    app.add_handler(CallbackQueryHandler(handle_trip_button, pattern=r"^trip:"))
    app.add_handler(CallbackQueryHandler(handle_button_click))

    return app
