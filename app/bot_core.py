from contextlib import nullcontext
import truststore
import os
import uuid
import datetime
from dataclasses import replace
from decimal import Decimal
from dotenv import load_dotenv
from telegram import (
    BotCommand, Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply, ReplyKeyboardMarkup,
)
from telegram.helpers import escape_markdown
from telegram.error import BadRequest
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import asyncio
import contextlib
import tempfile
import time
import re
from app.add_expense import add_expense, dollars_to_cents, format_cents, format_money
from app.services.one_currency import one_currency, take_line_parts
from app.services.trip_rates import get_trip_rates, upsert_trip_rate, rate_equation, rate_display, parse_rate
from app.services.trip_sheet import refresh_trip_sheet
from app.services.utils import get_sgt_now, get_week_start, get_month_start
from app.services.ledger_queries import (
    get_recent_page, get_live_entry, soft_delete_entry, restore_entry,
    get_period_summary, get_category_summary, find_recent_duplicate,
    get_trip_split_entries, get_trip_settlements,
)
from app.services.dates import apply_date_resolution
from app.database import get_connection, get_read_connection
from app.services.participants import (
    CLOSED_CARD_WARNING, NO_WALLET_MESSAGE, Directory, WalletNotSetUp, load_directory, resolve_payment,
    NO_OUTSIDE_ACCOUNT_MESSAGE, PAYER_UNCLEAR_WARNING, OutsideAccountNotSetUp, read_payer,
    NO_OUTSIDE_ACCOUNT_TOAST, NO_PAYER_ACCOUNT_TOAST, PayerHasNoAccount, change_payer,
    PAYER_READ_AS_WARNING, second_reader_payer, change_account, OUTSIDE_PAYER_ACCOUNT_NAME,
)
from app.services.splits import (
    resolve_split, menu_people, split_groups, split_people, split_rows,
    open_split, toggle_split_person, toggle_split_family, remove_split,
    amounts_prompt, parse_split_amounts, AMOUNTS_PROMPT_LINE, drop_share_entries,
)
from app.services.trips import (
    Trip, load_active_trip, trip_families, switch_trip_off, switch_trip_on, member_trips,
    member_trip, banner_message_id, set_banner_message_id, format_trip_dates,
)
from app.services.balances import (
    trip_balance_lines, trip_payments, unit_of, open_bill_shares, line_parts, payment_rows,
    balance_currencies, default_balance_currency, trip_payment_lines, trip_payback_lines, payment_button_label,
    unit_name, unit_balances, NO_PAYER_LINE,
)
from app.services.settlements import (
    add_settlement, is_overpayment, settlement_card_text, change_settlement_amount, parse_settlement_amount,
    payback_default_note,
)
from app.services.settle import Bill, SettleChoices, SettleRefused, is_settle, read_settle
from app.services.settle_card import build_settle_card, settle_card_state
from app.services.fx import reference_rate, convert, rate_cached
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
    with get_read_connection() as conn:
        return load_directory(conn)


def _load_active_trip(participant_id: int) -> Trip | None:
    """Reads which trip, if any, is on. Read fresh for every entry, like the directory, so a
    /trip switch by this person applies to the very next message. Its own function so tests can
    swap it out without a database."""
    with get_read_connection() as conn:
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

    # /start is where the button menu under the text box is handed out (#163). Telegram keeps a
    # persistent reply keyboard until the bot removes it, so one /start is enough; sending
    # /start again brings it back if it was hidden.
    await update.message.reply_text(
        f"Hello {username}! Your ID is authorized by admin. Send me a voice note to get started.\n"
        "The buttons under the text box open the bot's screens.",
        reply_markup=_menu_keyboard())


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

# --- /recent: the latest entries, each one deletable (ADR-0050, #115) -----------------------
#
# Stateless on purpose: every button carries the entry's id and the page it was drawn on, and
# every tap reads the database fresh. Nothing is pending in memory, so there is no card lock
# here; two overlapping taps are made safe by the queries themselves (a delete changes only a
# live row, an undo only a deleted one, so the second tap changes nothing and says so).
#
# Any household member may delete any entry. Every bot user is household (a database check),
# so passing authorize() is the whole permission check.
#
# Sent as plain text (no parse_mode): descriptions are raw messages, and a stray _ or * in
# one would otherwise break the whole reply.

RECENT_PAGE_SIZE = 5
RECENT_ERROR = "❌ Couldn't read the ledger, try again."
RECENT_CHANGE_ERROR = "❌ Couldn't change the ledger, nothing was changed. Try again."
RECENT_EMPTY = "No entries yet. 📭"
RECENT_GONE_TOAST = "That entry is already deleted."
RECENT_DELETED_TOAST = "Deleted."
RECENT_RESTORED_TOAST = "Restored."
RECENT_ALREADY_LIVE_TOAST = "That entry is already back."
# How much of a description a list line shows. The confirm screen shows more, so you can see
# exactly what you are deleting.
_RECENT_LINE_DESC = 60
_RECENT_CONFIRM_DESC = 300
# Telegram refuses callback data over 64 bytes. Every id is a 36-character UUID today, which
# fits; an id that wouldn't fit gets no button rather than a button that fails when tapped.
_CALLBACK_DATA_LIMIT = 64


def _short_text(text: str | None, limit: int) -> str:
    text = (text or "").strip() or "(no description)"
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _short_date(iso: str, today) -> str:
    """'2026-11-18' as '18 Nov', with the year only when it isn't this year. date is stored as
    ISO text; anything that doesn't parse is shown as it is rather than hidden."""
    try:
        day = datetime.date.fromisoformat(iso)
    except (TypeError, ValueError):
        return str(iso)
    return day.strftime("%d %b") if day.year == today.year else day.strftime("%d %b %Y")


def _entry_title(entry: dict, today, desc_limit: int) -> str:
    """'18 Nov · Dinner at hawker' plus markers: [Transfer], 🧳 tagged to a trip, ✂️ split."""
    title = f"{_short_date(entry['date'], today)} · {_short_text(entry['description'], desc_limit)}"
    # The bot records a transfer (a YouTrip top-up) as transaction_type 'Transfer', with
    # category 'YouTrip top-up'; only very old rows say 'Transfer' in the category.
    if "Transfer" in (entry.get("transaction_type"), entry.get("category")):
        title += " [Transfer]"
    if entry.get("trip_name"):
        title += " 🧳"
    if entry.get("split_count"):
        title += " ✂️"
    return title


def _entry_amount(entry: dict) -> str:
    return f"{entry['currency']} {format_cents(int(entry['amount']))}"


def _logged_by(entry: dict, names: dict) -> str | None:
    """The sender's name, or None for rows whose sender was never recorded (CLI, API, old rows)."""
    return names.get(entry.get("entered_by_participant_id"))


def _recent_button(label: str, data: str) -> InlineKeyboardButton | None:
    if len(data.encode()) > _CALLBACK_DATA_LIMIT:
        return None
    return InlineKeyboardButton(label, callback_data=data)


def _recent_list(entries: list, more: bool, offset: int, names: dict, today):
    """The list message: numbered entries, one 🗑 button per entry, then Newer/Older."""
    if not entries:
        return RECENT_EMPTY, None
    first, last = offset + 1, offset + len(entries)
    lines = [f"📊 Recent entries ({first}–{last})", ""]
    delete_row = []
    for n, entry in enumerate(entries, first):
        lines.append(f"{n}. {_entry_title(entry, today, _RECENT_LINE_DESC)}")
        who = _logged_by(entry, names)
        lines.append(f"   {_entry_amount(entry)}" + (f" · logged by {who}" if who else ""))
        button = _recent_button(f"🗑 {n}", f"recent:ask:{offset}:{entry['transaction_id']}")
        if button:
            delete_row.append(button)
    rows = [delete_row] if delete_row else []
    paging = []
    if offset > 0:
        paging.append(InlineKeyboardButton("◂ Newer", callback_data=f"recent:page:{max(0, offset - RECENT_PAGE_SIZE)}"))
    if more:
        paging.append(InlineKeyboardButton("Older ▸", callback_data=f"recent:page:{offset + RECENT_PAGE_SIZE}"))
    if paging:
        rows.append(paging)
    return "\n".join(lines), (InlineKeyboardMarkup(rows) if rows else None)


def _recent_confirm(entry: dict, offset: int, names: dict, today):
    """The second tap: the entry in full, and what deleting it changes."""
    lines = ["Delete this entry?", "", _entry_title(entry, today, _RECENT_CONFIRM_DESC),
             f"{_entry_amount(entry)} · {entry.get('category') or 'No category'}"]
    tags = []
    if entry.get("trip_name"):
        tags.append(f"{entry['trip_name']} trip")
    if entry.get("split_count"):
        tags.append(f"split {entry['split_count']} ways")
    if tags:
        lines.append(" · ".join(tags))
    who = _logged_by(entry, names)
    if who:
        lines.append(f"Logged by {who}")
    # Only a split entry on a trip moves a trip balance; say /trip_bals only when it does.
    leaves = "the totals and /trip_bals" if entry.get("trip_name") and entry.get("split_count") else "the totals"
    lines += ["", f"It leaves {leaves}. You can undo it straight after."]
    tid = entry["transaction_id"]
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Yes, delete", callback_data=f"recent:del:{offset}:{tid}"),
        InlineKeyboardButton("◂ Back", callback_data=f"recent:page:{offset}"),
    ]])
    return "\n".join(lines), keyboard


def _recent_deleted(entry: dict, offset: int, today):
    """After a delete: what went, with Undo. Undo lasts until this message is redrawn; after
    that the entry never shows in /recent again (ADR-0050)."""
    text = f"🗑 Deleted: {_entry_title(entry, today, _RECENT_LINE_DESC)}\n{_entry_amount(entry)}"
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("↩ Undo", callback_data=f"recent:undo:{offset}:{entry['transaction_id']}"),
        InlineKeyboardButton("◂ Back to list", callback_data=f"recent:page:{offset}"),
    ]])
    return text, keyboard


def _recent_list_screen(offset: int, names: dict, today):
    """Reads one page and draws it, or None if the read failed. A page left empty by deletes
    (offset past the end) falls back to the newest page rather than saying "no entries"."""
    page = get_recent_page(offset, RECENT_PAGE_SIZE)
    if page is not None and not page[0] and offset > 0:
        offset = 0
        page = get_recent_page(offset, RECENT_PAGE_SIZE)
    if page is None:
        return None
    entries, more = page
    return _recent_list(entries, more, offset, names, today)


def _participant_names(directory: Directory) -> dict:
    return {person.id: person.display_name for person in directory.participants}


async def recent_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/recent: the newest entries, with a 🗑 button for each. Nothing is typed after it."""
    directory = await authorize(update)
    if directory is None:
        return
    screen = _recent_list_screen(0, _participant_names(directory), get_sgt_now().date())
    if screen is None:
        await update.effective_message.reply_text(RECENT_ERROR)
        return
    text, keyboard = screen
    await update.effective_message.reply_text(text, reply_markup=keyboard)


def _parse_recent_callback(data: str):
    """'recent:page:<offset>' or 'recent:<ask|del|undo>:<offset>:<transaction id>'.
    Returns (action, offset, transaction id or None); raises ValueError for anything else."""
    parts = data.split(":", 3)
    if len(parts) < 3 or parts[0] != "recent":
        raise ValueError("Invalid recent callback")
    action, offset = parts[1], int(parts[2])
    if not 0 <= offset <= 2**31 - 1:
        raise ValueError("Invalid recent offset")
    if action == "page" and len(parts) == 3:
        return action, offset, None
    if action in ("ask", "del", "undo") and len(parts) == 4 and 0 < len(parts[3]) <= _CALLBACK_DATA_LIMIT:
        return action, offset, parts[3]
    raise ValueError("Invalid recent callback")


async def _refresh_saved_trip_sheet(trip_id):
    """Keep even an unexpected refresh failure away from an already saved card."""
    if trip_id is None:
        return
    try:
        # Await, never create_task: Cloud Run grants CPU only while the webhook is open.
        await refresh_trip_sheet(trip_id)
    except Exception as exc:
        print(f"Trip sheet not updated for trip {trip_id}: {exc}")


async def handle_recent_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A tap under /recent: page, ask (first tap on 🗑), del (Yes, delete) or undo.

    A callback query can be answered exactly once, so the outcome and the new screen are
    worked out first; then one answer(), then the redraw. A failed redraw is only printed:
    a delete or undo that committed stays committed, and the toast already said so.
    """
    query = update.callback_query
    directory = await authorize(update)
    if directory is None:
        await query.answer()
        return
    sender = directory.participant_by_telegram_id(str(update.effective_user.id))
    names = _participant_names(directory)
    today = get_sgt_now().date()
    toast = None
    screen = None
    changed_entry = None
    try:
        action, offset, transaction_id = _parse_recent_callback(query.data)
    except ValueError as e:
        print(f"Bad /recent button: {e}")
        await query.answer(RECENT_ERROR)
        return

    if action == "page":
        screen = _recent_list_screen(offset, names, today)
        if screen is None:
            toast = RECENT_ERROR

    elif action in ("ask", "del"):
        # Read the entry first: the confirm screen shows it, and the deleted screen needs it
        # after the delete, when the view can no longer see it.
        entry = get_live_entry(transaction_id)
        if entry is None:
            toast = RECENT_ERROR
        elif not entry:
            # Deleted since this was drawn, by someone else or by this tap's twin. A second
            # "Yes, delete" must not redraw: the first tap's "Deleted" message with its Undo
            # button is on screen, and a redraw would wipe the only way back. So a "del" gets
            # the toast alone; an "ask" (a 🗑 on an old list) shows the fresh list.
            toast = RECENT_GONE_TOAST
            if action == "ask":
                screen = _recent_list_screen(offset, names, today)
        elif action == "ask":
            screen = _recent_confirm(entry, offset, names, today)
        else:
            deleted = soft_delete_entry(transaction_id, sender.id)
            if deleted is None:
                toast = RECENT_CHANGE_ERROR
            elif deleted is True:
                changed_entry = entry
                toast = RECENT_DELETED_TOAST
                screen = _recent_deleted(entry, offset, today)
            else:
                # Lost the race to an overlapping tap: toast only, for the same reason.
                toast = RECENT_GONE_TOAST

    else:  # undo
        restored = restore_entry(transaction_id)
        if restored is None:
            toast = RECENT_CHANGE_ERROR
        else:
            toast = RECENT_RESTORED_TOAST if restored else RECENT_ALREADY_LIVE_TOAST
            if restored is True:
                # Only the restored live view can supply the trip after an undo.
                changed_entry = get_live_entry(transaction_id)
            screen = _recent_list_screen(offset, names, today)
            if screen is None:
                # The undo committed; only the list couldn't be read. Drop the Undo button so
                # it can't be mistaken for "not undone yet".
                screen = ("↩ Restored. Send /recent to see the list.", None)

    try:
        await query.answer(toast)
    except Exception as e:
        print(f"Could not answer /recent tap: {e}")
    if screen is None:
        return
    text, keyboard = screen
    try:
        await query.edit_message_text(text, reply_markup=keyboard)
    except Exception as e:
        print(f"Could not redraw /recent: {e}")
    if changed_entry and changed_entry.get('split_count'):
        await _refresh_saved_trip_sheet(changed_entry.get('trip_id'))

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

# The category periods, in button order: callback key -> button label. The key is what a
# "cat:<key>" button carries and what _category_text takes.
CATEGORY_PERIODS = {"today": "Today", "week": "This week", "month": "This month"}
CATEGORY_PICKER_TEXT = "📊 Categories for which period?"
CATEGORY_GONE_TOAST = "That button is gone. Tap 📊 By category again."


def _category_text(period: str) -> str:
    """The category summary for one period ("today", "week" or "month").

    One builder for the /cat_* commands and the "📊 By category" buttons, so a tap and a typed
    command can never show different totals for the same period. Markdown, as before.
    """
    today = get_sgt_now()
    end_str = today.strftime('%Y-%m-%d')
    if period == "today":
        cat_data = get_category_summary(end_str, end_str)
        return format_category_summary(f"📊 **Today's Categories** ({end_str})", cat_data)
    if period == "week":
        start, title = get_week_start(today), "This Week's Categories"
    else:
        start, title = get_month_start(today), "This Month's Categories"
    start_str = start.strftime('%Y-%m-%d')
    cat_data = get_category_summary(start_str, end_str)
    return format_category_summary(f"📊 **{title}** ({start_str} to {end_str})", cat_data)


async def cat_today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await authorize(update) is None:
        return
    await update.message.reply_text(_category_text("today"), parse_mode="Markdown")


async def cat_week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await authorize(update) is None:
        return
    await update.message.reply_text(_category_text("week"), parse_mode="Markdown")


async def cat_month_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await authorize(update) is None:
        return
    await update.message.reply_text(_category_text("month"), parse_mode="Markdown")


async def category_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The "📊 By category" menu button: one inline button per period, so no command is typed.

    Nothing is read yet; the tap (handle_category_button) reads the totals fresh.
    """
    if await authorize(update) is None:
        return
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"cat:{key}")
                                      for key, label in CATEGORY_PERIODS.items()]])
    await update.message.reply_text(CATEGORY_PICKER_TEXT, reply_markup=keyboard)


async def handle_category_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A tap on a "cat:<period>" button: the picker message becomes that period's summary.

    Reads only (no pending entry, no lock). Answered exactly once: an unknown period (a button
    from an older version) gets a toast and no redraw. The summary replaces the picker so the
    chat isn't left with a dead menu above the answer.
    """
    query = update.callback_query
    if await authorize(update) is None:
        await query.answer()
        return
    period = query.data.removeprefix("cat:")
    if period not in CATEGORY_PERIODS:
        await query.answer(CATEGORY_GONE_TOAST)
        return
    text = _category_text(period)
    try:
        await query.answer()
    except Exception as e:
        print(f"Could not answer category tap: {e}")
    try:
        await query.edit_message_text(text, parse_mode="Markdown")
    except Exception as e:
        # Nothing was changed, so a failed redraw (e.g. a double tap) loses nothing.
        print(f"Could not show category summary: {e}")

# --- Confirmation-card state -------------------------------------------------------------
#
def _md(name: str) -> str:
    """A name escaped for Telegram's Markdown, so a trip called "Beach_2026" shows as typed. An
    unescaped _ or * starts a format that never closes, and Telegram then rejects the whole
    message - on a card, that would stop every entry while the trip is on. Only for text sent with
    parse_mode="Markdown"; button labels and toasts are plain text and need none."""
    return escape_markdown(name, version=1)


# The "/" menu, most used on a trip first (agreed 2026-11-21, #163). Telegram shows them in
# this order, so the order is the design: /start is last because it is needed once.
BOT_COMMANDS = [
    BotCommand("trip_bals", "Who owes whom on a trip"),
    BotCommand("recent", "Recent entries"),
    BotCommand("trip", "Change your trip mode"),
    BotCommand("today", "Today's summary"),
    BotCommand("week", "This week's summary"),
    BotCommand("month", "This month's summary"),
    BotCommand("cat_today", "Today's categories"),
    BotCommand("cat_week", "This week's categories"),
    BotCommand("cat_month", "This month's categories"),
    BotCommand("help", "How to word an entry"),
    BotCommand("start", "Start using the ledger"),
]


# The button menu under the text box (a persistent reply keyboard, agreed 2026-11-21, #163).
# A tap sends the label as an ordinary text message, so each label must be text nobody would
# send as an expense: none has an amount, so none could have been a valid entry anyway.
MENU_WHO_OWES = "💰 Who owes"
MENU_TRIP = "🧳 Trip"
MENU_RECENT = "🕘 Recent"
MENU_HELP = "❓ Help"
MENU_TODAY = "Today"
MENU_WEEK = "This week"
MENU_MONTH = "This month"
MENU_CATEGORIES = "📊 By category"
MENU_SETTLE = '🤝 Settle'
MENU_FAMILY_SETTLE = '👪 Family settle'
SETTLE_PROMPT = '🤝 Settle: who paid, how much, what for?'
FAMILY_SETTLE_PROMPT = '👪 Family settle: who paid, how much, what for?'
MENU_ROWS = (
    (MENU_WHO_OWES, MENU_TRIP),
    (MENU_SETTLE, MENU_FAMILY_SETTLE),
    (MENU_RECENT, MENU_HELP),
    (MENU_TODAY, MENU_WEEK, MENU_MONTH),
    (MENU_CATEGORIES,),
)
MENU_PLACEHOLDER = "Say or type an expense…"


def _menu_keyboard() -> ReplyKeyboardMarkup:
    """The button menu. Persistent, so Telegram keeps it open instead of folding it away after a
    tap, and resized so its four rows don't take half the screen."""
    return ReplyKeyboardMarkup([list(row) for row in MENU_ROWS], resize_keyboard=True,
                               is_persistent=True, input_field_placeholder=MENU_PLACEHOLDER)


def _menu_command(text: str | None):
    """The handler a menu label opens, or None when the text is not exactly a label.

    Only an exact label counts: "Today 12 coffee" or "today" is still an expense message. The
    lookup is built here, not at import time, because the handlers are defined further down.
    Each handler authorizes for itself, exactly as when the command is typed.
    """
    actions = {
        MENU_WHO_OWES: trip_bals_command,
        MENU_TRIP: trip_command,
        MENU_SETTLE: settle_command,
        MENU_FAMILY_SETTLE: family_settle_command,
        MENU_RECENT: recent_command,
        MENU_HELP: help_command,
        MENU_TODAY: today_command,
        MENU_WEEK: week_command,
        MENU_MONTH: month_command,
        MENU_CATEGORIES: category_menu,
    }
    return actions.get(text)


async def _settle_menu_prompt(update, context, first_line):
    """Authorize before opening a stateless settle reply prompt."""
    if await authorize(update) is None:
        return
    await update.message.reply_text(first_line + '\ne.g. Carol paid 381 for dinner',
                                    reply_markup=ForceReply(selective=True))


async def settle_command(update, context):
    """Ask for an ordinary settle message from the button menu."""
    await _settle_menu_prompt(update, context, SETTLE_PROMPT)


async def family_settle_command(update, context):
    """Ask for a family settle message from the button menu."""
    await _settle_menu_prompt(update, context, FAMILY_SETTLE_PROMPT)


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
        with get_read_connection() as conn:
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
        with get_read_connection() as conn:
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
BALS_CUT_SHORT = "… (Too long to show in full.)"


def _load_bals_trip(participant_id: int, trip_id: int | None, *, conn=None) -> Trip | None:
    """The trip to show balances for, with its members' families, or None.

    trip_id None means "the trip this person has switched on". A trip id (from a button) is
    loaded only if this person is a member of it. Its own function, like _load_directory, so
    tests can swap it out without a database.
    """
    # Share one checkout per screen: each costs a ping across the Pacific.
    with (nullcontext(conn) if conn is not None else get_read_connection()) as conn:
        if trip_id is None:
            trip = load_active_trip(conn, participant_id)
        else:
            trip = member_trip(conn, participant_id, trip_id)
        if trip is None:
            return None
        families = trip_families(conn, trip.id)
        return replace(trip, member_ids=tuple(pid for pid, _ in families), families=families)


def _load_bals_choices(participant_id: int, offset: int, *, conn=None):
    """One page of this person's trips for the picker, newest first: (trips, more to come)."""
    # Share one checkout per screen: each costs a ping across the Pacific.
    with (nullcontext(conn) if conn is not None else get_read_connection()) as conn:
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


def _cut_bals_lines(lines):
    """Keep a prefix of whole lines, reserving space for the truncation notice."""
    if len('\n'.join(lines)) <= TELEGRAM_MESSAGE_LIMIT:
        return lines, len(lines)
    kept = []
    size = len(BALS_CUT_SHORT)
    for line in lines:
        if size + len(line) + 1 > TELEGRAM_MESSAGE_LIMIT:
            break
        kept.append(line)
        size += len(line) + 1
    return kept + [BALS_CUT_SHORT], len(kept)


def _bals_screen(trip: Trip, directory: Directory, today, currency=None, screen='cur'):
    """One screen of a trip's balances, for one currency: 'cur' (the main screen), 'pay'
    (all payments, with their "Mark paid" buttons) or 'pbk' (paybacks). ADR-0056.

    Sent as plain text (no parse_mode), so a name with _ or * can't break it and it can be
    forwarded as it is. Bills and paybacks are read fresh on every draw.
    get_trip_split_entries catches its own errors and returns None for a failed read, which
    is shown as an error and never as "nobody owes anything".

    The "Mark paid" callback keeps the payment's number in the whole trip_payments list (all
    currencies), so _new_settlement and buttons on older reports work unchanged.
    """
    header = f"🧳 {trip.name} · {format_trip_dates(trip, today)}"
    entries = get_trip_split_entries(trip.id)
    settlements = get_trip_settlements(trip.id)
    other = [InlineKeyboardButton("Other trip", callback_data="bals:page:0")]
    if entries is None or settlements is None:
        lines, _ = _cut_bals_lines([header, '', BALS_ERROR])
        return '\n'.join(lines), InlineKeyboardMarkup([other])
    names = {person.id: person.display_name for person in directory.participants}
    families = dict(trip.families)
    payments = trip_payments(entries, families, settlements)
    currencies = balance_currencies(entries, settlements, payments)
    if currency not in currencies:
        # A stale currency button returns to a useful main screen, even from a detail view.
        currency, screen = default_balance_currency(currencies, payments), 'cur'
    selected = [(n, p) for n, (c, p) in enumerate(payments, 1) if c == currency]
    rows = []
    if screen == 'pay':
        lines = [f"🧳 {trip.name} · {currency} · to settle",
                 *trip_payment_lines(entries, names, families, currency, settlements)]
        lines, kept = _cut_bals_lines(lines)
        # The first body lines correspond one-to-one to selected payments. Position,
        # rather than matching text, keeps cut or duplicate names from enabling a button.
        rows = [[InlineKeyboardButton(payment_button_label(p, currency, names),
                                      callback_data=f"settle:new:{trip.id}:{n}:{p.cents}")]
                for n, p in selected[:max(0, kept - 1)]][:99]
    elif screen == 'pbk':
        lines, _ = _cut_bals_lines([f"🧳 {trip.name} · {currency} · paybacks",
                                   *trip_payback_lines(entries, names, families, currency, settlements)])
    else:
        lines, _ = _cut_bals_lines([header, *trip_balance_lines(
            entries, names, families, settlements, currency)])
        if len(currencies) > 1:
            buttons = [InlineKeyboardButton(
                ('✓ ' if c == currency else '') + f"{c} ({sum(cur == c for cur, _ in payments)})",
                callback_data=f"bals:cur:{trip.id}:{c}") for c in currencies]
            rows.extend(buttons[i:i + 4] for i in range(0, len(buttons), 4))
        if any(c != 'SGD' for c, _ in payments):
            rows.append([InlineKeyboardButton('💱 All in SGD', callback_data=f'bals:sgd:{trip.id}')])
        if selected:
            rows.append([InlineKeyboardButton(f"All payments ({len(selected)})",
                                              callback_data=f"bals:pay:{trip.id}:{currency}")])
        count = sum(s['currency'] == currency for s in settlements)
        if count:
            rows.append([InlineKeyboardButton(f"View paybacks ({count})",
                                              callback_data=f"bals:pbk:{trip.id}:{currency}")])
    if screen in ('pay', 'pbk'):
        rows.append([InlineKeyboardButton("⬅ Back", callback_data=f"bals:cur:{trip.id}:{currency}")])
    else:
        rows.append(other)
    return '\n'.join(lines), InlineKeyboardMarkup(rows)


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
    message = await update.effective_message.reply_text(text, reply_markup=keyboard)
    _bounded_put(context.user_data.setdefault('bals_reports', {}), message.message_id, directory)


async def handle_bals_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open a trip, picker page, or one currency's main/payments/paybacks screen.

    A callback query can be answered exactly once, so everything is read and the reply is
    built first; then one answer(), then the redraw.
    """
    query = update.callback_query
    if query.data.startswith('bals:sgd:'):
        await _open_sgd_screen(update, context)
        return
    directory = await authorize(update)
    if directory is None:
        await query.answer()
        return
    sender = directory.participant_by_telegram_id(str(update.effective_user.id))
    today = get_sgt_now().date()
    toast = None
    try:
        parts = query.data.split(":")
        action = parts[1] if len(parts) > 1 else ''
        detail = action in ('cur', 'pay', 'pbk')
        if (parts[0] != 'bals' or action not in ('show', 'page', 'cur', 'pay', 'pbk')
                or len(parts) != (4 if detail else 3)
                or not re.fullmatch(r'[0-9]+', parts[2])
                or (detail and not re.fullmatch(r'[A-Z]{3}', parts[3]))):
            raise ValueError("Invalid balances callback")
        value = int(parts[2])
        if not 0 <= value <= 2**63 - 1:
            raise ValueError("Invalid balances callback")
        trip = _load_bals_trip(sender.id, value) if action != "page" else None
        if trip is not None:
            text, keyboard = _bals_screen(trip, directory, today,
                                          parts[3] if detail else None, action if detail else 'cur')
        else:
            # A page of the picker; also where a tap on a trip this person isn't on lands.
            offset = value if action == "page" else 0
            if action != "page":
                toast = BALS_NOT_MEMBER
            choices, more = _load_bals_choices(sender.id, offset)
            text, keyboard = _bals_picker(choices, offset, more, today)
    except Exception as e:
        print(f"Could not load trip balances: {e}")
        try:
            await query.answer(BALS_ERROR)
        except Exception as answer_error:
            # A slow read (a cold start) can outlast Telegram's answer window; the tap
            # changed nothing, so only print it rather than leave an unhandled traceback.
            print(f"Could not answer balances tap: {answer_error}")
        return
    try:
        await query.answer(toast)
    except Exception as e:
        print(f"Could not answer balances tap: {e}")
    try:
        await query.edit_message_text(text, reply_markup=keyboard)
        _bounded_put(context.user_data.setdefault('bals_reports', {}), query.message.message_id, directory)
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
    if len(rows) == 1 and 'set' not in rows[0]:
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
            suffix = (' ✏️' if row['set'] else " (what's left)") if 'set' in row else ''
            lines.append(f" • {_md(row['name'])}: {count}{format_cents(row['share_cents'])}{suffix}")
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
        row = [InlineKeyboardButton(label, callback_data="split:menu")]
        # Often the names are right and only the shares are wrong, so "Set amounts" sits here
        # too, one tap away, whenever two or more people are ticked (ADR-0048).
        if len(transaction.get('split') or ()) >= 2:
            row.append(InlineKeyboardButton("✏️ Set amounts", callback_data="split:amounts"))
        rows.append(row)
    if transaction.get('payer_menu'):
        # Opens the list of people (handle_payer_button). Absent on a YouTrip top-up, and on a
        # card stashed before this button shipped.
        rows.append([InlineKeyboardButton("👤 Change who paid", callback_data="payer:menu"),
                     InlineKeyboardButton("💳 Change card", callback_data="card:menu")])
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


def _card_accounts(transaction: dict) -> tuple:
    """The open ordinary accounts offered by this entry's directory snapshot, in name order."""
    # Share this filter with the tap validator so stale or outside-account ids cannot sneak in.
    return tuple(sorted(
        (a for a in transaction['payer_menu']['directory'].accounts
         if a.is_open and a.name.strip().casefold() != OUTSIDE_PAYER_ACCOUNT_NAME.casefold()),
        key=lambda a: (a.name.casefold(), a.id)))


def _card_keyboard(transaction: dict) -> InlineKeyboardMarkup:
    """Offer accounts two per row, marking the current one and replacing Confirm/Cancel."""
    # Ids keep callbacks short regardless of account name length.
    buttons = [InlineKeyboardButton(("✓ " if a.id == transaction.get('account_id') else "") + a.name,
                                    callback_data=f"card:set:{a.id}")
               for a in _card_accounts(transaction)]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("⬅ Back", callback_data="card:back")])
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
    # At the top, so it is the first thing seen once the right people are ticked.
    if len(marked) >= 2:
        rows.append([InlineKeyboardButton("✏️ Set amounts", callback_data="split:amounts")])
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
    if is_settle(raw_text):
        await _settle_message(update, context, raw_text, status_msg, directory)
        return
    # One "today" for this whole entry, in Singapore time: the prompt reads the message
    # against it and the date resolver counts back from it, so they can't disagree (#15).
    today = get_sgt_now().date()

    # Extract structured data from the raw text
    structured_data = await extract_transactions(raw_text, today, directory.account_names())

    # Check if data exists AND if the 'transactions' list is present
    if not structured_data or not structured_data.get("transactions"):
        await status_msg.edit_text("❌ Failed to extract structured data from your message.")
        return

    # Extract the actual list from the wrapper and the spender. An entry the model made out
    # of a share ("Bob's split is 90") is dropped first, so the guard below doesn't refuse it.
    transactions_list = drop_share_entries(structured_data["transactions"], raw_text)
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

SETTLE_UP_READ_ERROR = "Couldn't load the bills. Try again."
SETTLE_UP_FAILED = "Couldn't read that settle message. Nothing was saved."
SETTLE_UP_GONE = 'This settle card is no longer available. Send the settle message again.'


def _bill_date(value):
    """`transactions.date` is ISO text ("2026-10-19"), but the card prints a day and month
    from it, so turn it into a date here. Text that doesn't parse stays as it is; the
    card shows it raw rather than crash (a str here once crashed the close-match card)."""
    try:
        return datetime.date.fromisoformat(value) if isinstance(value, str) else value
    except ValueError:
        return value


def _load_settle_bills(trip, *, conn=None):
    """Read and fold one scope with its own families; None is failure, never empty debts."""
    scope = trip.id if trip else None
    # Share one checkout per screen: each costs a ping across the Pacific.
    with (nullcontext(conn) if conn is not None else get_read_connection()) as conn:
        entries = get_trip_split_entries(scope, conn=conn)
        settlements = get_trip_settlements(scope, conn=conn)
    if entries is None or settlements is None:
        raise RuntimeError('Bills or paybacks unavailable')
    opened = open_bill_shares(entries, dict(trip.families) if trip else {}, settlements)
    return tuple(Bill(e['transaction_id'], scope, _bill_date(e['date']), e['description'],
                      dollars_to_cents(e['amount']), e['currency'], e['paid_by'],
                      {p: cents for (bid, p), cents in opened.items() if bid == e['transaction_id']})
                 for e in entries if e['paid_by'] is not None)


def _load_settle_snapshot(sender, directory):
    """Capture every allowed scope once; taps use only this result.

    An absent active trip is normal. Missing membership details or failed pages
    are errors, because silently omitting a trip could match the wrong bill.
    """
    # One checkout for the screen avoids a ping across the Pacific for each loader.
    with get_read_connection() as conn:
        active = _load_bals_trip(sender.id, None, conn=conn)
        trips = [active] if active else []
        if active is None:
            offset = 0
            while True:
                page = _load_bals_choices(sender.id, offset, conn=conn)
                if page is None:
                    raise RuntimeError('Trip list unavailable')
                choices, more = page
                for choice in choices:
                    trip = _load_bals_trip(sender.id, choice.id, conn=conn)
                    if trip is None:
                        raise RuntimeError('Trip unavailable')
                    trips.append(trip)
                if not more:
                    break
                if not choices:
                    raise RuntimeError('Empty unfinished trip page')
                offset += TRIP_PAGE_SIZE
        bills = list(_load_settle_bills(None, conn=conn))
        for trip in trips:
            bills.extend(_load_settle_bills(trip, conn=conn))
    # Stable sorting retains the query's created_at ordering for same-day bills.
    # str() keeps an unparsed date (left as text by _bill_date) from crashing the sort;
    # a date's str is its ISO text, so the order is the same.
    bills.sort(key=lambda b: str(b.date))
    people = tuple(p for p in directory.participants if not active or p.id in active.member_ids)
    return dict(bills=tuple(bills), trip_id=active.id if active else None,
                scope_labels={None: 'Everyday', **{t.id: t.name for t in trips}},
                scope_people={None: tuple(p.id for p in directory.participants),
                              **{t.id: t.member_ids for t in trips}},
                scope_families={t.id: {p: label for p, label in dict(t.families).items()
                                      if p in t.member_ids} for t in trips},
                names={p.id: p.display_name for p in directory.participants},
                people=tuple(p.id for p in people)), people, dict(active.families) if active else {}


def _su_state(card):
    """Drop conversion provenance when a choice changes the currency pair.

    A tap never fetches a rate. A newly foreign pair asks for a typed amount;
    conversion from a different pair must never silently follow the choice.
    """
    changed = dict(card)
    probe = settle_card_state({**changed, 'clears_cents': None})
    pair = (probe['result'].paid_currency, probe['result'].currency)
    if changed.get('fx_pair') != pair or pair[0] == pair[1]:
        for key in ('clears_cents', 'fx_rate', 'fx_rate_date', 'fx_source', 'fx_pair'):
            changed.pop(key, None)
    return settle_card_state(changed)


def _su_markup(rows):
    """Translate the pure renderer's index-only buttons at the Telegram boundary."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data)
                                  for label, data in row] for row in rows]) if rows else None


async def _clear_settle_prompt(context, chat_id, card_id, replied_id=None):
    """Delete the card's open amount prompt (and a stale reply target) and drop the links.

    Mirrors _clear_split_prompt. Deletes are best effort (_delete_quietly), so a failed
    delete can never block a redraw or a save.
    """
    prompt_id = context.user_data.get('settle_up_prompt_of_card', {}).pop(card_id, None)
    for previous in dict.fromkeys((prompt_id, replied_id)):
        if previous is not None:
            context.user_data.get('settle_up_prompts', {}).pop(previous, None)
            await _delete_quietly(context.bot, chat_id, previous)


async def _su_prompt(message, context, card_id, card, error=None, replied_id=None):
    """Link a ForceReply to its card; no amount reply can fall into expense extraction.

    A card keeps one open prompt: the old one is deleted only after the new one was sent,
    so a failed send leaves the old prompt usable.
    """
    currency = card['result'].currency
    prompt = f'Reply with how much {currency} this clears, e.g. 6452'
    if error:
        prompt += '\n⚠️ ' + error
    sent = await message.reply_text(prompt, reply_markup=ForceReply(selective=True))
    await _clear_settle_prompt(context, message.chat_id, card_id, replied_id)
    _bounded_put(context.user_data.setdefault('settle_up_prompts', {}), sent.message_id, (card_id, currency))
    _bounded_put(context.user_data.setdefault('settle_up_prompt_of_card', {}), card_id, sent.message_id)


async def _settle_message(update, context, raw_text, status_msg, directory):
    """Read a settle message without extraction, capture bills, then look up at most one rate."""
    started, rate_ms, cached = time.perf_counter(), 0, False
    try:
        sender = directory.participant_by_telegram_id(str(update.effective_user.id))
        try:
            snapshot, people, families = _load_settle_snapshot(sender, directory)
        except Exception as exc:
            print(f'Settle snapshot unavailable: {type(exc).__name__}')
            await status_msg.edit_text(SETTLE_UP_READ_ERROR)
            return
        reading = read_settle(raw_text, people, sender, families)
        # The directory is kept on the card only for the fresh /trip_bals after Confirm
        # (#151), so that report shows the names the card was read with, never a reload.
        # raw_text is the message as read, saved as the payback's note on Confirm: like an
        # expense's description (ADR-0008), the words say who paid for whom, which the
        # stored ids and bill rows alone can't show later.
        card = settle_card_state(dict(snapshot, reading=reading, choices=SettleChoices(), view='card',
                                      raw_text=raw_text,
                                      date=get_sgt_now().date(), entered_by_participant_id=sender.id,
                                      sender_telegram_id=str(update.effective_user.id),
                                      idempotency_key=str(uuid.uuid4()), directory=directory))
        if card['result'].needs_rate:
            pair = card['result'].needs_rate
            cached = rate_cached(*pair, card['date'])
            rate_started = time.perf_counter()
            rate = await reference_rate(*pair, card['date'])
            rate_ms = (time.perf_counter() - rate_started) * 1000
            if rate is not None:
                try:
                    cleared = convert(reading.cents, rate[0])
                except ValueError:
                    cleared = None
                if cleared is not None:
                    card.update(clears_cents=cleared, fx_rate=rate[0], fx_rate_date=rate[1],
                                fx_source='frankfurter', fx_pair=pair)
                    card = settle_card_state(card)
        text, rows = build_settle_card(card)
        await status_msg.edit_text(text, reply_markup=_su_markup(rows))
        _bounded_put(context.user_data.setdefault('pending_settle_ups', {}), status_msg.message_id, card)
        if card['result'].needs_rate:
            await _su_prompt(update.effective_message, context, status_msg.message_id, card)
    except SettleRefused as exc:
        await status_msg.edit_text(str(exc))
    except Exception as exc:
        # Never leave "Extracting data..." up with no answer: a crash while drawing
        # the card once did exactly that on the test bot. Nothing was saved.
        print(f'Settle card failed: {type(exc).__name__}: {exc}')
        try:
            await status_msg.edit_text(SETTLE_UP_FAILED)
        except Exception as edit_exc:
            print(f'Settle error message failed: {type(edit_exc).__name__}')
    finally:
        print(f'settle card {(time.perf_counter() - started) * 1000:.0f} ms '
              f'(rate {rate_ms:.0f} ms, cached {"yes" if cached else "no"})')


async def handle_settle_up_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Serialize every tap, answer once, and commit copied state only after its redraw."""
    query = update.callback_query
    card_id = query.message.message_id
    toast, answered, alert = None, False, False
    try:
        async with _card_lock(context, card_id):
            outcome = _card_outcome(context, card_id)
            if outcome:
                toast = '✅ Payback already saved.' if outcome == 'saved' else '❌ Cancelled.'
                return
            if query.data == 'su:saving':
                toast = '⏳ Saving...'
                return
            pending = context.user_data.setdefault('pending_settle_ups', {})
            card = pending.get(card_id)
            if card is None:
                # A toast only: editing would wipe a card someone else may still be
                # reading (pending cards live in the tapper's own user_data).
                toast = SETTLE_UP_GONE
                return
            if card['sender_telegram_id'] != str(update.effective_user.id):
                return
            # Only a button currently displayed can act. This also rejects negative
            # indexes and fabricated Confirm callbacks on unanswered questions.
            _, displayed = build_settle_card(card)
            if query.data not in {data for row in displayed for _, data in row}:
                return
            action = query.data.split(':')[1]
            if action == 'amount':
                await _su_prompt(query.message, context, card_id, card)
                return
            if action == 'confirm':
                # Answer now, before the blocking save, so the button stops spinning;
                # the "⏳ Saving..." redraw below is what stops a second tap.
                # Set the guard first: if answer() itself fails, it must not be retried.
                answered = True
                await query.answer()
                text, rows = build_settle_card({**card, 'saving': True})
                await query.edit_message_text(text, reply_markup=_su_markup(rows))
                result = card['result']
                try:
                    add_settlement(from_id=result.giver_id, to_id=result.receiver_id, for_id=None,
                                   clears_amount=format_cents(card.get('clears_cents', card['reading'].cents)),
                                   clears_currency=result.currency, paid_amount=format_cents(card['reading'].cents),
                                   paid_currency=result.paid_currency, trip_id=result.trip_id, date=card['date'],
                                   source='telegram_bot', entered_by_participant_id=card['entered_by_participant_id'],
                                   idempotency_key=card['idempotency_key'], note=card.get('raw_text'),
                                   bills=[dict(transaction_id=r.transaction_id, for_id=r.for_id, cents=r.cents) for r in result.rows],
                                   fx_rate=card.get('fx_rate'), fx_rate_date=card.get('fx_rate_date'),
                                   fx_source=card.get('fx_source'), needs_statement_check=result.paid_currency != result.currency)
                except Exception as exc:
                    print(f'Could not save settle: {type(exc).__name__}')
                    text, rows = build_settle_card(card)
                    await query.edit_message_text(text + "\nCouldn't save. Try Confirm again.", reply_markup=_su_markup(rows))
                    return
                # Once persisted, resolve even if Telegram's final edit fails: retrying
                # an old button must not create a second write or reopen the card.
                pending.pop(card_id)
                _mark_card_resolved(context, card_id, 'saved')
                # Saved: the amount prompt is now clutter. Only after a successful save,
                # so a failed save (returned above) keeps it for another try.
                await _clear_settle_prompt(context, query.message.chat_id, card_id)
                text, _ = build_settle_card({**card, 'outcome': 'saved'})
                await query.edit_message_text(text, reply_markup=None)
                # An everyday payback (no trip) has no /trip_bals to show.
                if result.trip_id is not None:
                    await _send_fresh_balances(context, query.message.chat_id,
                                               card['entered_by_participant_id'], result.trip_id,
                                               card['directory'], result.currency)
                    await _refresh_saved_trip_sheet(result.trip_id)
                return
            if action == 'cancel':
                text, _ = build_settle_card({**card, 'outcome': 'cancelled'})
                await query.edit_message_text(text, reply_markup=None)
                pending.pop(card_id)
                _mark_card_resolved(context, card_id, 'cancelled')
                await _clear_settle_prompt(context, query.message.chat_id, card_id)
                return
            changed = dict(card)
            choices = card['choices']
            if action in ('bills', 'giver', 'receivers', 'back'):
                changed['view'] = {'receivers': 'receiver', 'back': 'card'}.get(action, action)
                if action == 'bills':
                    # Freeze the row order for this visit to the menu (ticked first), and
                    # start on the first page.
                    options = card['share_options']
                    changed['bills_order'] = tuple(sorted(range(len(options)), key=lambda i: not options[i][3]))
                    changed['bills_page'] = 0
            elif action == 'everyday':
                changed['choices'] = replace(choices, everyday=True, shares=None)
            elif action == 'family':
                changed['choices'] = replace(choices, family=not card['result'].family)
            else:
                index = int(query.data.split(':')[2])
                if action == 'receiver':
                    changed['choices'] = replace(choices, receiver_id=card['receiver_options'][index][0], shares=None)
                    changed['view'] = 'card'
                elif action == 'person':
                    changed['choices'] = replace(choices, giver_id=card['giver_options'][index], shares=None)
                    changed['view'] = 'card'
                elif action == 'pick':
                    part, bid = card['pick_options'][index]
                    changed['choices'] = replace(choices, picks={**choices.picks, part: bid})
                elif action == 'oldest':
                    changed['choices'] = replace(choices, oldest_first=choices.oldest_first | {card['oldest_options'][index]})
                elif action == 'page':
                    changed['bills_page'] = index
                elif action == 'share':
                    selected = [(bid, p) for bid, p, _, tick in card['share_options'] if tick]
                    key = card['share_options'][index][:2]
                    selected.remove(key) if key in selected else selected.append(key)
                    changed['choices'] = replace(choices, shares=tuple(selected))
            try:
                changed = _su_state(changed)
            except SettleRefused as exc:
                # A menu choice that can't work (say, a new giver who owes nothing here)
                # is an alert, still inside the lock; the card and its pending state stay.
                # An alert, not a toast: a toast vanishes in a second and the tap looks
                # dead, as the payer button's "no account fits" learned (show_alert).
                toast, alert = str(exc), True
                return
            text, rows = build_settle_card(changed)
            await query.edit_message_text(text, reply_markup=_su_markup(rows))
            pending[card_id] = changed
            if changed['result'].needs_rate and changed.get('view') == 'card':
                await _su_prompt(query.message, context, card_id, changed)
            elif changed['result'].paid_currency == changed['result'].currency:
                # No longer paid in another currency (say, a receiver whose bills are in
                # the paid currency): an open amount prompt has nothing left to set.
                await _clear_settle_prompt(context, query.message.chat_id, card_id)
    except Exception as exc:
        print(f'Settle tap failed: {type(exc).__name__}')
    finally:
        if not answered:
            if alert:
                await query.answer(toast, show_alert=True)
            else:
                await query.answer(toast) if toast else await query.answer()


async def _settle_up_reply(update, context):
    """Consume FX replies under the card lock, validating currency before any change.

    The reply message is always deleted (inside the lock, like split replies), but only
    after the bot's answer to it was sent. The prompt is deleted too once the amount was
    read, or if the card is gone; a refused amount replaces the prompt with the error below
    its fixed first line.
    """
    message = update.message
    replied = message.reply_to_message
    link = context.user_data.get('settle_up_prompts', {}).get(replied.message_id) if replied else None
    chat_id = message.chat_id
    if link is None:
        if (replied and isinstance(replied.text, str) and replied.text.startswith('Reply with how much ')
                and replied.from_user and replied.from_user.id == context.bot.id):
            # Answer first, then delete: see _reply_deleted_last.
            await message.reply_text(SETTLE_UP_GONE)
            await _delete_quietly(context.bot, chat_id, message.message_id)
            await _delete_quietly(context.bot, chat_id, replied.message_id)
            return True
        return False
    card_id, currency = link
    async with _card_lock(context, card_id), _reply_deleted_last(context, message):
        pending = context.user_data.get('pending_settle_ups', {})
        card = pending.get(card_id)
        if card is None:
            await _clear_settle_prompt(context, chat_id, card_id, replied.message_id)
            await message.reply_text(SETTLE_UP_GONE)
            return True
        if currency != card['result'].currency:
            await message.reply_text('The bills changed. Use Change amount cleared on the card.')
            return True
        try:
            cents, code = parse_settlement_amount(message.text, currency, (currency,))
            if code != currency:
                raise ValueError(f'Use {currency} for the amount it clears')
        except ValueError as exc:
            await _su_prompt(message, context, card_id, card, str(exc), replied.message_id)
            return True
        changed = dict(card, clears_cents=cents, fx_rate=Decimal(cents) / Decimal(card['reading'].cents),
                       fx_rate_date=get_sgt_now().date(), fx_source='typed', view='card',
                       fx_pair=(card['result'].paid_currency, currency))
        # The reply is about to be deleted, so a failure here must still say something; the
        # card stays as it was, with the error in a replacement prompt.
        try:
            changed = settle_card_state(changed)
            text, rows = build_settle_card(changed)
            await context.bot.edit_message_text(chat_id=chat_id, message_id=card_id,
                                                text=text, reply_markup=_su_markup(rows))
        except SettleRefused as exc:
            await _su_prompt(message, context, card_id, card, str(exc), replied.message_id)
            return True
        except Exception as exc:
            print(f'Settle amount reply failed: {type(exc).__name__}')
            await _su_prompt(message, context, card_id, card,
                             "Couldn't change the card. Reply again.", replied.message_id)
            return True
        pending[card_id] = changed
        # Read and redrawn: the prompt has done its job.
        await _clear_settle_prompt(context, chat_id, card_id, replied.message_id)
    return True


def _sgd_data(trip):
    """Read every input together and distinguish unavailable storage from missing rates."""
    entries, paid, rates = (get_trip_split_entries(trip.id), get_trip_settlements(trip.id),
                            get_trip_rates(trip.id))
    if entries is None or paid is None or rates is None:
        raise _SettlementUnavailable(BALS_ERROR)
    payments = trip_payments(entries, dict(trip.families), paid)
    needed = sorted({c for c, _ in payments if c != 'SGD'})
    return entries, paid, rates, payments, needed


def _sgd_line_text(line, names, button=False):
    """The button and report use the same order and the same whole-cent amount."""
    owing, receiver = unit_name(line.from_unit, names), names[line.to_person]
    if line.sgd_cents == 0:
        return f'{owing} and {receiver} are even'
    verb = '→' if button else 'pays'
    return f'{owing} {verb} {receiver} · {format_money(line.sgd_cents, "SGD")}' if button else (
        f'{owing} {verb} {receiver} {format_money(line.sgd_cents, "SGD")}')


def _sgd_shortcut_notes(line, names):
    """The same 💡 words as each currency's own screen, one per shortcut on the line."""
    return [f'💡 {unit_name(line.from_unit, names)} pays {names[line.to_person]} the '
            f'{format_money(cents, "SGD")} {unit_name(unit, names)} owes, and pays '
            f'{names[middle]} {format_money(cents, "SGD")} less.'
            for unit, middle, cents in line.shortcuts]


def _sgd_screen(trip, directory, data=None):
    """Render stored agreements only; automatic lookups belong solely to the opening tap."""
    entries, paid, rates, payments, needed = data or _sgd_data(trip)
    names = {p.id: p.display_name for p in directory.participants}
    lines = [f'🧳 {trip.name} · all in SGD']
    rows = [[InlineKeyboardButton(f'✏️ {c} rate', callback_data=f'sgd:rate:{trip.id}:{c}')
             for c in needed[i:i + 4]] for i in range(0, len(needed), 4)]
    for currency in needed:
        lines.append(rate_display(currency, rates[currency], names) if currency in rates else
                     f'⚠️ No {currency} rate yet. Tap ✏️ {currency} rate.')
    # A split bill with no payer is left out of trip_payments, so without this line the screen
    # could say "Everyone is settled" while that bill was never counted (Codex review, #171).
    # Same words as the main screen; put above the lines so the ✅ buttons still match them.
    no_payer = len(unit_balances(entries, dict(trip.families), paid).no_payer)
    if no_payer:
        lines.append(NO_PAYER_LINE.format(count=no_payer,
                                          entries='entry has' if no_payer == 1 else 'entries have',
                                          they='it is' if no_payer == 1 else 'they are'))
    lines.append('')
    offset = len(lines)
    suggestions = ()
    if all(c in rates for c in needed):
        suggestions = one_currency(payments, dict(trip.families), {c: r['rate'] for c, r in rates.items()})
        lines.extend(_sgd_line_text(line, names) for line in suggestions)
        if not suggestions:
            lines.append('Everyone is settled.')
        # After the payment lines, so the ✅ buttons still match the lines they follow.
        lines.extend(note for line in suggestions for note in _sgd_shortcut_notes(line, names))
    lines, kept = _cut_bals_lines(lines)
    rows.extend([InlineKeyboardButton('✅ ' + _sgd_line_text(line, names, True),
                                     callback_data=f'sgd:new:{trip.id}:{i}:{line.sgd_cents}')]
                for i, line in enumerate(suggestions[:max(0, kept - offset)], 1))
    currency = default_balance_currency(balance_currencies(entries, paid, payments), payments) or 'SGD'
    rows.append([InlineKeyboardButton('⬅ Back', callback_data=f'bals:cur:{trip.id}:{currency}')])
    return '\n'.join(lines), InlineKeyboardMarkup(rows)


async def _open_sgd_screen(update, context):
    """Fill absent rates once per opening, preserving stored or concurrently typed rates."""
    query = update.callback_query
    directory = context.user_data.get('bals_reports', {}).get(query.message.message_id)
    if directory is None:
        await query.answer(SETTLE_STALE)
        return
    try:
        sender = directory.participant_by_telegram_id(str(update.effective_user.id))
        if sender is None:
            raise _SettlementUnavailable(BALS_NOT_MEMBER)
        trip = _load_bals_trip(sender.id, int(query.data.split(':')[2]))
        if trip is None:
            raise _SettlementUnavailable(BALS_NOT_MEMBER)
        data = _sgd_data(trip)
        for currency in data[4]:
            if currency in data[2]:
                continue
            found = await reference_rate('SGD', currency, min(trip.end_date, get_sgt_now().date()))
            if found and not upsert_trip_rate(trip.id, currency, *found, 'frankfurter'):
                raise _SettlementUnavailable(BALS_ERROR)
        # Re-read so a racing typed agreement wins both storage and the displayed screen.
        text, keyboard = _sgd_screen(trip, directory)
    except Exception as exc:
        await query.answer(str(exc) if isinstance(exc, _SettlementUnavailable) else BALS_ERROR)
        return
    await query.answer()
    await query.edit_message_text(text, reply_markup=keyboard)


def _sgd_prompt(trip, currency, row, names):
    """Persist direction in the prompt itself, so replies survive a process restart."""
    lines = [f'💱 {currency} rate · {trip.name} · #{trip.id}']
    if row:
        lines.append('Now: ' + rate_display(currency, row, names))
    if row and row['rate'] < 1:
        lines.append(f'Reply with how many SGD 1 {currency} cost, e.g. 1.2834')
    else:
        lines.append(f'Reply with how many {currency} 1 SGD bought, e.g. 14100')
    return '\n'.join(lines)


async def _sgd_rate_prompt(update, context):
    """Use the report's directory snapshot, and check membership before offering a rate."""
    query = update.callback_query
    directory = context.user_data.get('bals_reports', {}).get(query.message.message_id)
    if directory is None:
        await query.answer(SETTLE_STALE)
        return
    try:
        _, _, trip_id, currency = query.data.split(':')
        sender = directory.participant_by_telegram_id(str(update.effective_user.id))
        trip = _load_bals_trip(sender.id, int(trip_id)) if sender else None
        if trip is None:
            raise _SettlementUnavailable(BALS_NOT_MEMBER)
        data = _sgd_data(trip)
        if currency not in data[4]:
            raise _SettlementUnavailable(SETTLE_STALE)
        text = _sgd_prompt(trip, currency, data[2].get(currency),
                           {p.id: p.display_name for p in directory.participants})
    except Exception as exc:
        await query.answer(str(exc) if isinstance(exc, _SettlementUnavailable) else BALS_ERROR)
        return
    await query.answer()
    await query.message.reply_text(text, reply_markup=ForceReply(selective=True))


async def _sgd_rate_reply(update, context, directory):
    """Consume bot-authored rate replies statelessly, before any extraction path."""
    message = update.message
    replied = message.reply_to_message
    if not (replied and replied.from_user and replied.from_user.id == context.bot.id
            and isinstance(replied.text, str)):
        return False
    match = re.fullmatch(r'💱 ([A-Z]{3}) rate · .+ · #([0-9]+)', replied.text.split('\n', 1)[0])
    if not match:
        return False
    currency, trip_id = match.group(1), int(match.group(2))
    async with _reply_deleted_last(context, message):
        try:
            sender = directory.participant_by_telegram_id(str(update.effective_user.id))
            trip = _load_bals_trip(sender.id, trip_id) if sender else None
            if trip is None:
                raise _SettlementUnavailable(BALS_NOT_MEMBER)
            rates = get_trip_rates(trip_id)
            if rates is None:
                raise _SettlementUnavailable(BALS_ERROR)
            if currency == 'SGD':
                raise ValueError('SGD does not need a rate.')
            rate = parse_rate(message.text, currency, replied.text, rates.get(currency))
            if not upsert_trip_rate(trip_id, currency, rate, get_sgt_now().date(), 'typed', sender.id):
                raise _SettlementUnavailable(BALS_ERROR)
        except ValueError as exc:
            # Keep the original direction even if another member changed the agreement.
            text = replied.text.split('\n⚠️ ', 1)[0] + f'\n⚠️ {exc}'
            await message.reply_text(text, reply_markup=ForceReply(selective=True))
            await _delete_quietly(context.bot, message.chat_id, replied.message_id)
            return True
        except Exception as exc:
            await message.reply_text(str(exc) if isinstance(exc, _SettlementUnavailable) else BALS_ERROR)
            return True
        await _delete_quietly(context.bot, message.chat_id, replied.message_id)
        await _send_fresh_sgd(context, message.chat_id, sender.id, trip_id, directory)
    return True


async def _send_fresh_sgd(context, chat_id, sender_id, trip_id, directory):
    """Serialize fresh reports and register their buttons; a saved payment stays saved."""
    async with _redraw_lock(context, chat_id, trip_id):
        try:
            trip = _load_bals_trip(sender_id, trip_id)
            if trip is None:
                return
            text, keyboard = _sgd_screen(trip, directory)
            sent = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
            _bounded_put(context.user_data.setdefault('bals_reports', {}), sent.message_id, directory)
        except Exception as exc:
            print(f'Could not send fresh SGD balances: {type(exc).__name__}')


def _sgd_keyboard(card, people=False):
    """Full-line paybacks offer only payer selection, Confirm and Cancel."""
    if people:
        return _people_keyboard(card['people'], 'sgd:person', 'sgd:back', (card['from_id'],))
    first = ([InlineKeyboardButton('Confirm', callback_data='sgd:confirm')]
             if card['from_id'] is not None else [])
    if card.get('save_started'):
        # After a half-finished save only a retry makes sense: Cancel and Who paid would
        # leave the saved parts without the rest (_sgd_tap refuses both as well).
        return InlineKeyboardMarkup([first])
    return InlineKeyboardMarkup([first + [InlineKeyboardButton('Cancel', callback_data='sgd:cancel')],
                                 [InlineKeyboardButton('Who paid', callback_data='sgd:who')]])


def _sgd_card_text(card):
    """Explain the gross debts and retained reverse money behind the net cash transfer."""
    names, line = card['names'], card['line']
    lines = ['💱 ' + _sgd_line_text(line, names)]
    for part in line.parts:
        amount = format_money(part.cents_in_currency, part.currency)
        if part.currency != 'SGD':
            amount += (f" at {rate_equation(part.currency, card['rates'][part.currency]['rate'])}"
                       f' → {format_money(part.sgd_cents, "SGD")}')
        debt = f'{unit_name(part.payment.from_unit, names)} owes {names[part.payment.to_unit[1]]}'
        if part.reverse and part.payment.to_unit[1] not in card['debtor_member_ids']:
            # Moved here with a whole shortcut line: it is owed to the middle person's side,
            # so it comes off the money they pass on, not off this card's amount.
            amount = f'Less {amount} that {debt}, off what is passed on'
        elif part.reverse:
            amount = f'Less {amount} that {debt}'
        elif part.giver is not None:
            # A shortcut's middle person passes this on: it clears their family's debt.
            amount = f'{amount} of what {debt}, passed on by {names[part.giver]}'
        elif part.payment.to_unit[1] != line.to_person:
            amount = f'{amount} that {debt}'
        lines.append(amount)
    lines.extend(_sgd_shortcut_notes(line, names))
    lines.append('Paid by: ' + names.get(card['from_id'], 'Choose who paid'))
    return '\n'.join(lines)


async def _new_sgd_settlement(update, context):
    """Recheck fresh debts and freeze every part's provenance and retry key at card-open."""
    query = update.callback_query
    directory = context.user_data.get('bals_reports', {}).get(query.message.message_id)
    if directory is None:
        await query.answer(SETTLE_STALE)
        return
    try:
        sender = directory.participant_by_telegram_id(str(update.effective_user.id))
        if sender is None:
            raise _SettlementUnavailable(SETTLE_GONE)
        match = re.fullmatch(r'sgd:new:([0-9]+):([0-9]+):([0-9]+)', query.data)
        if not match:
            raise _SettlementUnavailable(SETTLE_STALE)
        trip_id, index, cents = map(int, match.groups())
        if not (0 < trip_id <= 2**63 - 1 and 0 < index <= 2**63 - 1 and 0 <= cents <= 2**63 - 1):
            raise _SettlementUnavailable(SETTLE_STALE)
        trip = _load_bals_trip(sender.id, trip_id)
        if trip is None:
            raise _SettlementUnavailable(BALS_NOT_MEMBER)
        entries, paid, rates, payments, needed = _sgd_data(trip)
        if any(c not in rates for c in needed):
            raise _SettlementUnavailable(SETTLE_STALE)
        families = dict(trip.families)
        lines = one_currency(payments, families, {c: r['rate'] for c, r in rates.items()})
        if index > len(lines) or lines[index - 1].sgd_cents != cents:
            raise _SettlementUnavailable(SETTLE_STALE)
        line = lines[index - 1]
        if any(p.sgd_cents <= 0 or p.cents_in_currency <= 0 for p in line.parts):
            raise _SettlementUnavailable('A debt rounds below one cent. Use its original currency to settle.')
        members = ((line.from_unit[1],) if line.from_unit[0] == 'person' else
                   tuple(pid for pid in trip.member_ids if unit_of(pid, families) == line.from_unit))
        # Nobody may pay a card that would have them pay themselves: the line's receiver, and
        # anyone a shortcut on it sends the payer's money to (Codex review, #171). Checked here,
        # before any part is saved, since a half-saved card can't change payer.
        paid_to = {line.to_person} | {p.payment.to_unit[1] for p in line.parts
                                      if p.giver is None and not p.reverse}
        payer = line.from_unit[1] if line.from_unit[0] == 'person' else sender.id if sender.id in members else None
        payer = None if payer in paid_to else payer
        # A netted card hands money back to the owing family (the receiver repays their
        # smaller debt). Paid by an outsider, that would record the outsider handing over the
        # gross amount and a relative receiving money they never got (Codex review, #171),
        # so such a card is paid only from inside the owing family.
        insiders_only = any(p.payment.to_unit[1] in members for p in line.parts)
        all_paths = _sgd_paths(lines, entries, families, paid, trip.member_ids)
        parts = []
        for j, part in enumerate(line.parts):
            unit = part.payment.from_unit
            part_members = ((unit[1],) if unit[0] == 'person' else
                            tuple(pid for pid in trip.member_ids if unit_of(pid, families) == unit))
            parts.append(dict(part=part, members=part_members, idempotency_key=str(uuid.uuid4()),
                              line_parts=all_paths[(index - 1, j)]))
        card = dict(kind='sgd', trip_id=trip.id, trip_name=trip.name, line=line, parts=tuple(parts),
                    rates=rates, directory=directory, names={p.id: p.display_name for p in directory.participants},
                    from_id=payer, to_id=line.to_person, for_id=None, debtor_member_ids=members,
                    family=line.from_unit[1] if line.from_unit[0] == 'family' else None,
                    date=get_sgt_now().date(), entered_by_participant_id=sender.id,
                    people=tuple(sorted((p for p in directory.participants
                                         if p.id in trip.member_ids and p.id not in paid_to
                                         and (p.id in members or not insiders_only)),
                                        key=lambda p: (p.id not in members, p.display_name.lower(), p.id))))
    except Exception as exc:
        await query.answer(str(exc) if isinstance(exc, _SettlementUnavailable) else BALS_ERROR)
        return
    await query.answer()
    sent = await query.message.reply_text(_sgd_card_text(card), reply_markup=_sgd_keyboard(card, payer is None))
    _bounded_put(context.user_data.setdefault('pending_settlements', {}), sent.message_id, card)


def _sgd_paths(lines, entries, families, paid, member_ids):
    """The bill paths behind every part, keyed (line index, part index).

    A payment can be cut over several cards (a reverse part, or a shortcut's pass-on
    piece). Reusing its full paths on each card would clear the first bill twice, so each
    piece gets a disjoint portion. Pieces someone else hands on go first, with that
    person's own shares first, so a middle person's own debt is the one cleared.
    """
    used = {}
    for line in lines:
        for part in line.parts:
            used[part.number] = used.get(part.number, 0) + 1
    remaining, result = {}, {}
    order = sorted(((i, j) for i, line in enumerate(lines) for j in range(len(line.parts))),
                   key=lambda ij: (lines[ij[0]].parts[ij[1]].giver is None, ij))
    for i, j in order:
        part = lines[i].parts[j]
        unit = part.payment.from_unit
        if part.number not in remaining:
            remaining[part.number] = line_parts(entries, families, paid, part.currency,
                                                 unit, part.payment.to_unit[1])
        paths = remaining[part.number]
        if paths is None or used[part.number] == 1:
            result[(i, j)] = paths
            continue
        members = ((unit[1],) if unit[0] == 'person' else
                   tuple(pid for pid in member_ids if unit_of(pid, families) == unit))
        first = part.giver if part.giver in members else min(members)
        result[(i, j)], remaining[part.number] = take_line_parts(paths, first, part.cents_in_currency)
    return result


def _save_sgd_parts(card):
    """Retry the same immutable parts in order; already inserted keys are harmless."""
    for item in card['parts']:
        part = item['part']
        # Each part is a payback of its own payment, to that payment's receiver: the card's
        # payer hands most over; a reverse part comes from the line's receiver, and a
        # shortcut's pass-on piece from its middle person (Part.giver).
        giver = (part.giver if part.giver is not None else
                 card['to_id'] if part.reverse else card['from_id'])
        receiver = part.payment.to_unit[1]
        beneficiary = None if giver in item['members'] else min(item['members'])
        unit = part.payment.from_unit
        note_card = dict(card, from_id=giver, for_id=beneficiary,
                         family=unit[1] if unit[0] == 'family' else None)
        note = payback_default_note(note_card) + ' · all in SGD'
        fx = {}
        if part.currency != 'SGD':
            row = card['rates'][part.currency]
            fx = dict(fx_rate=row['rate'], fx_rate_date=row['rate_date'], fx_source=row['source'])
            note += ' at ' + rate_equation(part.currency, row['rate'])
        add_settlement(from_id=giver, to_id=receiver, for_id=beneficiary,
                       clears_amount=format_cents(part.cents_in_currency), clears_currency=part.currency,
                       paid_amount=format_cents(part.sgd_cents), paid_currency='SGD',
                       trip_id=card['trip_id'], date=card['date'], source='telegram_bot',
                       entered_by_participant_id=card['entered_by_participant_id'],
                       idempotency_key=item['idempotency_key'], note=note, needs_statement_check=False,
                       **({'bills': payment_rows(item['line_parts'], beneficiary or giver,
                                                part.cents_in_currency)} if item['line_parts'] else {}), **fx)


async def handle_sgd_button(update, context):
    """Dispatch reports and serialize card taps without ever loading the directory."""
    query = update.callback_query
    if query.data.startswith('sgd:rate:'):
        await _sgd_rate_prompt(update, context)
        return
    if query.data.startswith('sgd:new:'):
        await _new_sgd_settlement(update, context)
        return
    async with _card_lock(context, query.message.message_id):
        await _sgd_tap(query, context)


async def _sgd_tap(query, context):
    """Copy before redraw; save with original keys after showing a non-actionable button."""
    message_id = query.message.message_id
    outcome = _card_outcome(context, message_id)
    if outcome:
        await query.answer('✅ Paid back.' if outcome == 'saved' else '❌ Cancelled.')
        return
    pending = context.user_data.setdefault('pending_settlements', {})
    card = pending.get(message_id)
    if card is None or card.get('kind') != 'sgd':
        await query.answer(SETTLE_GONE)
        return
    action = query.data.removeprefix('sgd:')
    if action == 'saving':
        await query.answer('⏳ Saving...')
        return
    if action == 'confirm':
        if card['from_id'] not in {p.id for p in card['people']}:
            await query.answer('Choose who paid first.')
            return
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton('⏳ Saving...', callback_data='sgd:saving')]]))
        # A partial save may already contain this giver. Retrying the same keys with a
        # different giver would silently mix two agreements on one card.
        card = dict(card, save_started=True)
        pending[message_id] = card
        try:
            _save_sgd_parts(card)
        except Exception as exc:
            print(f'Could not save SGD payback: {type(exc).__name__}')
            await query.edit_message_text(_sgd_card_text(card) + "\nCouldn't save. Try Confirm again.",
                                          reply_markup=_sgd_keyboard(card))
            return
        pending.pop(message_id)
        _mark_card_resolved(context, message_id, 'saved')
        await query.edit_message_text(_sgd_card_text(card) + '\n✅ Paid back.', reply_markup=None)
        await _send_fresh_sgd(context, query.message.chat_id, card['entered_by_participant_id'],
                              card['trip_id'], card['directory'])
        await _refresh_saved_trip_sheet(card['trip_id'])
        return
    if action == 'cancel':
        if card.get('save_started'):
            # Some parts may already be saved. Cancelling would throw away the keys that let
            # Confirm finish the rest, leaving the line half recorded, so only Confirm is left.
            await query.answer('Some of this is already saved. Tap Confirm to finish it.')
            return
        await query.answer()
        await query.edit_message_text(_sgd_card_text(card) + '\n❌ Cancelled.', reply_markup=None)
        pending.pop(message_id)
        _mark_card_resolved(context, message_id, 'cancelled')
        return
    changed = dict(card)
    if action.startswith('person:'):
        if card.get('save_started'):
            await query.answer('Saving has started. Retry Confirm with the same payer.')
            return
        value = action.removeprefix('person:')
        if not value.isascii() or not value.isdigit() or int(value) not in {p.id for p in card['people']}:
            await query.answer('Choose someone from this trip.')
            return
        changed['from_id'] = int(value)
        changed['for_id'] = None if int(value) in card['debtor_member_ids'] else min(card['debtor_member_ids'])
    elif action not in ('who', 'back'):
        await query.answer(SETTLE_GONE)
        return
    await query.answer()
    await query.edit_message_text(_sgd_card_text(changed), reply_markup=_sgd_keyboard(changed, action == 'who'))
    pending[message_id] = changed


# typed expense entry - no transcription step, the message body IS the input
SETTLE_STALE = "Balances changed, open /trip_bals again."
SETTLE_GONE = "This payback card is no longer available. Open /trip_bals again."
SETTLE_PROMPTS = {
    'clears': "Reply with the amount it clears, e.g. 250000",
    'paid': "Reply with what was handed over, e.g. 50 SGD",
}


class _SettlementUnavailable(Exception):
    """A refused new card with a specific toast, before the query has been answered."""


def _settle_keyboard(card: dict) -> InlineKeyboardMarkup:
    """Offer Confirm only after an trip member has been picked."""
    first = []
    if card['from_id'] is not None:
        label = ("⚠️ Confirm overpayment" if is_overpayment(card['clears_cents'], card['suggested_cents'])
                 else "Confirm")
        first.append(InlineKeyboardButton(label, callback_data="settle:confirm"))
    first.append(InlineKeyboardButton("Cancel", callback_data="settle:cancel"))
    # Two buttons per row at most, so the labels aren't cut off on a phone screen.
    return InlineKeyboardMarkup([first, [
        InlineKeyboardButton("Other amount", callback_data="settle:clears"),
        InlineKeyboardButton("Other currency", callback_data="settle:paid"),
    ], [InlineKeyboardButton("Who paid", callback_data="settle:who")]])


def _settle_people(card: dict) -> InlineKeyboardMarkup:
    """Use the people menu shared with expenses, with owing-unit members first."""
    return _people_keyboard(card['people'], "settle:person", "settle:back", (card['from_id'],))


async def _new_settlement(update, context):
    """Recheck the suggestion and keep its report and snapshot for a redraw after saving."""
    query = update.callback_query
    directory = context.user_data.get('bals_reports', {}).get(query.message.message_id)
    if directory is None:
        await query.answer(SETTLE_STALE)
        return
    sender = directory.participant_by_telegram_id(str(update.effective_user.id))
    if sender is None:
        await query.answer(SETTLE_GONE)
        return
    toast = None
    try:
        parts = query.data.split(':')
        if len(parts) != 5 or not all(p.isascii() and p.isdigit() for p in parts[2:]):
            raise ValueError("Invalid payback button")
        trip_id, number, cents = map(int, parts[2:])
        if not all(0 < value <= 2**63 - 1 for value in (trip_id, number, cents)):
            raise ValueError("Invalid payback number")
        trip = _load_bals_trip(sender.id, trip_id)
        if trip is None:
            raise _SettlementUnavailable(BALS_NOT_MEMBER)
        entries, settlements = get_trip_split_entries(trip.id), get_trip_settlements(trip.id)
        if entries is None or settlements is None:
            raise _SettlementUnavailable(BALS_ERROR)
        families = dict(trip.families)
        payments = trip_payments(entries, families, settlements)
        if number > len(payments) or payments[number - 1][1].cents != cents:
            raise _SettlementUnavailable(SETTLE_STALE)
        currency, payment = payments[number - 1]
        member_ids = ((payment.from_unit[1],) if payment.from_unit[0] == 'person' else
                      tuple(pid for pid in trip.member_ids if unit_of(pid, families) == payment.from_unit))
        payer = (payment.from_unit[1] if payment.from_unit[0] == 'person' else
                 sender.id if sender.id in member_ids else None)
        card = dict(trip_id=trip.id, trip_name=trip.name, from_id=payer,
                    # Keep the names the user saw; saving must never reload the directory.
                    directory=directory,
                    line_parts=line_parts(entries, families, settlements, currency,
                                          payment.from_unit, payment.to_unit[1]),
                    to_id=payment.to_unit[1], clears_cents=cents, clears_currency=currency,
                    paid_cents=cents, paid_currency=currency, suggested_cents=cents,
                    debtor_member_ids=member_ids, for_id=None, idempotency_key=str(uuid.uuid4()),
                    date=get_sgt_now().date(), entered_by_participant_id=sender.id,
                    family=payment.from_unit[1] if payment.from_unit[0] == 'family' else None,
                    names={p.id: p.display_name for p in directory.participants},
                    # Who can hand the money over: anyone on the trip but the receiver, even
                    # the receiver's own relative (ADR-0051: debts are per person, so a
                    # family isn't one wallet). Someone outside the owing unit pays on its
                    # behalf. Owing unit first, then everyone else by name.
                    people=tuple(sorted((p for p in directory.participants if p.id in trip.member_ids
                                         and p.id != payment.to_unit[1]),
                                        key=lambda p: (p.id not in member_ids, p.display_name.lower(), p.id))),
                    # Currencies already on the trip are always accepted; any real ISO code is
                    # accepted too (app/services/currencies.py), so no extra database read.
                    currencies={e['currency'] for e in entries} | {s['currency'] for s in settlements}
                    | {s['paid_currency'] for s in settlements})
    except _SettlementUnavailable as e:
        toast = str(e)
    except Exception as e:
        print(f"Could not prepare payback: {e}")
        toast = BALS_ERROR
    # Answer outside the read's try block: a failed Telegram answer must never be retried.
    await query.answer(toast)
    if toast:
        return
    message = await query.message.reply_text(settlement_card_text(card),
                                            reply_markup=_settle_keyboard(card) if payer else _settle_people(card))
    _bounded_put(context.user_data.setdefault('pending_settlements', {}), message.message_id, card)


async def handle_settle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle payback cards separately from expenses, with one answer and one lock per tap."""
    query = update.callback_query
    if query.data.startswith('settle:new:'):
        await _new_settlement(update, context)
        return
    if query.data == 'settle:saving':
        await query.answer("⏳ Saving...")
        return
    async with _card_lock(context, query.message.message_id):
        await _settle_tap(query, context)


async def _settle_tap(query, context):
    """Change or save under the card lock, clearing prompts and refreshing saved balances."""
    message_id = query.message.message_id
    outcome = _card_outcome(context, message_id)
    if outcome:
        await query.answer("✅ Payback already saved." if outcome == 'saved' else "❌ Cancelled.")
        return
    pending = context.user_data.setdefault('pending_settlements', {})
    card = pending.get(message_id)
    if card is None:
        await query.answer(SETTLE_GONE)
        return
    action = query.data.removeprefix('settle:')
    if action == 'confirm':
        if card['from_id'] not in {p.id for p in card['people']}:
            await query.answer("Choose who paid first.")
            return
        await query.answer()
        # Keep state until the progress redraw succeeds. A Telegram failure must not lose it.
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⏳ Saving...", callback_data="settle:saving")]]))
        pending.pop(message_id)
        # Whose debt a shortened line clears first: the giver's own, or, when someone outside
        # the owing family pays, the member the card names (for_id: the lowest id). A line
        # with no shortcut clears that same member first (_pay_back), so both kinds agree.
        first = card['from_id'] if card['for_id'] is None else card['for_id']
        try:
            add_settlement(from_id=card['from_id'], to_id=card['to_id'], for_id=card['for_id'],
                           clears_amount=format_cents(card['clears_cents']), clears_currency=card['clears_currency'],
                           paid_amount=format_cents(card['paid_cents']), paid_currency=card['paid_currency'],
                           trip_id=card['trip_id'], date=card['date'], source="telegram_bot",
                           entered_by_participant_id=card['entered_by_participant_id'],
                           idempotency_key=card['idempotency_key'], note=payback_default_note(card),
                           **({'bills': payment_rows(card['line_parts'], first,
                                                     card['clears_cents'])}
                              if card.get('line_parts') else {}))
        except Exception as e:
            print(f"Could not save payback: {e}")
            _bounded_put(pending, message_id, card)
            await query.edit_message_text(settlement_card_text(card) + "\nCouldn't save. Try Confirm again.",
                                          reply_markup=_settle_keyboard(card))
            return
        # False from the writer means this key was saved before: it is still a success.
        _mark_card_resolved(context, message_id, 'saved')
        await _clear_settlement_prompt(context, query.message.chat_id, message_id)
        await query.edit_message_text(settlement_card_text(card) + "\n✅ Payback saved.", reply_markup=None)
        await _send_fresh_balances(context, query.message.chat_id, card['entered_by_participant_id'],
                                   card['trip_id'], card['directory'], card['clears_currency'])
        await _refresh_saved_trip_sheet(card['trip_id'])
        return
    if action == 'cancel':
        await query.answer()
        await query.edit_message_text(settlement_card_text(card) + "\n❌ Cancelled.", reply_markup=None)
        pending.pop(message_id)
        _mark_card_resolved(context, message_id, 'cancelled')
        await _clear_settlement_prompt(context, query.message.chat_id, message_id)
        return
    if action in ('clears', 'paid'):
        await query.answer()
        await _settlement_prompt(query.message, context, message_id, action)
        return
    changed = dict(card)
    if action.startswith('person:'):
        value = action.removeprefix('person:')
        if not value.isascii() or not value.isdigit() or int(value) not in {p.id for p in card['people']}:
            await query.answer("Choose someone from this trip.")
            return
        changed['from_id'] = int(value)
        changed['for_id'] = (None if int(value) in card['debtor_member_ids']
                             else min(card['debtor_member_ids']))
    elif action not in ('who', 'back'):
        await query.answer(SETTLE_GONE)
        return
    await query.answer()
    keyboard = _settle_people(changed) if action == 'who' else _settle_keyboard(changed)
    await query.edit_message_text(settlement_card_text(changed), reply_markup=keyboard)
    pending[message_id] = changed  # Only remember what Telegram actually showed.


async def _settlement_reply(update, context) -> bool:
    """Delete typed replies under the card lock and keep errors in replacement prompts.

    The reply is deleted only after the bot's answer to it was sent (_reply_deleted_last).
    """
    message = update.message
    replied = message.reply_to_message
    link = context.user_data.get('settlement_prompts', {}).get(replied.message_id) if replied else None
    if link is None:
        # Prompt links are bounded and disappear on restart. Their bot-authored wording
        # still tells us this is a payback reply, never a fresh expense to extract.
        if (replied and isinstance(replied.text, str)
                and replied.text.startswith(tuple(SETTLE_PROMPTS.values())) and replied.from_user
                and replied.from_user.id == context.bot.id):
            # Answer first, then delete: see _reply_deleted_last.
            await message.reply_text(SETTLE_GONE)
            await _delete_quietly(context.bot, message.chat_id, message.message_id)
            await _delete_quietly(context.bot, message.chat_id, replied.message_id)
            return True
        return False
    card_id, side = link
    async with _card_lock(context, card_id), _reply_deleted_last(context, message):
        card = context.user_data.get('pending_settlements', {}).get(card_id)
        if card is None or _card_outcome(context, card_id):
            await _clear_settlement_prompt(context, message.chat_id, card_id, replied.message_id)
            await message.reply_text(SETTLE_GONE)
            return True
        try:
            changed = change_settlement_amount(card, message.text, side)
        except ValueError as e:
            await _settlement_prompt(message, context, card_id, side, str(e), replied.message_id)
            return True
        try:
            await context.bot.edit_message_text(chat_id=message.chat_id, message_id=card_id,
                                                text=settlement_card_text(changed), reply_markup=_settle_keyboard(changed))
        except Exception as exc:
            print(f'Payback amount redraw failed: {exc}')
            await _settlement_prompt(message, context, card_id, side,
                                     "Couldn't change the card. Reply again.", replied.message_id)
            return True
        # Commit only what Telegram showed, then remove the now-used prompt.
        context.user_data['pending_settlements'][card_id] = changed
        await _clear_settlement_prompt(context, message.chat_id, card_id, replied.message_id)
    return True


async def _clear_settlement_prompt(context, chat_id, card_id, replied_id=None):
    """Drop both prompt links and best-effort delete current and stale reply targets."""
    current = context.user_data.get('settlement_prompt_of_card', {}).pop(card_id, None)
    for previous in dict.fromkeys((current, replied_id)):
        if previous is not None:
            context.user_data.get('settlement_prompts', {}).pop(previous, None)
            await _delete_quietly(context.bot, chat_id, previous)


async def _settlement_prompt(message, context, card_id, side, error=None, replied_id=None):
    """Send one prompt with a fixed first line, replacing the old prompt only on success."""
    text = SETTLE_PROMPTS[side] + ('\n⚠️ ' + error if error else '')
    sent = await message.reply_text(text, reply_markup=ForceReply(selective=True))
    # Send first so a Telegram failure leaves the old prompt usable.
    await _clear_settlement_prompt(context, message.chat_id, card_id, replied_id)
    _bounded_put(context.user_data.setdefault('settlement_prompts', {}), sent.message_id, (card_id, side))
    _bounded_put(context.user_data.setdefault('settlement_prompt_of_card', {}), card_id, sent.message_id)


def _redraw_lock(context, chat_id, trip_id) -> asyncio.Lock:
    """The lock for one chat's and one trip's fresh /trip_bals after a saved payback.

    Why it is needed: two payback cards have two card locks, so their Confirms can run at the
    same time. Without this lock the first report could read the balances before the second
    payback was saved and still be sent after the second report, so the report at the bottom
    of the chat would miss a payback. One at a time, the report sent last reads after both
    saves, so the newest report is always current.

    Kept in user_data and bounded like the card locks. It is taken inside a card lock and never
    takes one itself, so the two can't wait on each other.
    """
    locks = context.user_data.setdefault('bals_redraw_locks', {})
    key = (chat_id, trip_id)
    if key not in locks:
        _bounded_put(locks, key, asyncio.Lock())
    return locks[key]


async def _send_fresh_balances(context, chat_id, sender_id, trip_id, directory, currency):
    """Send a new /trip_bals below a payback card that was just saved on a trip (#151).

    Used by both payback cards: "Mark paid" and the typed settle card. Older reports stay where
    they are, as a record: nothing is deleted. Their buttons still work, and a stale "Mark paid"
    tap is rechecked against the current numbers when it opens a card.

    Built from the directory snapshot kept on the card, never a reload, so the names match the
    card. Opens the currency just cleared, so the updated line is easy to find. Runs one
    at a time per chat and trip (_redraw_lock), from the read to the send.
    """
    async with _redraw_lock(context, chat_id, trip_id):
        try:
            # The membership-checked read: someone who has left the trip gets no report.
            trip = _load_bals_trip(sender_id, trip_id)
            if trip is None:
                return
            text, keyboard = _bals_screen(trip, directory, get_sgt_now().date(), currency)
            if BALS_ERROR in text.splitlines():
                # The bills or paybacks couldn't be read just now. Send nothing rather than an
                # error right under "Payback saved", which would read as if the save failed;
                # /trip_bals can be asked for again.
                print('Could not read balances after a payback; no new report sent.')
                return
            sent = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
        except Exception as exc:
            # The payback is already saved; a report failure must not disguise that success.
            print(f'Could not send fresh balances: {exc}')
            return
        # Registered like a /trip_bals reply, so its "Mark paid" buttons open cards from the
        # same snapshot.
        _bounded_put(context.user_data.setdefault('bals_reports', {}), sent.message_id, directory)


@contextlib.asynccontextmanager
async def _reply_deleted_last(context, message):
    """Delete a user's typed reply once the bot has answered it, however the handler ends.

    Why last: every answer to a typed reply (a new prompt, an error, "no longer available") is
    sent with message.reply_text, and in a group chat python-telegram-bot quotes the message it
    answers. Telegram refuses to quote a message that is already deleted, so deleting the reply
    first makes the answer fail there. In a private chat the order makes no difference.
    Entered inside the card lock, so the delete still lands before the next tap or reply runs.
    """
    try:
        yield
    finally:
        await _delete_quietly(context.bot, message.chat_id, message.message_id)


async def _delete_quietly(bot, chat_id, message_id):
    """Best-effort chat cleanup must never undo a redraw or interrupt a save."""
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        print(f'❌ Message delete failed: {e}')


async def _clear_split_prompt(context, chat_id, card_id, replied_id=None):
    """Forget the current prompt and any stale reply target after the card is gone."""
    prompt_id = context.user_data.get('split_prompt_of_card', {}).pop(card_id, None)
    for previous in dict.fromkeys((prompt_id, replied_id)):
        if previous is not None:
            context.user_data.get('split_prompts', {}).pop(previous, None)
            await _delete_quietly(context.bot, chat_id, previous)


async def _replace_split_prompt(context, chat_id, card_id, old_id, prompt_id):
    """Replace links only after a fresh prompt was sent successfully.

    Also remove the current prompt if a delayed reply targeted an older one, so the chat
    still has one prompt for this card even when a previous deletion failed.
    """
    current = context.user_data.get('split_prompt_of_card', {}).get(card_id)
    for previous in dict.fromkeys((old_id, current)):
        if previous is not None:
            await _delete_quietly(context.bot, chat_id, previous)
            context.user_data.get('split_prompts', {}).pop(previous, None)
    _bounded_put(context.user_data.setdefault('split_prompts', {}), prompt_id, card_id)
    _bounded_put(context.user_data.setdefault('split_prompt_of_card', {}), card_id, prompt_id)


async def _split_amounts_reply(update, context, directory) -> bool:
    """Consume split replies before extraction, using the card's participant snapshot.

    Authorization has already loaded directory; it must not replace the sender or people on
    the card. Hold the same lock as Confirm until the redraw and state update finish.
    """
    message = update.message
    replied = message.reply_to_message
    card_id = context.user_data.get('split_prompts', {}).get(replied.message_id) if replied else None
    if card_id is None:
        if (replied and replied.from_user and replied.from_user.id == context.bot.id
                and (replied.text or '').startswith(AMOUNTS_PROMPT_LINE + '\n')):
            # Answer first, then delete: see _reply_deleted_last.
            await message.reply_text('⚠️ This prompt has expired. Send the entry again.')
            await _delete_quietly(context.bot, message.chat_id, message.message_id)
            await _delete_quietly(context.bot, message.chat_id, replied.message_id)
            return True
        return False
    async with _card_lock(context, card_id), _reply_deleted_last(context, message):
        pending = context.user_data.get('pending_cards', {}).get(card_id)
        if _card_outcome(context, card_id) or pending is None:
            await message.reply_text("⚠️ This entry can't be changed now.")
            await _clear_split_prompt(context, message.chat_id, card_id, replied.message_id)
            return True
        error = None
        try:
            changed = parse_split_amounts(pending, message.text, pending['entered_by_participant_id'])
        except ValueError as e:
            error = str(e)
        else:
            try:
                await context.bot.edit_message_text(
                    chat_id=message.chat_id, message_id=card_id, text=_pending_card_text(changed),
                    reply_markup=_keyboard_for(changed), parse_mode='Markdown')
            except Exception as e:
                print(f'❌ Split amounts redraw failed: {e}')
                error = "⚠️ Couldn't change the split, try again."
            else:
                # Do not resurrect a card that was removed while Telegram was answering.
                if context.user_data.get('pending_cards', {}).get(card_id) is not pending:
                    await message.reply_text("⚠️ This entry can't be changed now.")
                    await _clear_split_prompt(context, message.chat_id, card_id, replied.message_id)
                    return True
                pending.update(changed)
        text, placeholder = amounts_prompt(pending, error)
        try:
            prompt = await message.reply_text(text, reply_markup=ForceReply(
                selective=True, input_field_placeholder=placeholder))
        except Exception as e:
            # The card is authoritative even if Telegram cannot send its next prompt.
            print(f'❌ Split amounts prompt failed: {e}')
        else:
            await _replace_split_prompt(context, message.chat_id, card_id,
                                        replied.message_id, prompt.message_id)
    return True


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Passes a plain text message through the same pipeline a voice note uses."""
    # A tap on the button menu arrives as its label (#163). It runs the command and stops: no
    # model call, and never read as an expense. Checked before authorize() because the command
    # authorizes itself (one directory load, fail closed, the same as typing the command), and
    # before the prompt replies, so a tap while an amount prompt is open still opens the screen;
    # the prompt stays open for its reply.
    command = _menu_command(update.message.text)
    if command is not None:
        await command(update, context)
        return
    directory = await authorize(update)
    if directory is None:
        return
    replied = update.message.reply_to_message
    # Recognize the bot's exact first line, not remembered prompt ids: a restart
    # must never turn a forgotten settle reply into an extracted expense.
    first_line = (replied.text or '').split('\n', 1)[0] if replied else None
    prefixes = {SETTLE_PROMPT: 'Settle ', FAMILY_SETTLE_PROMPT: 'Family settle '}
    if (replied and replied.from_user and replied.from_user.id == context.bot.id
            and first_line in prefixes):
        # The prompt decides the kind: a reply that starts with its own "settle" or "family
        # settle" has that word dropped first, so "Settle Carol paid 381" sent to the 👪 prompt
        # is still a family settle (the card's toggle can switch it back).
        body = re.sub(r"^\s*(?:family\s+)?settle\b[\s,.:;!?—-]*", "", update.message.text, flags=re.I)
        text = prefixes[first_line] + body
        status_msg = await update.message.reply_text('Reading settle...')
        await process_expense_text(update, context, text, status_msg, directory)
        try:
            await replied.delete()
        except Exception as exc:
            print(f'Could not delete settle menu prompt: {exc}')
        return
    if await _sgd_rate_reply(update, context, directory):
        return
    # Authorize first (every message, fail closed), then check whether this text is a reply
    # to a payback amount prompt. Such a reply is never an expense, so it stops here.
    if await _settle_up_reply(update, context):
        return
    if await _settlement_reply(update, context):
        return
    if await _split_amounts_reply(update, context, directory):
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


async def handle_card_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open, close or pick from the account menu under the same lock as Confirm/Cancel.

    Every tap uses the pending snapshot only and answers exactly once, after its work.
    """
    query = update.callback_query
    message_id = query.message.message_id
    async with _card_lock(context, message_id):
        await _account_tap(query, context, message_id)


async def _account_tap(query, context: ContextTypes.DEFAULT_TYPE, message_id: int):
    """Apply one card-menu tap while holding the card lock; publish state after redraw."""
    # Resolved cards must retain their receipt even when an old menu callback arrives.
    outcome = _card_outcome(context, message_id)
    if outcome == "saved":
        await query.answer("✅ Already saved to the ledger.")
        return
    if outcome == "cancelled":
        await query.answer("❌ This entry was cancelled.")
        return
    pending = context.user_data.get('pending_cards', {}).get(message_id)
    if pending is None or not pending.get('payer_menu'):
        await query.answer("⚠️ This entry can't be changed now.")
        return

    if query.data in ("card:menu", "card:back"):
        keyboard = _card_keyboard(pending) if query.data == "card:menu" else _keyboard_for(pending)
        try:
            await query.edit_message_reply_markup(reply_markup=keyboard)
        except Exception as e:
            print(f"❌ Card menu redraw failed: {e}")
            await query.answer("⚠️ Couldn't update the buttons, try again.")
            return
        await query.answer()
        return

    account = next((a for a in _card_accounts(pending) if query.data == f"card:set:{a.id}"), None)
    if account is None:
        await query.answer("⚠️ This button is no longer supported. Please send the entry again.")
        return
    # The marked account just closes the menu: keep even the original base and warnings.
    changed = (dict(pending) if account.id == pending.get('account_id') else
               change_account(pending, pending['payer_menu']['directory'], account))
    try:
        await query.edit_message_text(_pending_card_text(changed), reply_markup=_keyboard_for(changed),
                                      parse_mode="Markdown")
    except Exception as e:
        print(f"❌ Change-card redraw failed: {e}")
        await query.answer("⚠️ Couldn't change card, try again.")
        return
    # A failed redraw or a replaced pending entry must never publish an unseen payment choice.
    if context.user_data.get('pending_cards', {}).get(message_id) is not pending:
        await query.answer("⏳ This entry is already being saved or was cancelled.")
        return
    pending.update(changed)
    await query.answer(f"💳 {account.name}")


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
    if query.data == 'split:amounts':
        if len(pending.get('split') or ()) < 2:
            await query.answer('⚠️ Tick at least two people first.')
            return
        text, placeholder = amounts_prompt(pending)
        try:
            prompt = await query.message.reply_text(text, reply_markup=ForceReply(
                selective=True, input_field_placeholder=placeholder))
        except Exception as e:
            print(f'❌ Split amounts prompt failed: {e}')
            await query.answer("⚠️ Couldn't send the prompt, try again.")
            return
        await _replace_split_prompt(context, query.message.chat_id, message_id, None, prompt.message_id)
        # Answer once and last: only promise a reply prompt after Telegram accepted it.
        await query.answer("Reply with a name and amount")
        return
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
    if action in ('p', 'f', 'none'):  # Only these change who is ticked.
        await _refresh_split_prompt(context, query.message.chat_id, message_id, pending)
    await query.answer()


async def _refresh_split_prompt(context, chat_id, card_id, pending):
    """Keep an open amounts prompt in step with the card after a tick change.

    Telegram refuses to edit a message sent with ForceReply ("Message can't be edited", seen
    on the test bot 2026-11-18), so a fresh prompt is sent and the old one deleted, exactly as
    after a reply. With fewer than two ticked there is nothing to set, so the prompt just goes.
    Best effort: a failed send is only printed, because the card already shows the truth.
    """
    prompt_id = context.user_data.get('split_prompt_of_card', {}).get(card_id)
    if prompt_id is None:
        return
    if len(pending.get('split') or ()) < 2:
        await _clear_split_prompt(context, chat_id, card_id)
        return
    text, placeholder = amounts_prompt(pending)
    try:
        prompt = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=ForceReply(
            selective=True, input_field_placeholder=placeholder))
    except Exception as e:
        print(f"❌ Split amounts prompt refresh failed: {e}")
        return
    await _replace_split_prompt(context, chat_id, card_id, prompt_id, prompt.message_id)


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
            await _clear_split_prompt(context, query.message.chat_id, message_id)
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
            if _owed_split(transaction_to_save):
                await _refresh_saved_trip_sheet(transaction_to_save.get('trip_id'))
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
        await _clear_split_prompt(context, query.message.chat_id, message_id)
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
    app.add_handler(CallbackQueryHandler(handle_settle_button, pattern=r"^settle:"))
    app.add_handler(CallbackQueryHandler(handle_sgd_button, pattern=r"^sgd:"))
    app.add_handler(CallbackQueryHandler(handle_settle_up_button, pattern=r"^su:"))
    app.add_handler(CallbackQueryHandler(handle_payer_button, pattern=r"^payer:"))
    app.add_handler(CallbackQueryHandler(handle_card_button, pattern=r"^card:"))
    app.add_handler(CallbackQueryHandler(handle_split_button, pattern=r"^split:"))
    app.add_handler(CallbackQueryHandler(handle_help_button, pattern=r"^help:"))
    app.add_handler(CallbackQueryHandler(handle_recent_button, pattern=r"^recent:"))
    app.add_handler(CallbackQueryHandler(handle_category_button, pattern=r"^cat:"))
    app.add_handler(CallbackQueryHandler(handle_button_click))

    return app
