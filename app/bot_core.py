import truststore
import os
import uuid
from dataclasses import replace
from dotenv import load_dotenv
from telegram import BotCommand, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.helpers import escape_markdown
from telegram.error import BadRequest
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import asyncio
import tempfile
import time
from app.add_expense import add_expense, dollars_to_cents, format_cents
from app.services.utils import get_sgt_now, get_week_start, get_month_start
from app.services.ledger_queries import (
    get_recent_entries, get_period_summary, get_category_summary, find_recent_duplicate,
    get_trip_split_entries,
)
from app.services.dates import apply_date_resolution
from app.database import get_connection
from app.services.participants import (
    CLOSED_CARD_WARNING, NO_WALLET_MESSAGE, Directory, WalletNotSetUp, load_directory, resolve_payment,
    NO_OUTSIDE_ACCOUNT_MESSAGE, PAYER_UNCLEAR_WARNING, OutsideAccountNotSetUp, read_payer,
    NO_OUTSIDE_ACCOUNT_TOAST, NO_PAYER_ACCOUNT_TOAST, PayerHasNoAccount, change_payer,
    PAYER_READ_AS_WARNING, second_reader_payer,
)
from app.services.splits import (
    resolve_split, menu_people, split_groups, split_people, split_rows,
    open_split, toggle_split_person, toggle_split_family, remove_split,
)
from app.services.trips import (
    Trip, load_active_trip, trip_families, switch_trip_off, switch_trip_on, member_trips,
    member_trip, banner_message_id, set_banner_message_id, format_trip_dates,
)
from app.services.balances import trip_balance_lines
from app.services.help_topics import INTRO as HELP_INTRO, TOPICS as HELP_TOPICS

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
        if trip is None:
            return None
        families = trip_families(conn, trip.id)
        return replace(trip, member_ids=tuple(pid for pid, _ in families), families=families)


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


HELP_UNKNOWN_TOAST = "That help page is gone. Send /help again."


def _help_screen(topic: str | None):
    """The /help message: the intro with one button per topic, or one topic with a Back button.
    Plain text, sent with no parse_mode, so the examples' apostrophes and quotes need no escaping."""
    if topic is None:
        rows = [[InlineKeyboardButton(label, callback_data=f"help:{key}")]
                for key, (label, _) in HELP_TOPICS.items()]
        return HELP_INTRO, InlineKeyboardMarkup(rows)
    return HELP_TOPICS[topic][1], InlineKeyboardMarkup([[InlineKeyboardButton("◀ Back", callback_data="help:menu")]])


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help: how to word an entry, as topic buttons (taps, never typed arguments)."""
    if await authorize(update) is None:
        return
    text, keyboard = _help_screen(None)
    await update.message.reply_text(text, reply_markup=keyboard)


async def handle_help_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A tap under /help: "help:<topic>" or "help:menu". Only swaps the text; reads nothing and
    changes nothing. Answered once, before the redraw, as every button handler here is."""
    query = update.callback_query
    if await authorize(update) is None:
        await query.answer()
        return
    key = query.data.removeprefix("help:")
    if key != "menu" and key not in HELP_TOPICS:
        await query.answer(HELP_UNKNOWN_TOAST)
        return
    text, keyboard = _help_screen(None if key == "menu" else key)
    try:
        await query.answer()
    except Exception as e:
        print(f"Could not answer help tap: {e}")
    try:
        await query.edit_message_text(text, reply_markup=keyboard)
    except Exception as e:
        # Nothing was changed, so a failed redraw (or "message is not modified") loses nothing.
        print(f"Could not redraw help: {e}")

def format_period_summary(title: str, expenses: list, transfers: list) -> str:
    """Helper function to build a mobile-readable summary for any date range."""
    lines = [f"{title}\n"]
    
    # 🛒 Calculate and format expenses
    tx_count = sum(count for _, count, _ in expenses) if expenses else 0
    lines.append(f"🛒 **Expenses:** {tx_count} transactions")
    
    if expenses:
        for curr, count, total_cents in expenses:
            # SUM over a BIGINT column comes back from Postgres as a Decimal, and money is
            # only ever formatted through format_cents (ADR-0004), never via a float.
            lines.append(f"  • {curr}: **{format_cents(int(total_cents))}**")
    else:
        lines.append("  • No expenses.")
        
    # 🔄 Format transfers separately
    lines.append("\n🔄 **Transfers:**")
    if transfers:
        for curr, count, total_cents in transfers:
            lines.append(f"  • {curr}: **{format_cents(int(total_cents))}** ({count} tx)")
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
            lines.append(f"  • {curr}: **{format_cents(int(total_cents))}**")
            
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
    BotCommand("help", "How to word an entry"),
    BotCommand("recent", "Recent entries"),
    BotCommand("today", "Today's summary"),
    BotCommand("week", "This week's summary"),
    BotCommand("month", "This month's summary"),
    BotCommand("cat_today", "Today's categories"),
    BotCommand("cat_week", "This week's categories"),
    BotCommand("cat_month", "This month's categories"),
    BotCommand("trip", "Change your trip mode"),
    BotCommand("trip_bals", "Who owes whom on a trip"),
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


# --- /trip_bals: who owes whom on one trip --------------------------------------------------
#
# Read-only: it changes nothing in the database and holds no pending entry, so it needs no
# card lock. The maths is in app/services/balances.py; this part only picks the trip, reads
# the entries and shows the lines.

BALS_ERROR = "❌ Couldn't work out the balances, try again."
BALS_NOT_MEMBER = "You're not on that trip."
# Telegram refuses a message longer than this, and a refused reply would look like a dead
# command. So a long report is shortened here rather than sent and lost.
TELEGRAM_MESSAGE_LIMIT = 4096
BALS_FAMILIES_ONLY = "(Too long to list who is owed under each family, so totals and payments only.)"
BALS_CUT_SHORT = "… (Too long to show in full.)"


def _load_bals_trip(participant_id: int, trip_id: int | None) -> Trip | None:
    """The trip to show balances for, with its members' families, or None.

    trip_id None means "the trip this person has switched on". A trip id (from a button) is
    loaded only if this person is a member of it. Its own function, like _load_directory, so
    tests can swap it out without a database.
    """
    with get_connection() as conn:
        if trip_id is None:
            trip = load_active_trip(conn, participant_id)
        else:
            trip = member_trip(conn, participant_id, trip_id)
        if trip is None:
            return None
        families = trip_families(conn, trip.id)
        return replace(trip, member_ids=tuple(pid for pid, _ in families), families=families)


def _load_bals_choices(participant_id: int, offset: int):
    """One page of this person's trips for the picker, newest first: (trips, more to come)."""
    with get_connection() as conn:
        return member_trips(conn, participant_id, offset, TRIP_PAGE_SIZE)


def _bals_picker(choices, offset: int, more: bool, today):
    """The "which trip?" message: one button per trip, paged like /trip's menu. Callback data
    holds only a trip id or a page offset, never a name."""
    if not choices and offset == 0:
        return "You're not on any trips yet.", None
    rows = [[InlineKeyboardButton(f"{trip.name} · {format_trip_dates(trip, today)}",
                                  callback_data=f"bals:show:{trip.id}")] for trip in choices]
    paging = []
    if offset > 0:
        paging.append(InlineKeyboardButton("◂ Newer", callback_data=f"bals:page:{max(0, offset - TRIP_PAGE_SIZE)}"))
    if more:
        paging.append(InlineKeyboardButton("Older ▸", callback_data=f"bals:page:{offset + TRIP_PAGE_SIZE}"))
    if paging:
        rows.append(paging)
    return "Which trip's balances?", InlineKeyboardMarkup(rows)


def _bals_screen(trip: Trip, directory: Directory, today):
    """The balances message for one trip, and the button under it.

    Sent as plain text (no parse_mode), so a name with _ or * can't break it and it can be
    forwarded as it is. get_trip_split_entries catches its own errors and returns None for a
    failed read, which is shown as an error and never as "nobody owes anything".
    """
    header = f"🧳 {trip.name} · {format_trip_dates(trip, today)}"
    entries = get_trip_split_entries(trip.id)
    if entries is None:
        body = [BALS_ERROR]
    else:
        names = {person.id: person.display_name for person in directory.participants}
        body = trip_balance_lines(entries, names, dict(trip.families))
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Other trip", callback_data="bals:page:0")]])
    text = "\n".join([header, "", *body])
    if len(text) > TELEGRAM_MESSAGE_LIMIT:
        # First try the shorter form: every family and every payment, without the people
        # under each family. Nothing that decides who pays whom is lost.
        body = trip_balance_lines(entries, names, dict(trip.families), show_members=False)
        text = "\n".join([header, BALS_FAMILIES_ONLY, "", *body])
    if len(text) > TELEGRAM_MESSAGE_LIMIT:
        # Still too long: keep whole lines from the top and say plainly that it was cut.
        kept = text[:TELEGRAM_MESSAGE_LIMIT - len(BALS_CUT_SHORT) - 1].rsplit("\n", 1)[0]
        text = kept + "\n" + BALS_CUT_SHORT
    return text, keyboard


async def trip_bals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/trip_bals: the balances of the trip that is switched on, else a list of trips to tap.

    Nothing is typed after the command; anything typed there is ignored.
    """
    directory = await authorize(update)
    if directory is None:
        return
    sender = directory.participant_by_telegram_id(str(update.effective_user.id))
    today = get_sgt_now().date()
    try:
        trip = _load_bals_trip(sender.id, None)
        if trip is None:
            choices, more = _load_bals_choices(sender.id, 0)
    except Exception as e:
        print(f"Could not load trip balances: {e}")
        await update.effective_message.reply_text(BALS_ERROR)
        return
    text, keyboard = _bals_screen(trip, directory, today) if trip else _bals_picker(choices, 0, more, today)
    await update.effective_message.reply_text(text, reply_markup=keyboard)


async def handle_bals_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A tap under /trip_bals: "bals:show:<trip id>" or "bals:page:<offset>".

    A callback query can be answered exactly once, so everything is read and the reply is
    built first; then one answer(), then the redraw.
    """
    query = update.callback_query
    directory = await authorize(update)
    if directory is None:
        await query.answer()
        return
    sender = directory.participant_by_telegram_id(str(update.effective_user.id))
    today = get_sgt_now().date()
    toast = None
    try:
        parts = query.data.split(":")
        action, value = parts[1], int(parts[2])
        if len(parts) != 3 or action not in ("show", "page") or not 0 <= value <= 2**63 - 1:
            raise ValueError("Invalid balances callback")
        trip = _load_bals_trip(sender.id, value) if action == "show" else None
        if trip is not None:
            text, keyboard = _bals_screen(trip, directory, today)
        else:
            # A page of the picker; also where a tap on a trip this person isn't on lands.
            offset = value if action == "page" else 0
            if action == "show":
                toast = BALS_NOT_MEMBER
            choices, more = _load_bals_choices(sender.id, offset)
            text, keyboard = _bals_picker(choices, offset, more, today)
    except Exception as e:
        print(f"Could not load trip balances: {e}")
        await query.answer(BALS_ERROR)
        return
    try:
        await query.answer(toast)
    except Exception as e:
        print(f"Could not answer balances tap: {e}")
    try:
        await query.edit_message_text(text, reply_markup=keyboard)
    except Exception as e:
        # Nothing was changed, so a failed redraw (or "message is not modified") loses nothing.
        print(f"Could not redraw balances: {e}")


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


def _card_lock(context: ContextTypes.DEFAULT_TYPE, message_id: int) -> asyncio.Lock:
    """The lock for one card. Every tap that reads or changes a card's pending entry runs inside
    it: Confirm, Cancel, the trip toggle and the "Paid by" menu.

    Why it is needed: the webhook handles updates concurrently, and every `await` on a Telegram
    call is a point where another tap on the same card can run. Without the lock, a Confirm
    landing while a payer change is still redrawing the card would save the old payer under a
    card that shows the new one. With it, taps on one card run one at a time, in arrival order,
    so what is saved is always what the card showed. Taps on different cards never wait for
    each other.

    One lock per card, kept in user_data like the pending entries and bounded the same way. It
    only works inside one process - which is also where the pending entries live (ADR-0023).
    """
    locks = context.user_data.setdefault('card_locks', {})
    if message_id not in locks:
        _bounded_put(locks, message_id, asyncio.Lock())
    return locks[message_id]


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


def _owed_split(transaction: dict) -> tuple[dict, ...]:
    """The split rows that mean something: what the card shows and Confirm saves.

    A split whose only sharer is the payer is no split at all - they paid for themselves - so
    it is dropped, and the entry is the payer's alone ("no rows means not split"). The pending
    entry keeps the tick, so a later "Change who paid" tap brings the split back as a debt.
    """
    rows = tuple(sorted(transaction.get('split') or (), key=lambda row: row['participant_id']))
    if len(rows) == 1 and rows[0]['participant_id'] == transaction.get('paid_by_participant_id'):
        return ()
    return rows


def _split_lines(transaction: dict) -> str:
    """Show every share, grouping family labels in lowest-member-id order."""
    rows = _owed_split(transaction)
    if not rows:
        return ""
    if len(rows) == 1:
        # "Split: only Carol" read as Carol splitting with herself. One sharer who isn't the
        # payer owes the payer the whole amount, so say exactly that. A bot entry always has
        # a payer (resolve_payment sets one), so the name is always there.
        row = rows[0]
        payer = transaction.get('paid_by') or transaction.get('account_owner')
        return f"👥 **Split:** {_md(row['name'])} owes {_md(payer)} {format_cents(row['share_cents'])}\n"
    # Keep the unit on the pending snapshot so the saved receipt uses the same wording.
    by_count = 'count' in rows[0]
    unit = (transaction.get('split_menu') or {}).get('unit') or 'count'
    heading = f"Split by {_md(unit)}" if by_count else "Split"
    lines = [f"👥 **{heading} between {len(rows)}:**"]
    groups = {}
    for row in rows:
        groups.setdefault(row.get('family') or None, []).append(row)
    # Unlabelled people come last; insertion order already sorts families by their first id.
    labels = [label for label in groups if label is not None]
    if None in groups:
        labels.append(None)
    for label in labels:
        if label:
            lines.append(_md(label))
        for row in groups[label]:
            count = f"{row['count']}, " if by_count else ""
            lines.append(f" • {_md(row['name'])}: {count}{format_cents(row['share_cents'])}")
    return "\n".join(lines) + "\n"


def _live_split_lines(transaction: dict) -> str:
    """Keep reading warnings on live cards only, never on the saved receipt."""
    return _split_lines(transaction) + "".join(f"{_md(warning)}\n" for warning in transaction.get('split_warnings') or ())


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
    rows = [*main.inline_keyboard]
    offer = transaction.get('trip_offer')
    if offer:
        # One button that flips between the two states, so a mis-tap on "Not trip" can be undone.
        # The callback_data is fixed and short: Telegram allows only 64 bytes, and the tap finds
        # the entry by the card's message_id anyway.
        label = "🏠 Not trip" if transaction.get('trip_id') else f"🧳 Tag to {offer['name']}"
        rows.append([InlineKeyboardButton(label, callback_data="trip_toggle")])
    if transaction.get('split_menu'):
        label = "👥 Change split" if transaction.get('split') else "👥 Add split"
        rows.append([InlineKeyboardButton(label, callback_data="split:menu")])
    if transaction.get('payer_menu'):
        # Opens the list of people (handle_payer_button). Absent on a YouTrip top-up, and on a
        # card stashed before this button shipped.
        rows.append([InlineKeyboardButton("👤 Change who paid", callback_data="payer:menu")])
    return InlineKeyboardMarkup(rows)


def _people_keyboard(people, pick_prefix: str, back_data: str, marked_ids=(), per_row: int = 2) -> InlineKeyboardMarkup:
    """A menu of people to tap, two to a row, with a Back button under them.

    Shared on purpose: "Paid by" and "Add split" use their own prefixes, and splits can mark
    more than one person. Each button sends `<pick_prefix>:<participant id>`,
    which stays far under Telegram's 64-byte limit however long a name is. People in
    `marked_ids` get a ✅. Labels are plain text, so names need no Markdown escaping.
    """
    buttons = [
        InlineKeyboardButton(("✅ " if p.id in marked_ids else "") + p.display_name,
                             callback_data=f"{pick_prefix}:{p.id}")
        for p in people
    ]
    rows = [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]
    rows.append([InlineKeyboardButton("◂ Back", callback_data=back_data)])
    return InlineKeyboardMarkup(rows)


def _payer_keyboard(transaction: dict) -> InlineKeyboardMarkup:
    """The "who paid?" menu for one pending entry, with the current payer marked."""
    return _people_keyboard(_payer_people(transaction), "payer:set", "payer:back",
                            marked_ids=(transaction.get('paid_by_participant_id'),))


def _payer_people(transaction: dict) -> tuple:
    """The people the "Change who paid" menu offers right now (ADR-0041).

    Decided from the card as it is now, not when the entry was logged: tagged to a trip, the
    trip's members; not tagged (no trip on, or after "Not trip"), every participant, household
    first. The payer shown now is always added, so the menu always has a ✅. _payer_tap checks a
    tapped id against this same list, so a button left over from the other state is refused.
    Both lists were copied onto the entry at log time, so this never reads the database.
    """
    menu = transaction['payer_menu']
    people = menu['trip_people'] if transaction.get('trip_id') else menu['everyone']
    current = menu['directory'].participant(transaction.get('paid_by_participant_id'))
    if current is not None and all(p.id != current.id for p in people):
        people = (*people, current)
    return people


def _split_keyboard(transaction: dict) -> InlineKeyboardMarkup:
    """The split menu for one pending entry (ADR-0041).

    Tagged to a trip: each family gets a toggle row (✅ when all its members are ticked), with its
    members two to a row under it; the unlabelled group comes last and has no toggle. Not tagged:
    every participant, two to a row, no families. Anyone ticked who isn't on that list is added at
    the end, so they can always be unticked. Then "Remove split" and "Done".

    Buttons carry only a participant id or a group index, never a name, to stay inside Telegram's
    64-byte limit; _split_tap checks each one against what this function would draw now.
    """
    marked = {row['participant_id'] for row in transaction.get('split') or ()}
    rows, shown = [], set()
    if transaction.get('trip_id'):
        for index, (label, members) in enumerate(transaction['split_menu']['groups']):
            if label:
                tick = "✅ " if all(p.id in marked for p in members) else ""
                rows.append([InlineKeyboardButton(tick + label, callback_data=f"split:f:{index}")])
            rows.extend(_people_keyboard(members, "split:p", "split:done", marked).inline_keyboard[:-1])
            shown.update(p.id for p in members)
    people = tuple(p for p in split_people(transaction) if p.id not in shown)
    rows.extend(_people_keyboard(people, "split:p", "split:done", marked).inline_keyboard[:-1])
    rows.append([InlineKeyboardButton("Remove split", callback_data="split:none"),
                 InlineKeyboardButton("Done", callback_data="split:done")])
    return InlineKeyboardMarkup(rows)


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
    toggle and the "Paid by" menu can redraw the card without the database or the model. The
    duplicate banner is worked out once, when the entry is logged, and kept on the entry. The
    closed-card warning is worked out here, from closed_account, because a "Paid by" tap can move
    the entry onto or off a closed card."""
    banner = transaction.get('card_banner', "")
    if transaction.get('closed_account'):
        # Under the duplicate banner when both apply - one card, every reason to look twice.
        banner += f"{CLOSED_CARD_WARNING}\n\n"
    return banner + (
        f"Please confirm your **{transaction.get('transaction_type', 'Expense')}**:\n\n"
        f"💰 **Amount:** {_display_amount(transaction)}\n"
        f"🏷️ **Category:** {transaction.get('category')}\n"
        f"{_live_account_lines(transaction)}"
        f"{_live_split_lines(transaction)}"
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
    second_payer = (second_reader_payer(single_transaction.get('payer_name'), candidates, sender, raw_text)
                    if reading.payer is None else None)

    # Which account paid, and whose it is (app/services/participants.py has the rules). A top-up
    # with no wallet set up is refused outright rather than guessed at: nothing is stashed, so
    # there is no card to confirm.
    try:
        single_transaction = resolve_payment(single_transaction, directory, sender, text_payer=reading.payer or second_payer)
    except WalletNotSetUp:
        await status_msg.edit_text(NO_WALLET_MESSAGE)
        return
    except OutsideAccountNotSetUp:
        await status_msg.edit_text(NO_OUTSIDE_ACCOUNT_MESSAGE)
        return
    if (second_payer and single_transaction.get('paid_by_participant_id') == second_payer.id
            and not single_transaction.get('payer_warning')):
        single_transaction['payer_warning'] = PAYER_READ_AS_WARNING.format(payer=second_payer.display_name)
    elif reading.unclear and second_payer is None:
        single_transaction['payer_warning'] = PAYER_UNCLEAR_WARNING

    everyone = menu_people(directory)
    trip_people = candidates if active_trip else ()
    single_transaction['split'] = ()
    single_transaction['split_warnings'] = ()
    if (single_transaction.get('category') != "YouTrip top-up"
            and single_transaction.get('transaction_type') != "Transfer"):
        split_candidates = candidates if active_trip else directory.participants
        families = dict(active_trip.families) if active_trip else {}
        result = resolve_split(single_transaction.get('split_reference'), split_candidates,
                               sender, families, active_trip is not None, message=raw_text)
        single_transaction['split_warnings'] = result.warnings
        try:
            amount_cents = dollars_to_cents(single_transaction['amount'])
        except ValueError:
            pass  # Saving rejects this amount too; no share should imply it was valid.
        else:
            single_transaction['split_menu'] = {
                'amount_cents': amount_cents, 'groups': split_groups(trip_people, families),
                'families': families, 'everyone': everyone,
                'counts': dict(result.counts), 'default_count': result.default_count, 'unit': result.unit,
            }
            single_transaction['split'] = split_rows(single_transaction, (p.id for p in result.people))

    # What the "Paid by" button needs, copied onto the entry so a tap never reads the database:
    # both people lists (chosen at draw time from the card's trip tag) and the snapshot, which
    # change_payer uses to find each person's account. Not on a top-up: its payer is the owner
    # of the wallet's funding card, and there is nothing to choose.
    if single_transaction.get('category') != "YouTrip top-up":
        single_transaction['payer_menu'] = {
            'trip_people': trip_people, 'everyone': everyone, 'directory': directory,
        }

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
    # Kept on the entry so a button tap can redraw the card exactly as it was (see
    # _pending_card_text, which adds the closed-card warning itself). The duplicate banner names
    # people, which needs the directory.
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


async def handle_payer_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The "Paid by" menu on a pending card: open it, pick a person, or go back.

    callback_data is "payer:menu", "payer:back" or "payer:set:<participant id>". Everything
    comes from the pending entry (process_expense_text put the people and the directory
    snapshot there), so no tap reads the database. Nothing is saved here: a pick changes only
    the in-memory entry, and Confirm then saves what the card shows.

    Like handle_button_click, each path answers the query exactly once, and last, so the single
    toast can say what really happened. It runs inside the card's lock (_card_lock), so a
    Confirm or Cancel tap can't slip in between the redraw and the change to the entry.
    """
    query = update.callback_query
    message_id = query.message.message_id
    async with _card_lock(context, message_id):
        await _payer_tap(query, context, message_id)


async def _payer_tap(query, context: ContextTypes.DEFAULT_TYPE, message_id: int):
    """handle_payer_button's work, run while holding the card's lock."""
    # A finished card gets a toast only, never an edit (#29).
    outcome = _card_outcome(context, message_id)
    if outcome == "saved":
        await query.answer("✅ Already saved to the ledger.")
        return
    if outcome == "cancelled":
        await query.answer("❌ This entry was cancelled.")
        return

    # Looked up, not taken: the entry stays pending for Confirm. Missing while a save is in
    # flight (Confirm takes it out) or after a restart.
    pending = context.user_data.get('pending_cards', {}).get(message_id)
    if pending is None or not pending.get('payer_menu'):
        await query.answer("⚠️ This entry can't be changed now.")
        return
    menu = pending['payer_menu']

    action, _, value = query.data.removeprefix("payer:").partition(":")

    if action in ("menu", "back"):
        # Only the buttons change: the people list replaces Confirm/Cancel, or they come back.
        # While the list is open there is no Confirm to tap, so a half-made choice can't be saved.
        keyboard = _payer_keyboard(pending) if action == "menu" else _keyboard_for(pending)
        try:
            await query.edit_message_reply_markup(reply_markup=keyboard)
        except Exception as e:
            print(f"❌ Paid-by menu redraw failed: {e}")
            await query.answer("⚠️ Couldn't update the buttons, try again.")
            return
        await query.answer()
        return

    # "payer:set:<id>". The id must be one of the people this card offered; anything else is a
    # stale or malformed button.
    person = next((p for p in _payer_people(pending) if action == "set" and str(p.id) == value), None)
    if person is None:
        await query.answer("⚠️ This button is no longer supported. Please send the entry again.")
        return

    if person.id == pending.get('paid_by_participant_id'):
        # Tapping the person already marked ✅ keeps the account as it is and closes the menu.
        # It is still a deliberate "yes, them", so a payer warning has done its job and goes.
        changed = {**pending, 'payer_warning': None}
    else:
        try:
            changed = change_payer(pending, menu['directory'], person)
        except PayerHasNoAccount:
            await query.answer(NO_PAYER_ACCOUNT_TOAST.format(payer=person.display_name), show_alert=True)
            return
        except OutsideAccountNotSetUp:
            await query.answer(NO_OUTSIDE_ACCOUNT_TOAST, show_alert=True)
            return

    # Redraw from the changed copy first, and change the pending entry only once the card shows
    # the new payer. If the redraw fails, the card still shows the old one, so the entry must
    # keep it too: Confirm saves exactly what the card showed. (Same order as the trip toggle.)
    try:
        await query.edit_message_text(_pending_card_text(changed), reply_markup=_keyboard_for(changed),
                                      parse_mode="Markdown")
    except Exception as e:
        print(f"❌ Paid-by redraw failed: {e}")
        await query.answer("⚠️ Couldn't change who paid, try again.")
        return
    # The webhook runs updates concurrently, so a Confirm or Cancel tap can have taken this entry
    # while the redraw above was waiting. Then it is no longer this card's to change.
    if context.user_data.get('pending_cards', {}).get(message_id) is not pending:
        await query.answer("⏳ This entry is already being saved or was cancelled.")
        return
    # In place, so the dict the cache holds is the one that changes.
    pending.update(changed)
    await query.answer(f"👤 Paid by {person.display_name}.")


async def handle_split_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The split menu on a pending card: open it, tick people or a family, remove, or close.

    callback_data is "split:menu", "split:p:<participant id>", "split:f:<group index>",
    "split:none" (Remove split) or "split:done". Everything comes from the pending entry
    (process_expense_text stored the lists and the amount in cents there), so no tap reads the
    database, and nothing is saved here: Confirm saves what the card shows.

    Same rules as handle_payer_button: it runs inside the card's lock, so a Confirm can't land
    between the redraw and the change to the entry; it answers the query exactly once, and last;
    and a finished card gets a toast only.
    """
    query = update.callback_query
    message_id = query.message.message_id
    async with _card_lock(context, message_id):
        await _split_tap(query, context, message_id)


async def _split_tap(query, context: ContextTypes.DEFAULT_TYPE, message_id: int):
    """handle_split_button's work, run while holding the card's lock.

    The state functions in app/services/splits.py return a changed copy. The card is redrawn
    from the copy first, and the pending entry changes only once that worked, so Confirm always
    saves what the card showed. A tap that changes nothing (Done, or reopening the same menu)
    makes Telegram say "message is not modified"; the card already shows the copy, so that
    counts as success.
    """
    outcome = _card_outcome(context, message_id)
    if outcome in ('saved', 'cancelled'):
        await query.answer("✅ Already saved to the ledger." if outcome == 'saved' else "❌ This entry was cancelled.")
        return
    pending = context.user_data.get('pending_cards', {}).get(message_id)
    if pending is None or not pending.get('split_menu'):
        await query.answer("⚠️ This entry can't be changed now.")
        return

    action, _, value = query.data.removeprefix('split:').partition(':')
    try:
        if query.data == 'split:menu':
            changed = open_split(pending)
        elif query.data == 'split:none':
            changed = remove_split(pending)
        elif query.data == 'split:done':
            changed = dict(pending)
        elif action == 'p' and value in {str(p.id) for p in split_people(pending)}:
            changed = toggle_split_person(pending, int(value))
        elif action == 'f' and value in {str(i) for i in range(len(pending['split_menu']['groups']))}:
            changed = toggle_split_family(pending, int(value))
        else:
            raise ValueError('Unknown split button')
    except ValueError:
        await query.answer("⚠️ This button is no longer supported. Please send the entry again.")
        return

    keyboard = _keyboard_for(changed) if action in ('done', 'none') else _split_keyboard(changed)
    try:
        await query.edit_message_text(_pending_card_text(changed), reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        # Telegram refuses identical redraws, including Done or opening an unchanged menu.
        # That still means the card already shows the copy we intend to keep.
        if not isinstance(e, BadRequest) or 'message is not modified' not in str(e).lower():
            print(f"❌ Split redraw failed: {e}")
            await query.answer("⚠️ Couldn't change the split, try again.")
            return
    if context.user_data.get('pending_cards', {}).get(message_id) is not pending:
        await query.answer("⏳ This entry is already being saved or was cancelled.")
        return
    pending.update(changed)
    await query.answer()


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

    # Everything else reads or changes the card's pending entry, so it runs one tap at a time
    # per card (see _card_lock). The progress toast above stays outside: it touches nothing.
    async with _card_lock(context, message_id):
        await _card_tap(query, context, message_id)


async def _card_tap(query, context: ContextTypes.DEFAULT_TYPE, message_id: int):
    """handle_button_click's work for Confirm, Cancel and the trip toggle, run while holding
    the card's lock."""
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
            # The same rows the card showed: a split of only the payer is dropped (_owed_split).
            splits=[(row['participant_id'], row['share_cents']) for row in _owed_split(transaction_to_save)],
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
                f"{_split_lines(transaction_to_save)}"
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
                f"{_live_split_lines(transaction_to_save)}"
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
    app.add_handler(CommandHandler("help", help_command))

    # queries handler
    app.add_handler(CommandHandler("recent", recent_command))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("week", week_command))
    app.add_handler(CommandHandler("month", month_command))
    app.add_handler(CommandHandler("cat_today", cat_today_command))
    app.add_handler(CommandHandler("cat_week", cat_week_command))
    app.add_handler(CommandHandler("cat_month", cat_month_command))
    app.add_handler(CommandHandler("trip", trip_command))
    app.add_handler(CommandHandler("trip_bals", trip_bals_command))

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
    app.add_handler(CallbackQueryHandler(handle_bals_button, pattern=r"^bals:"))
    app.add_handler(CallbackQueryHandler(handle_payer_button, pattern=r"^payer:"))
    app.add_handler(CallbackQueryHandler(handle_split_button, pattern=r"^split:"))
    app.add_handler(CallbackQueryHandler(handle_help_button, pattern=r"^help:"))
    app.add_handler(CallbackQueryHandler(handle_button_click))

    return app
