# CLAUDE.md

Rules for working in this repo that the code can't tell you. Read the code for how things work;
read this for what must not be broken. The ADRs in `docs/decisions/` (index:
`docs/decisions/README.md`) say *why* each rule exists — read the relevant one before re-opening a
settled choice, and add a new record rather than editing an accepted one. `docs/SCHEMA.md` is the
canonical schema reference — read it before changing table columns.

## Start here

`ROADMAP.md` "Current status" says what phase we're in and what's next. Read it first, and update
it before ending a session that made progress. Don't start implementation work unless the user asks
for it in the current conversation, even if the roadmap lists it as next.

## Response style

Keep responses concise: lead with the answer, short paragraphs, plain prose over heavy formatting.
Expand detail only when the task genuinely needs it.

## What this is

A household expense ledger in Neon Postgres, fed by a Telegram bot (voice or text → Whisper →
GPT-4o-mini extraction → confirm card → save), a small FastAPI REST API (`app/main.py`), and a CLI.

## Keeping the docs current

This repo is public. No personal details, real names, real account names or job-search content in
any committed file; real participant and account rows live only in gitignored files.

Every build, feature or fix includes a check of whether `README.md`, `ROADMAP.md` and this file
need updating. `README.md` only ever describes what has shipped; forward-looking language belongs in
`ROADMAP.md` and moves into `README.md` once it ships, never before. Where the local-only
`doc-checker` subagent exists (`.claude/agents/doc-checker.md`, gitignored), run it before
committing doc changes or when picking work back up after a gap; otherwise check by hand.

## Commands and environment

- The environment is the micromamba env `ledger-env` (`micromamba run -n ledger-env <cmd>`).
- Run everything from the repo root: imports use the `app.` prefix with no `__init__.py` files.
- Gate before merge (CI requires all three, `main` is protected by a ruleset —
  [ADR-0021](docs/decisions/0021-rulesets-over-classic-branch-protection.md)): `pytest`,
  `ruff check .`, and the import smoke check in `.github/workflows/ci.yml`.
- **`alembic upgrade head` targets production.** `alembic/env.py` reads `DATABASE_URL` from `.env`,
  never `.env.local`. To migrate a test branch, set `DATABASE_URL` in the shell and check the host
  `alembic current` prints. See `docs/LOCAL_TESTING.md`.
- **Never poll the production bot token** — polling deletes its registered webhook. Local runs use
  `python -m app.bot_local`, which loads `.env.local` (a separate test bot and a Neon branch) and
  refuses the production token ([ADR-0019](docs/decisions/0019-separate-bot-token-for-local-testing.md)).
- `tests/` never touches the network or a database: an autouse fixture in `tests/conftest.py`
  makes any connection fail, because locally `.env` is production. Stub queries and Telegram
  objects (see `tests/test_button_callback.py`). Test-only dependencies go in
  `requirements-dev.txt`, never `requirements.txt`.
- **OpenAI calls need the user's explicit approval, every run.** That covers
  `scripts.check_extraction`, ad-hoc model calls and anything else that hits the API. State
  the call count first, and ask again before the next run: one yes covers one run, never the
  next step of a plan. Workers never call it. Production shares the same prepaid credit, and
  test runs once drained it so the live bot failed with a 429.
- After any change to the prompt or schema descriptions in `app/services/extraction.py`,
  `python -m scripts.check_extraction` (real model, no database) must be run, with approval
  (see above), and any real message the model misread added to its list
  ([ADR-0031](docs/decisions/0031-last-week-from-message-text.md)). `--only "<text>"` reruns just
  the matching messages; the script prints its call count first. `--margins` lists every
  choice the model made under 95% likelihood, at no extra calls: at temperature 0 a pass at
  51% looks like one at 99%, and only the first flips on the next call.
- The prompt holds only what Python can't read from the words
  ([ADR-0049](docs/decisions/0049-prompt-holds-only-what-python-cant-read.md)). Class docstrings
  on the schema models are sent to the model, so notes go in `#` comments; each rule is written
  once, in its field; `tests/test_extraction.py` caps the size. Raise the cap only with a run.
  The call runs at temperature 0, so `check_extraction` defaults to one run per message: add
  different messages rather than more runs.
- `scripts/migrate_to_postgres.py` can't run again but is kept on purpose as a reference. Don't
  delete it.

Env vars (`.env.example` shows the format):
- `DATABASE_URL` must use `postgresql+psycopg://` — psycopg v3; a plain `postgresql://` makes
  SQLAlchemy look for the uninstalled psycopg2 ([ADR-0006](docs/decisions/0006-psycopg3-url-scheme.md)).
- `WEBHOOK_SECRET_TOKEN` is required whenever `WEBHOOK_URL` is set. `/webhook` rejects a delivery
  without it *before* parsing the body or recording the `update_id`; that is what makes the sender
  ID `authorize()` trusts trustworthy ([ADR-0025](docs/decisions/0025-webhook-secret-token.md)).
- Who may use the bot, and their cards, are rows in `participants` and `accounts`, never env vars.
  The old `ALLOWED_TG_IDS`, `ACCOUNT_OWNERS` and `PRIMARY_ACCOUNT_OWNER` are gone; never bring a
  second copy of that list back.

## Bot rules

- `app/bot_core.py` holds all logic; `bot_local.py` (polling) and `bot_webhook.py` (production,
  Cloud Run) are transports only ([ADR-0003](docs/decisions/0003-split-bot-transport-from-logic.md)).
- **Put new extraction or inference logic in `process_expense_text`**, never in a handler —
  anything in a handler only works for that one input type.
- The model may choose only from `directory.account_names()` (closed accounts included, so a refund
  on a cancelled card can still be named). Never add a second source for that
  list: a separate, never-set `ALLOWED_ACCOUNTS` var once ran the prompt on a hardcoded fallback and
  attributed entries to the wrong owner. When the model picks a closed card whose
  whole name isn't in the raw message, `resolve_payment` uses an open card the message does name;
  with none named, the closed card stays, with its warning.
- `description` is the raw input, never an LLM summary
  ([ADR-0008](docs/decisions/0008-raw-transcript-as-description.md)). Whisper's `language="en"` is
  pinned ([ADR-0009](docs/decisions/0009-pin-whisper-language-en.md)). One expense per message is a
  product guardrail ([ADR-0010](docs/decisions/0010-one-expense-per-message.md)).
- The model never guesses an amount, and never calculates a date. It returns a `date_reference`;
  `app/services/dates.py` does the calendar maths in tested Python, counting from one SGT `today`.
  "Last week" is read from the message text, not the model
  ([ADR-0030](docs/decisions/0030-llm-reads-dates-python-resolves.md),
  [ADR-0031](docs/decisions/0031-last-week-from-message-text.md)). A year counts only if the
  message says it (`says_year`). The date warning never blocks a save and is never stored.
- `resolve_payment` never guesses: no open wallet or funding card for a YouTrip top-up → refuse the
  entry (`WalletNotSetUp`); an outside payer with no open "Paid by someone else" account → refuse
  it too (`OutsideAccountNotSetUp`).
- The payer is read from the message by `read_payer` in Python first, so "split with Carol"
  can't become "Carol paid". The model's `payer_name` is a second reader, used only when Python
  finds nobody, the message has a paying word (`mentions_paying`) and the name matches one known
  person other than the sender; the card then says
  "⚠️ Read as: … paid. Check." Python's reading always wins
  ([ADR-0040](docs/decisions/0040-model-as-second-reader-for-the-payer.md)). Candidates are the
  sender's trip members while a trip is on, otherwise the household; one word of a name is enough
  when no other candidate shares it (`match_person`).
  An owned account always decides the payer; "Paid by someone else"
  is reached only by the outside-payer rule, never named in the prompt's account list
  ([ADR-0037](docs/decisions/0037-paid-by-person-read-from-text.md)). Payer warnings are card
  lines only: never stored, never block a save. Every bot user is household (a database check).
- The "Change who paid" button (`handle_payer_button`) applies `change_payer`, which starts from
  the account the message named (`base_account_id`), never the account on the card now, so taps
  don't pile up. It reads the directory snapshot kept on the pending entry, never the database.
  No account fits → an alert, and nothing changes. No button on a YouTrip top-up. Build new
  people menus with `_people_keyboard`
  ([ADR-0038](docs/decisions/0038-paid-by-button-starts-from-the-message.md)). The menu offers
  the trip's members while the card is tagged to a trip, otherwise every participant, decided
  when it is drawn (`_payer_people`), and a tap is checked against that same list. Only the menu
  widens: `read_payer` and the second reader keep the trip or the household
  ([ADR-0041](docs/decisions/0041-split-menu-and-wider-people-menus.md)).
- The "Change card" button (`handle_card_button`, ADR-0057) follows the payer button's rules
  and applies `change_account`: an owned card decides the payer, an unowned one keeps a
  household payer (else the sender), and the tapped card becomes `base_account_id`, so a later
  payer tap starts from it. The menu (`_card_accounts`) is the open accounts in the snapshot,
  never "Paid by someone else", and a tap is checked against that same list.
- A split is read by the model and worked out by Python
  ([ADR-0039](docs/decisions/0039-splits-read-by-the-model-worked-out-by-python.md)). The model
  returns only a `split_reference` (who and how many), never a share. `resolve_split` matches
  the names and `share_out` hands out whole cents (both in `app/services/splits.py`). The message
  decides whether an entry is split, never trip mode: a split the model read is kept only if the text has a
  split word (`asks_for_split`), and "with"/"between" decide whether the sender shares
  (`sender_shares`), both read by Python. A "between" list that Python reads in full (every
  item a known person, family, trip label or "me", no known name after it) decides who shares,
  over the model's names and kind (`read_split_list`,
  [ADR-0055](docs/decisions/0055-between-list-read-by-python-first.md)): only when the model
  read a split, with no count, uneven or "except" words. "With" lists stay the model's. The
  model's "everyone" with no names and no "except" names, in a message that lists sharers
  but never says everyone, is a ⚠️ line, never the whole trip. With no split word, a cost paid
  "for" known people ("for Dave's ticket", "on my behalf") is theirs to owe: `read_for_people` reads it in Python,
  only after the split rules find nobody, so no split read today changes. A gift word, a
  relative ("Dave's mum") or a list with an unknown name records no split
  ([ADR-0054](docs/decisions/0054-paid-for-someone-means-they-owe.md)). Nobody is guessed: an unmatched name, or
  "everyone" with no trip on, is a ⚠️ card line. Split warnings are never stored and never block a
  save. Uneven shares read from the message are counts (nights, or any count per person;
  [ADR-0042](docs/decisions/0042-uneven-shares-by-count.md)): the model reads each count, a
  count is used only if its number is in the message, and a menu tap keeps the counts (one row
  builder for the message and every tap). Python, not the model, decides that an "except" entry
  with a count is a sharer at that count; "everyone" counts only when the text says it. A name
  that matches no person is tried as a trip family label (a person always wins). A name
  that still matches nothing ("Reds and Blues") is split on "and", "&" or ",", used only if
  every part matches. In a split by count, a name or label the model left out is added by
  `_add_unlisted` only with exactly one number after it in its own clause, never from a
  paying mention. A paying
  clause ("X paid ... for 6 nights") gives X no count unless it says "her/his/their N"; X still
  shares, at a count from the other words. The rest's count ("rest", "others", "everyone else")
  is read by Python from its own clause, beats the model's, and is skipped when that clause has
  two numbers or says "2 others" ("the rest of the airbnb" is not a rest word). It is read only
  beside a named person's count, so "dinner with the others 80" stays personal. With a trip on
  and no "between"/"split with" list, "the rest" makes the split the whole trip
  (`_rest_count`, `_reread`;
  [ADR-0045](docs/decisions/0045-family-labels-the-rest-and-stricter-wording.md)). A count read
  loosely gets `CHECK_COUNT_WARNING`, never silence:
  a payer's dropped count that wasn't in their paying clause and differs from the count they
  end up with, or a missing count filled from a number in another clause. Every `/help`
  example (`app/services/help_topics.py`) is also a case in `scripts/check_extraction.py`;
  change one only with the other. A sharer left without a count gets a warning and no split,
  never a wrong even one. A fixed amount or percentage, or uneven wording with no count read
  ("unevenly", "by nights", "60/40": read by `says_uneven`, never the model), keeps
  the names ticked evenly with `UNEVEN_EVEN_SPLIT_WARNING` pointing to "Set amounts" (ADR-0048);
  every count the model read is dropped then, never trusted. An even split set in the menu
  keeps that warning, reworded, until Confirm or amounts are set. Transfers and YouTrip top-ups are never split.
  An extra entry the model makes of a share ("Bob's split is 90") is dropped by
  `drop_share_entries` before the one-expense guard: only the number in a share phrase, same
  currency, less than the first entry. Never merge entries any other way, and never fix this
  with a prompt rule (it cost the nights readings; ADR-0048).
- The split menu (`handle_split_button`, ADR-0041) follows the payer button's rules: card lock,
  answer once and last, toast only on resolved cards, never load the directory, redraw from a
  copy and change the pending entry only after the redraw worked. State changes are pure
  functions in `app/services/splits.py` that return a copy. "Ticked" means "has a row in
  `split`". The list is chosen when drawn: trip families while tagged, otherwise everyone, and
  anyone ticked always stays listed. The first opening on a trip card with no split ticks every
  member; never again after that (`split_menu['opened']`). Callback data is an id or a group
  index, never a name.
- Exact split amounts are typed, never read from the message
  ([ADR-0048](docs/decisions/0048-exact-split-amounts-by-reply.md)). "Set amounts" (2+ ticked;
  on the card and first in the split menu) sends a ForceReply prompt, replaced on a tick change
  (Telegram can't edit a ForceReply message), linked to the card in `split_prompts`; the reply is caught in
  `handle_text` before extraction, inside the card's lock, and read by `parse_split_amounts`
  (no model): names only from the ticked people, amounts only through `dollars_to_cents`. A reply
  merges into `split_menu['amounts']` (set people only); unset people share what's left by
  `share_out`, by count if any, so the card always adds up. `amounts_misfit` is the only
  refusal: a misfit reply changes nothing; a tick change that misfits clears the amounts into
  `cleared_amounts` with a ⚠️ line. Each reply and the old prompt are deleted once read, the
  last prompt on Confirm or Cancel; a failed delete is only printed, never blocks a save. A
  typed reply (split, payback or settle) is deleted last, after any answer to it is sent
  (`_reply_deleted_last`): in a group chat the answer quotes it. The
  expense message is never deleted.
- The button menu under the text box (#163) is a persistent reply keyboard handed out by
  `/start`. A tap sends its label as text: `handle_text` matches the exact label
  (`_menu_command`) first, before `authorize()` and the prompt replies, and runs that command,
  which authorizes itself. Anything but an exact label is an expense message as before. A new
  label needs a handler in `_menu_command` (a test checks every label has one). `BOT_COMMANDS`
  is in the agreed order, most used on a trip first; change it only with the user.
- `/recent` (`handle_recent_button`, ADR-0050) is stateless: each button carries an entry id
  and a page offset, every tap reads the database fresh, and there is no pending entry or card
  lock. The delete and undo queries change only a live or only a deleted row, so a second
  overlapping tap changes nothing and gets a toast. Delete takes a second tap; Undo lives on the
  "Deleted" message until it is redrawn. Any household member may delete any entry. Plain
  text, no `parse_mode`. A failed read is an error, never "no entries".
- `/trip_bals` ([ADR-0044](docs/decisions/0044-trip-balances-from-splits.md)) reads bills and
  settlements. It shows one currency per screen
  ([ADR-0056](docs/decisions/0056-trip-bals-one-currency-per-screen.md)): a main screen of one
  line per family, with currency buttons, then "All payments" and "View paybacks" screens, all
  read fresh on every tap. A "Mark paid" button names its payment, and its callback keeps the
  payment's number in the whole `trip_payments` list, so payback cards and older reports work
  unchanged. "Paid on someone else's behalf" uses only what `_pay_back` really took off each debt
  (never the gross share, never money owed back to the giver), takes an offset row off the
  middle person, and never lists a giver's own family; it is read-only and
  never a debt. The "Mark paid" buttons open separate payback cards (ADR-0046), saved
  only on Confirm. Payback taps use the directory snapshot kept with the report or card,
  never reload it; new cards recheck trip membership and the suggested amount. Amount replies
  are parsed in Python before expense extraction, and use a separate pending-card map.
  Each reply is deleted inside the card lock, and its prompt once read; a refused amount's
  error goes into a replacement prompt, never a separate message (#149). After any saved
  payback on a trip, from a "Mark paid" card or a typed settle card, a new report is sent
  below the card from the directory snapshot kept on the card (`_send_fresh_balances`, #151).
  No report is ever deleted. A failed send or bills read is only printed, never touching the
  saved card. New reports for one chat and trip go out one at a time (`_redraw_lock`), so
  the last one is current.
  Debts are worked out per person first, even inside the payer's own family, and rolled up
  only at the end ([ADR-0051](docs/decisions/0051-debts-per-person-then-families.md)): a
  family's debts to someone outside it are one line, a debt inside a family stays person to
  person. Debts are simplified per person before the roll-up (`_simplify_debts`, #129): loops
  of person-to-person debts cancel, and a debt moves only onto a line that already exists (P
  owes C, U owes P and already owes C: U pays C, and P that much less). Never a new pair, and
  never a loop through a family, which would move money between relatives' own totals. Fewest
  payments are found by a bounded search (`SIMPLIFY_BUDGET`, sized by measurement; a bigger
  one made `/trip_bals` take seconds). Each visible shortcut gets a 💡 line, on the same screen
  as its payment. Never net two
  families' debts to different people any other way. Each person's total never changes. A payback pays down one
  line, the beneficiary's own debt first; excess is owed back to whoever handed over the money.
  When someone outside the owing family pays, the beneficiary is its lowest-id member, on a
  shortened line too (Confirm gives `payment_rows` the `for_id`, never the giver).
  Anyone on the trip but the receiver can pay any line on someone's behalf, recorded only at
  payback time; nobody owes the giver in `/trip_bals`. The paid side records money handed
  over, never spending totals, and may be any ISO 4217 currency (`app/services/currencies.py`).
  An overpayment gets a warning and a Confirm overpayment button, never a refusal.
  A "Mark paid" on a shortened line also saves offset rows (#145, ADR-0052): the chain is
  worked out again from the bills when the card opens (`line_parts`), kept on the card, and
  trimmed to the amount on Confirm (`payment_rows`). Never store the simplification. A normal
  line, or a chain with no open share behind a step, saves no rows.
  Pure whole-cent maths in `app/services/balances.py` keeps currencies apart. It counts only entries tagged to the trip that have split rows. A failed read of bills or paybacks is shown as an error,
  never as "nobody owes anything". A split entry still with no payer after the fallback (account owner, else sender) is left out with a ⚠️ line,
  never guessed. A trip id from a button is loaded only through `member_trip`. The reply is
  plain text with no `parse_mode`.
- "💱 All in SGD" (`bals:sgd`, `sgd:` callbacks; #171, amendments to ADR-0051/0053/0056) is a
  view over `trip_payments`, never a second balance engine: `one_currency`
  (`app/services/one_currency.py`) converts each payment at the trip's stored rate and nets
  two families' opposite debts, largest line first, using every payment's cents exactly once.
  Then debts move only along lines that already exist (`_shortcut`, the #129 rule across
  currencies, with the middle person's family standing in for them), with the same 💡
  line: nobody is ever sent to someone they didn't already pay. Never a new pair.
  Rates live in `trip_fx_rates` (`app/services/trip_rates.py`): an ECB rate on the trip's last
  day (today while it's on) is looked up only on the tap that opens the screen and stored, and
  never overwrites a typed one. A typed rate comes from a ForceReply prompt recognised by its
  fixed first line (`💱 <CUR> rate · <trip> · #<id>`, so a restart can't send it to the model),
  read in `handle_text` after the settle-prompt block; the prompt's own words decide which way
  round it is; more than 25% from the ECB rate is refused. A ✅ card follows the settle card
  rules and saves one ordinary payback per part with `add_settlement` (paid in SGD, the stored
  rate, `needs_statement_check` false), each to its own payment's receiver; a netted part is
  saved from the line's receiver and a shortcut's pass-on piece from its middle person
  (`Part.giver`); a payment cut over several cards gets disjoint bill paths (`_sgd_paths`). Each
  part's key is made when the card opens, so a failed Confirm retries only what's missing, and
  the payer can't change after saving starts. The payer is never someone the card pays, and a
  card that hands money back to the owing family is paid only from inside it. Full lines only.
- A message whose first word is "settle" (or first words "family settle") is a payback, read
  by `app/services/settle.py` with no model
  ([ADR-0052](docs/decisions/0052-settle-up-by-typed-message.md),
  [ADR-0058](docs/decisions/0058-settle-wording-closest-bill-family-settle.md)). How it was
  paid is dropped only from the fixed `PAYMENT_METHODS` list, never a span naming a known
  person; anything else is still refused. With no full or amount-backed match, the bills sharing
  the most words are a "❓ Did you mean" question, never a silent pick. A family settle's
  default people come from `scope_families` in the card snapshot, never the database; on
  everyday bills it is the giver alone, with a ⚠️ line. A reply to the "🤝 Settle" or
  "👪 Family settle" menu prompt is recognised by the prompt's fixed first line (stateless, so
  a restart can't send it to the model), and the prompt decides the kind. The check is the
  first line of `process_expense_text`, so a settle message never reaches extraction. The
  message as read (`raw_text` on the card) is saved as the payback's `note` on Confirm, never
  a summary, like an expense's description (ADR-0008). A "Mark paid" payback, with nothing
  typed, gets `payback_default_note`: the giver, plus "(for <family>)" when it paid someone
  else's debt. The
  settle card (`app/services/settle_card.py`, callback prefix `su:`) follows the card rules:
  card lock, answer once, toast only on resolved cards, redraw from a copy. Every tap reruns
  `settle_up` from the reading and the snapshot kept on the card, never the database or the
  directory. Another currency is converted only through `app/services/fx.py`
  ([ADR-0053](docs/decisions/0053-other-currency-paybacks-at-a-reference-rate.md)), looked
  up once when the message is read, never on a tap; a failed lookup asks for a typed amount
  and never blocks. The amount prompt and each reply are deleted once read, like split
  prompts; a refused reply gets a replacement prompt with the error under its fixed first
  line, and a failed delete is only printed. Settle-screen money goes through `format_money` (built on `format_cents`).
- A trip with a `sheet_id` has its own Google Sheets workbook, one row per bill
  ([ADR-0059](docs/decisions/0059-live-sheet-per-trip.md)). `refresh_trip_sheet`
  (`app/services/trip_sheet.py`) rewrites its first tab after a confirmed split bill on the
  trip, a saved payback, and a `/recent` delete or undo that changed a row. It runs after the
  card says saved, awaited, never `create_task` (Cloud Run gives CPU only during a request);
  one write at a time per trip; it never raises, and a failure is only printed. Rows come from
  `open_bill_shares`, at bill level before netting. Values are RAW (a description starting
  with `=` stays text), written first and the rows below cleared after, so a failed call never
  blanks the sheet. The bot signs in as its Cloud Run identity (metadata server); locally only
  when `SHEETS_IMPERSONATE_SA` is set. The sheet is never read back. A new place that changes
  a trip's bills or paybacks needs the same call. Workbook ids never go in the repo.
- The duplicate check runs before the card is shown, never inside the insert, and a failed check
  gives a normal card — it must never block a save
  ([ADR-0027](docs/decisions/0027-duplicate-warning-before-the-card.md)).
- The sender's own trip is read fresh for every entry and copied onto the pending entry, so the "Not
  trip" toggle never reads the database. Unlike the directory, a failed trip read never blocks:
  the card shows untagged, with a ⚠️ line. Trip mode is per person (`participants.active_trip_id`),
  only for a trip they are a member of, enforced by the database. `/trip` is buttons only
  ([ADR-0036](docs/decisions/0036-trip-switch-per-person.md)); a failed banner pin never undoes
  a switch.
  An entry is tagged by when it is logged, never by its date
  ([ADR-0035](docs/decisions/0035-trips-tagged-at-log-time.md)).
- A pending entry lives in `context.user_data`, keyed by the card's `message_id`, and is written
  only on Confirm ([ADR-0023](docs/decisions/0023-in-memory-confirm-card-state.md)).
- In `handle_button_click`:
  - A callback query can be answered exactly once, so decide what to say before `answer()`.
  - On confirm, swap in the non-actionable "⏳ Adding to ledger..." button *before* the blocking
    save, or the button looks dead and gets tapped again.
  - A tap on a card already in `resolved_cards` gets a toast only. Never `edit_message_text` it
    ([#29](https://github.com/sujayjangam/unified-ledger-pipeline/issues/29)).
  - A failed save puts the entry back with its original `idempotency_key` and buttons.
  - A tap never loads the directory: what was confirmed is what is saved.
  - Every tap that reads or changes a pending entry runs inside that card's lock (`_card_lock`),
    so a Confirm can't land in the middle of a redraw. A new card button must take it too.
  - `handle_payer_button` follows the answer-once, toast-only-on-resolved-cards and
    never-load-the-directory rules. Like the trip toggle, it redraws from a copy and changes the
    pending entry only after the redraw worked.

## Participants and accounts

([ADR-0032](docs/decisions/0032-participants-and-accounts-in-postgres.md))
- `authorize()` loads the directory on **every message**, never cached, so a row edit takes effect
  on the next message.
- **Fail closed.** If the directory can't be loaded, nobody gets in. Never fall back to letting a
  message through.
- Ids are `GENERATED ALWAYS AS IDENTITY`: never insert or reuse one. A closed account keeps its
  name and id with `closed_on` set; at most one *open* account may have a given name (ignoring
  case).
- Real rows are never committed (this repo is public). `scripts/seed_directory.py` copies them from
  the gitignored `directory_seed.local.json`; a plain run is a dry run, `--write` commits. Point
  `DATABASE_URL` at a Neon branch first. Tests use the fictional `directory` fixture.
- The REST API and CLI accept only an **open** account (unknown or closed → 422 from the API, exit
  1 from the CLI): there is nobody to ask "are you sure?".

## Storage

- The engine (`app/database.py`) is created lazily, never at import time, so importing any module
  works without a `.env` — the CI import check depends on it.
- The database is a Pacific away from the bot, about 0.22 s per round trip, so round trips are
  what make a screen slow ([ADR-0060](docs/decisions/0060-reads-in-autocommit-one-connection-per-screen.md)).
  A block that only runs `SELECT`s and never commits uses `get_read_connection()` (autocommit:
  no `BEGIN`, no `ROLLBACK`). Anything that writes or stores a value stays on `get_connection()`.
  A screen's loads share one connection through a loader's `conn=` argument, and each loader
  keeps its own error handling. Keep `pool_pre_ping`, and never make the whole engine autocommit.
- **Money is integer cents.** In only through `dollars_to_cents`, out only through `format_cents`
  (both in `app/add_expense.py`). Never format a float amount yourself, and never split an amount
  by rounding each share — splits hand out whole cents
  ([ADR-0004](docs/decisions/0004-money-as-integer-cents.md),
  [ADR-0028](docs/decisions/0028-exact-cent-conversion-half-up.md)).
- `transactions` is a view of the live rows of `raw_transactions`
  ([ADR-0050](docs/decisions/0050-soft-delete-behind-a-view.md)). Every read, insert and join
  uses `transactions`; only `soft_delete_entry`, `restore_entry` and `get_trip_settlements` (for
  the payer of a payback row's bill, never an amount; ADR-0052) name `raw_transactions`. Never
  filter on `deleted_at` in a query: the view does it. A new column goes on `raw_transactions`,
  then into the view (`LIVE_COLUMNS` in migration 0010 lists the current set). A delete never
  removes a row; removing test rows for good is the hand-run query in `docs/LOCAL_TESTING.md`.
- Alembic owns the schema; no `CREATE TABLE` in application code. Revisions are hand-written
  ([ADR-0007](docs/decisions/0007-alembic-hand-written-migrations.md)).
- **Production is never migrated automatically**, and a merge to `main` auto-deploys. Run
  `alembic upgrade head` by hand *before* merging code that depends on a migration. Additive
  migrations are safe to apply first: no query uses `SELECT *` and every writer names its columns.
- `created_at` is the **write** time (Postgres default only); `date` is the **business** date.
  Never substitute one for the other: "within the last N minutes" uses `created_at`. An exact
  `00:00:00+08` on an old row means "time unknown"
  ([ADR-0024](docs/decisions/0024-created-at-write-time-column.md)).
- `paid_by_participant_id` is who paid, apart from who logged it and which account. NULL means
  "not recorded" (old, CLI and API rows) and still counts in totals; the period and category
  totals skip only a payer who is set and not household. A split entry the household paid
  counts in full: the totals are what was paid out, not the household's own share (ADR-0044).
- `entered_by_participant_id` stores the sender's id, never a name. NULL means "not recorded".
  Tests use fictional ids only ([ADR-0033](docs/decisions/0033-entered-by-participant-id.md)).
- `transactions` records the account and sender as ids only (`account_id`, required, and
  `entered_by_participant_id`). Names and owners are looked up from `accounts` and
  `participants` when shown. Migration 0005 dropped the old text columns (`account_desc`,
  `account_owner`, `entered_by`); never bring a stored name back.
- `transaction_splits` holds one row per person sharing an entry, in whole cents. No rows means
  "not split": the entry is the payer's alone, so a split whose only person is the payer is
  never shown or saved (`_owed_split`). The shares must add up to the entry's amount;
  `add_expense` checks that and writes them in the same database transaction as the entry, only
  when the entry row was really inserted. Leftover cents go to the largest remainder, ties to the
  lowest participant id, never to "whoever paid".
- `settlement_bills` says which bill shares a payback clears (ADR-0052): one row per person's
  share of one bill, written with the payback in one database transaction, only when it was
  really inserted. A row clears only a share on a live bill in the payback's currency, and
  never more than is still open on that share; otherwise its cents are owed back to the giver,
  like an overpayment. A row on a bill the receiver didn't pay is an offset row: it counts only
  if the payback's offsets to each middle person are no more than that person's own rows, and
  every one fits, all or nothing (ADR-0052). A deleted bill someone other than the receiver
  paid breaks its chain the same way: it is read with its payer (ADR-0052). Old paybacks have no rows
  and clear a line's oldest bills first (`open_bill_shares`). No `ON DELETE` on its keys.
- `ON CONFLICT (idempotency_key) DO NOTHING` stops a double-tapped Confirm inserting twice
  ([ADR-0011](docs/decisions/0011-idempotency-key-over-update-id.md)). Duplicate webhook
  deliveries (`update_id`) are a different problem, deduped only in memory today.
- `get_sgt_now()` (`app/services/utils.py`) is the canonical "now" — never `datetime.now()`, or
  cutoffs drift to UTC on Cloud Run. Aggregates are grouped by currency; there is no FX conversion.
- Each ledger query catches its own errors. For `find_recent_duplicate` that is load-bearing:
  `None` means "show a normal card".
- Backups: `pg_dump` to GCS every 6 hours (`.github/workflows/backup.yml`); Neon's point-in-time
  recovery covers only 6 hours. Production is never restored into directly — see
  `docs/BACKUP_RESTORE.md`.

## Workers (Codex, agy)

Claude writes the brief, sorts the findings, runs the gate and commits. Workers do the heavy
reading and writing.

| Job | Worker |
|---|---|
| Deep code review, writing code from a brief | Codex |
| Doc cold reads, mechanical cross-checks, bulk reading and writing | agy, with its Claude models only: `claude-opus-4-6-thinking` for reviews and careful reads, `claude-sonnet-4-6` for mechanical work. Skip its Gemini models (2 of 4 claims were wrong in a 2026-11-12 trial). When agy is out of quota, Claude does the job. agy is extra capacity, not a second opinion; Codex is the second set of eyes. |

- Write the brief to `.work/briefs/<task>.md` (`.work/` is gitignored). Name the files and line
  ranges; don't paste their content. Say what the result must look like.
- Pipe the brief in on stdin (the repo path has a space, and PowerShell 5.1 mangles quoted
  arguments). Write output to `.work/out/`. Read only the worker's final message and
  `git diff --stat`.
  - Codex: `Get-Content .work\briefs\X.md -Raw | codex exec --sandbox read-only -o .work\out\X.txt -`
    (`--sandbox workspace-write` for code tasks). For a review of finished work,
    `codex exec review --uncommitted` or `codex exec review --base main`.
  - agy: `agy --model <model> --mode plan "--print=Read the file .work/briefs/X.md and do what it says. Use only file read and search tools, never shell commands." > .work\out\X.txt`
  - agy's `-p`/`--print` takes the prompt as its value and doesn't read stdin, so the prompt points
    at the brief file. Headless mode auto-denies shell tools; reviews don't need them.
  - agy only sees the files you name. Check its claims against code outside that scope before
    accepting them.
- After any worker writes code, Claude runs the gate (see "Commands and environment").
- Run workers in the background, and tell the user before starting anything longer than about a
  minute.
- Fences: workers only see this repo. No `--add-dir`, no `--dangerously-skip-permissions`, no
  content from other folders on this machine. Workers never read `.env`, `.env.local`,
  `directory_seed.local.json`, `claude-plan-*.md` or `Test PDF_CSV/`; never run `alembic`,
  `scripts/seed_directory.py`, the bot or anything else that opens a database connection or calls
  OpenAI; and never
  commit, push, deploy, or create issues or PRs.
- Delegate only when briefing is cheaper than doing it. Small fixes stay with Claude.

## GitHub issue conventions

- **Ask before filing.** Don't create a GitHub issue without the user asking for it in the current
  conversation — offer to file one, or note it as a candidate, but wait for a go-ahead.
- **Default assignee: `sujayjangam`, always** (`gh issue create --assignee sujayjangam ...`).
- The rest (parent issues, sub-issues, labels, milestones, superseding) lives in the
  `github-issues` skill, `.claude/skills/github-issues/SKILL.md` — read it before filing or
  reorganising issues.
