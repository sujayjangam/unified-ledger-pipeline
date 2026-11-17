"""What /help shows: a short intro, one button per topic, and example messages for each.

Plain text only (the bot sends it with no parse_mode), so nothing here needs escaping. Every
example is a wording the bot is known to read right: each one (ignoring capitals) is in
scripts/check_extraction.py, which runs it against the real model, and the split rules are
covered in tests/test_splits.py.
The names are the fictional ones those checks use; this repo is public, so no real name or
account ever goes here. Change an example only together with its check.

Nothing in this module touches the database, the model or Telegram.
"""

INTRO = (
    "How to write an entry\n\n"
    "One expense per message, with the amount in it. Type it or send a voice note. "
    "The bot shows a card first; nothing is saved until you tap Confirm.\n\n"
    "Pick a topic for examples:"
)

# key -> (button label, text). The key is the button's callback data after "help:", so it must
# stay short, and the order here is the order of the buttons.
TOPICS = {
    "split": ("➗ Splitting", (
        "Splitting a bill evenly\n\n"
        "• Dinner 80 split with Carol\n"
        "   → you and Carol\n"
        "• Lunch 60 split between Carol and Dave\n"
        "   → only Carol and Dave, not you\n"
        "• Petrol 90, split between Alice's and Bob's families\n"
        "• Drinks 120 split between everyone except Dave\n"
        "   → the whole trip but Dave (trip mode on)\n\n"
        "Say split, shared or between. \"Dinner with Carol 70\" is not a split.\n"
        "To change who shares, tap Split on the card."
    )),
    "nights": ("🛏 Uneven (nights)", (
        "Splitting by nights (or any count)\n\n"
        "• Hotel 600 split by nights, me 3 and Carol 2\n"
        "• Airbnb 2300, split by nights: Alice's family 5, Carol and Erin 4\n"
        "• Airbnb 900 split by nights. Reds 5 nights, rest all 6 nights.\n"
        "   → a family by its trip name, and everyone else at 6\n"
        "• Carol paid 600 for her 4 nights. Rest all stay 6 nights.\n\n"
        "Rules that keep counts from being lost:\n"
        "• Start with \"split by nights\".\n"
        "• Put each count right after its name or family.\n"
        "• \"The rest\" and family names need trip mode on.\n"
        "• Only one number in the sentence about the rest.\n"
        "• The payer's own nights: \"her 4 nights\", or name them again (\"Carol 4\").\n"
        "• With \"split between A, B and C\", give each of them a count.\n\n"
        "Fixed amounts and percentages aren't supported. A \"⚠️ Check …\" line means a count "
        "was read loosely: the split menu can't change counts, so send the entry again, "
        "worded as above."
    )),
    "payer": ("💳 Who paid", (
        "Who paid\n\n"
        "With no payer named, you paid, from your usual card.\n\n"
        "• Carol covered dinner 90\n"
        "• Lunch 45, Dave's treat\n"
        "• Dinner 200 paid by Erin, split with Carol and Dave\n"
        "• Groceries 55 Card A\n"
        "   → naming a card means its owner paid\n\n"
        "Wrong payer? Tap \"Change who paid\" on the card."
    )),
    "dates": ("📅 Dates", (
        "Dates\n\n"
        "With no date, it's today.\n\n"
        "• Lunch 15 yday\n"
        "• Grab 12.80 day before yesterday\n"
        "• Coffee 5 last tues\n"
        "• Coffee 5 tues last week\n"
        "• Taxi 18 on 12 Sep\n"
        "• Dinner 40 on 3/7\n"
        "   → day first: 3 July\n\n"
        "Check the date on the card before you tap Confirm."
    )),
}
