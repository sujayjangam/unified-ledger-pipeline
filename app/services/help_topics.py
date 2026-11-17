"""What /help shows: a short intro, one button per topic, and an example or two for each.

Plain text only (the bot sends it with no parse_mode), so nothing here needs escaping. Every
example is a wording the bot is known to read right: each one (ignoring capitals) is in
scripts/check_extraction.py, which runs it against the real model, and tests/test_help.py
fails if one isn't. The names are the fictional ones those checks use; this repo is public, so
no real name or account ever goes here. Keep it short: it is read on a phone.

Nothing in this module touches the database, the model or Telegram.
"""

INTRO = "One expense per message, typed or spoken. Nothing saves until you tap Confirm.\n\nWhat do you need help with?"

# key -> (button label, text). The key is the button's callback data after "help:", so it must
# stay short, and the order here is the order of the buttons.
TOPICS = {
    "split": ("➗ Splitting", (
        "Say \"split\" and who with:\n\n"
        "• Dinner 80 split with Carol\n"
        "• Lunch 60 split between Carol and Dave\n\n"
        "\"With\" includes you, \"between\" doesn't."
    )),
    "nights": ("🛏 By nights", (
        "Start with \"split by nights\", and put each number right after the name:\n\n"
        "• Airbnb 900 split by nights. Reds 5 nights, rest all 6 nights.\n\n"
        "Family names and \"rest\" need trip mode on. If you see ⚠️ Check, send it again more plainly."
    )),
    "payer": ("💳 Who paid", (
        "If you don't say, it's you.\n\n"
        "• Carol covered dinner 90\n\n"
        "Wrong? Tap \"Change who paid\" on the card."
    )),
    "dates": ("📅 Dates", (
        "No date means today.\n\n"
        "• Coffee 5 last tues\n"
        "• Taxi 18 on 12 Sep"
    )),
}
