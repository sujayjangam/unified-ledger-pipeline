# ADR-0036: Trip mode is a switch per person, picked from buttons, shown by a pinned banner

**Status:** Accepted. Supersedes the "one active trip for everyone" part of
[ADR-0035](0035-trips-tagged-at-log-time.md); its other decisions stand.
**Date:** 2026-11-14
**Code:** `alembic/versions/0006_add_trips.py`, `app/services/trips.py`, `app/bot_core.py::trip_command`, `app/bot_core.py::handle_trip_button`, `app/bot_core.py::delete_pin_notice`, `app/bot_core.py::process_expense_text`, `scripts/seed_directory.py`

## Context

ADR-0035 made the active trip one setting for everyone, switched with `/trip on <name>` and
`/trip off`. Testing it on the polling bot, before it shipped, showed two problems. The two people
who log won't always be on the trip at the same time, so a shared switch tags one person's
everyday spending to a trip they aren't on. Typing a trip name on a phone is slow and easy to get
wrong. The state of the switch was also only visible on each card, not when opening the chat.

Migration 0006 had not reached production, so it was rewritten in place rather than followed by
a 0007.

## Decision

- **Each participant has their own switch:** `participants.active_trip_id`. One column holds
  one value, so a person has at most one trip on. A composite foreign key to
  `trip_members (trip_id, participant_id)` means the database only lets a person switch on a trip
  they are a member of. `trips.active` and its partial unique index are gone.
- **An entry is tagged with the sender's trip**, still by when it is logged, not by its date
  (ADR-0035 unchanged there). The card, the "Not trip" toggle and the save are unchanged.
- **`/trip` takes no arguments.** It replies with buttons: the person's trips, newest start date
  first, three at a time with "Older ▸" / "◂ Newer" paging, each labelled with its dates, and
  "Turn off trip mode" when one is on. Telegram has no scrolling list inside a message; paging by
  editing the same message is the nearest equivalent. A tap works the same from a voice note
  user's point of view: nothing is typed.
- **Trips have start and end dates** (`trips.start_date`, `trips.end_date`, from the seed file).
  They sort the list and label the buttons. They do not tag entries.
- **While a person's trip is on, a banner is pinned in their chat with the bot.** Its message id
  is stored in `participants.trip_banner_message_id`. On switch off, or a change of trip, the old
  banner is deleted: a message saying trip mode is on is only clutter once it's off. Telegram lets
  a bot delete a message only within 48 hours, so an older banner is unpinned and edited to say
  the trip ended instead. The "pinned a message" notice Telegram adds is deleted as it arrives.
  A failed banner step never undoes the switch; the menu says the banner couldn't be updated.
- `/trip` is registered in the bot's command menu at startup, so it is a tap from the "/" button.

## Alternatives considered

- **Keep one shared switch** (ADR-0035). Rejected for the reason in Context.
- **A switch per person in memory** (`context.user_data`). Rejected: a restart or redeploy would
  silently turn it off. Postgres keeps it, as before.
- **Keep `/trip on <name>` alongside the buttons.** Rejected: two ways to do one thing, and the
  typed one is the one that fails.
- **Tag by date range.** Still rejected (ADR-0035): pre-trip bookings fall outside it. The dates
  may later drive a *suggestion* on the card ("Tag to Beach Trip?") when the switch is off, never an
  automatic tag.

## Consequences

- Two people on the same trip each switch it on. Forgetting is the main risk; the pinned banner
  and the card's trip line are the reminders, and a date-based suggestion is a candidate follow-up.
- Seeding a trip now needs its dates.
- Switching is a write to `participants`, a table that `authorize()` reads on every message. The
  write touches only the two new columns.
- The banner always lives in the person's private chat with the bot, whichever chat they tapped
  in: Telegram message ids only mean something inside one chat. If a person deletes the
  pinned banner by hand, the stored id goes stale; the next switch can't remove it, shows the
  "couldn't update the pinned banner" line in the menu, and the switch still works.
