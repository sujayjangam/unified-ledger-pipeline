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
- **Never call the OpenAI API yourself** — no `scripts.check_extraction`, no ad-hoc model
  calls, no worker runs that hit it. Production shares the same prepaid credit, and test runs
  once drained it so the live bot failed with a 429. Give the user the command and the call
  count, and let them run it.
- After any change to the prompt or schema descriptions in `app/services/extraction.py`,
  `python -m scripts.check_extraction` (real model, no database) must be run by the user (see
  above), and any real message the model misread added to its list
  ([ADR-0031](docs/decisions/0031-last-week-from-message-text.md)). `--only "<text>"` reruns just
  the matching messages; the script prints its call count first.
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
  [ADR-0031](docs/decisions/0031-last-week-from-message-text.md)). The date warning never blocks a
  save and is never stored.
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
- A split is read by the model and worked out by Python
  ([ADR-0039](docs/decisions/0039-splits-read-by-the-model-worked-out-by-python.md)). The model
  returns only a `split_reference` (who and how many), never a share. `resolve_split` matches
  the names and `share_out` hands out whole cents (both in `app/services/splits.py`). The message
  decides whether an entry is split, never trip mode: a split is kept only if the text has a
  split word (`asks_for_split`), and "with"/"between" decide whether the sender shares
  (`sender_shares`), both read by Python. Nobody is guessed: an unmatched name, or
  "everyone" with no trip on, is a ⚠️ card line. Split warnings are never stored and never block a
  save. Uneven shares read from the message are counts (nights, or any count per person;
  [ADR-0042](docs/decisions/0042-uneven-shares-by-count.md)): the model reads each count, a
  count is used only if its number is in the message, and a menu tap keeps the counts (one row
  builder for the message and every tap). Python, not the model, decides that an "except" entry
  with a count is a sharer at that count; "everyone" counts only when the text says it. A name
  that matches no person is tried as a trip family label (a person always wins). A paying
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
  last prompt on Confirm or Cancel; a failed delete is only printed, never blocks a save. The
  expense message is never deleted.
- `/trip_bals` ([ADR-0044](docs/decisions/0044-trip-balances-from-splits.md)) reads bills and
  settlements. Its numbered "Mark paid" buttons open separate payback cards (ADR-0046), saved
  only on Confirm. Payback taps use the directory snapshot kept with the report or card,
  never reload it; new cards recheck trip membership and the suggested amount. Amount replies
  are parsed in Python before expense extraction, and use a separate pending-card map.
  Debt lines link each owing unit to the person who paid (ADR-0047); only opposite lines
  between the same two units cancel, largest first, then lowest creditor id. No pooling.
  A payback pays down one line; excess is owed back to whoever handed over the money.
  Anyone on the trip outside the receiver's own family can pay another unit's line as a gift,
  recorded only at payback time; nobody owes the giver. The paid side records money handed
  over, never spending totals, and may be any ISO 4217 currency (`app/services/currencies.py`).
  An overpayment gets a warning and a Confirm overpayment button, never a refusal.
  Pure whole-cent maths in `app/services/balances.py` keeps currencies apart. Shares inside
  the payer's unit cancel per bill (ADR-0044); outside shares owe the payer in person. It
  counts only entries tagged to the trip that have split rows. A failed read of bills or paybacks is shown as an error,
  never as "nobody owes anything". A split entry still with no payer after the fallback (account owner, else sender) is left out with a ⚠️ line,
  never guessed. A trip id from a button is loaded only through `member_trip`. The reply is
  plain text with no `parse_mode`.
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
- **Money is integer cents.** In only through `dollars_to_cents`, out only through `format_cents`
  (both in `app/add_expense.py`). Never format a float amount yourself, and never split an amount
  by rounding each share — splits hand out whole cents
  ([ADR-0004](docs/decisions/0004-money-as-integer-cents.md),
  [ADR-0028](docs/decisions/0028-exact-cent-conversion-half-up.md)).
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
