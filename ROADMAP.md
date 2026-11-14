# Roadmap

This document is the single source of truth for where this project stands and where it's going.
Read "Current status" first when resuming work — don't re-derive it by reading the whole repo.
What has shipped is in git history, the closed issues and the ADRs, not here.

## About blurb (draft — move into README.md once Phase 1/2 ship)

> A cloud-native financial reconciliation pipeline that unifies voice-logged expenses and
> multi-currency bank statements into a single, verifiable ledger — combining deterministic
> rule-based matching with LLM-assisted extraction, backed by a hand-labeled precision/recall
> evaluation harness.

## Current status

**Phase:** Phase 0 — Foundation & ownership closed on 2026-11-10, when its last item (the
date warning, #74) shipped. Phase 1 — Reconciliation engine has started, with #53 in progress. Phase 0's
scope was extended on 2026-08-20 to cover capture friction
([ADR-0017](docs/decisions/0017-extend-phase-0-for-capture-friction.md)), and its end was fixed on
2026-09-23 ([ADR-0029](docs/decisions/0029-close-phase-0-on-date-parsing.md)).

**In progress:** [#53](https://github.com/sujayjangam/unified-ledger-pipeline/issues/53),
participants and accounts as Postgres tables, replacing the env vars
([ADR-0032](docs/decisions/0032-participants-and-accounts-in-postgres.md),
[ADR-0033](docs/decisions/0033-entered-by-participant-id.md); design and plan in
`docs/superpowers/`). The database changes must land in production in order:

1. **Done, merged 2026-11-11** ([#84](https://github.com/sujayjangam/unified-ledger-pipeline/pull/84),
   which records how it was rolled out): migration 0004, the seed script, and the bot, CLI and
   REST API reading the tables while writing both the new ids and the old text columns.
2. **Done, merged 2026-11-14** ([#90](https://github.com/sujayjangam/unified-ledger-pipeline/pull/90)):
   the bot, CLI and REST API stop writing the old text columns (`account_desc`, `account_owner`,
   `entered_by`). REST GET returns the account name as `account` (from `accounts`) instead of
   `account_owner`. Merged without waiting for a few days of normal use: the columns are being
   dropped anyway, so any bug it exposed would need fixing either way.
3. Migration 0005 makes `account_id` required and drops the old columns. Written on
   `feat/migration-0005-53`, not run anywhere. Take a backup first.

The post-merge production checks (plan Task 9 in `docs/superpowers/`) passed on 2026-11-14: real
sends from both people, a test account added and then closed, a seed-script rerun, and the three
old env vars and their secrets removed from Cloud Run (that last step took a second try; see the
plan's Task 9 notes).

**Next action:** step 3. Before migration 0005 can run, two things (found 2026-11-13), in this
order, since the first uses the part of the seed script the second removes:
- **5 production rows have no `account_id`**, so 0005's guard refuses to run. They never recorded
  a card. Decision: assign each one a card (adding a card if needed) through `transaction_overrides`
  and `accounts` in `directory_seed.local.json`, not a hand-typed `UPDATE`, because the script
  checks the names and the closed dates and keeps a record. Order: list the rows
  (`WHERE account_id IS NULL`), edit the seed file, dry run (it rolls back), run the backup
  workflow, then `--write`, then check the count is 0. Their `account_owner` text is wrong too (a
  card name, or "unknown", in place of a person); leave it, since 0005 drops that column and its
  downgrade refills it from `account_id`.
- **`scripts/seed_directory.py` reads `account_desc` and `entered_by`**, which 0005 drops. Done on
  `feat/migration-0005-53`: the transaction-linking part is removed, the seeding part kept. So the
  five-row fix above runs from `main`, before this branch merges.

**After #53: group-trip splitting** (decided 2026-11-14). There's a real group trip with a fixed
deadline, kept out of this public repo. It pulls trips, splitting and settling up forward from
Phase 3, ahead of the rest of Phase 1. The scope is under "Group-trip splitting" in Phase 1
below. #53 finishes first (step 2, then migration 0005). Time-box it, and if it runs long, park
step 3 until after the trip rather than eat into the trip build. Deploy freeze: nothing merges to `main` for the last two days
before the deadline, because a merge auto-deploys and a broken bot mid-trip means nothing gets
logged. Test on a Neon branch with a fictional trip-sized directory first.

Follow-up: "test 1 zebra test card" came back with no amount (the model read "test 1" as part of a
name). Add it to the list in `scripts/check_extraction.py`.

Adding and closing cards from Telegram is
[#83](https://github.com/sujayjangam/unified-ledger-pipeline/issues/83), after #53.

Every Phase 0 item that was still open when the phase closed had already moved to a later phase
(decided 2026-09-23, [ADR-0029](docs/decisions/0029-close-phase-0-on-date-parsing.md)).

**Open top-level issues:**
[#53](https://github.com/sujayjangam/unified-ledger-pipeline/issues/53)
participants and accounts as tables; old text columns to drop (Phase 1) ·
[#27](https://github.com/sujayjangam/unified-ledger-pipeline/issues/27) unpinned dependencies (Phase 1) ·
[#22](https://github.com/sujayjangam/unified-ledger-pipeline/issues/22) entries can't be corrected (Phase 1) ·
[#17](https://github.com/sujayjangam/unified-ledger-pipeline/issues/17) unused REST API (Phase 5) ·
[#61](https://github.com/sujayjangam/unified-ledger-pipeline/issues/61) git-history purge (Phase 5).
Read the list without sub-issue noise with `gh issue list --search "no:parent-issue"`.

### Where things are written down

- **Design decisions** live in [`docs/decisions/`](docs/decisions/) as numbered ADRs — one file
  per decision, each carrying its context, the alternatives, the issue/PR that implemented it,
  and what it cost. **This file says where the project is going; those say why it is built the
  way it is.**
- **Task detail** (repro steps, sub-tasks, verification criteria) lives in GitHub issues, not
  here. This file stays the narrative/phase layer: current phase, what's blocking, and pointers.
- **Session narrative** ("what happened today") belongs in PR descriptions, next to the diff it
  explains. It used to live here and grew to 41% of the file before being moved out on
  2026-08-21.

## Constraints (agreed, don't relitigate without a reason)

- Timeline: ~10-15 hrs/week, ongoing — the project runs indefinitely as live household
infrastructure.
- Ownership: much of the current codebase was written with AI assistance — ownership is solid on
the simple parts, weaker on async/await, Pydantic schemas, and FastAPI's `lifespan` handling. Close
that gap deliberately, not by skipping the code — a standing rule for every phase rather than a
Phase 0 task ([ADR-0029](docs/decisions/0029-close-phase-0-on-date-parsing.md)). Relaxed
2026-11-14: code no longer has to be explainable line by line, without the assistant, before it is
committed. At the pace AI-assisted work moves, that gate isn't realistic. The goal of closing the
gap stands; the per-commit gate is gone
([ADR-0034](docs/decisions/0034-drop-line-by-line-ownership-gate.md)).
- Intended as live household infrastructure for two real users, not a demo — but **currently below
that bar**, and honestly so. Real usage is limited by capture friction, not by capacity or
reliability. Until that closes, claims about production usage belong in this file as
intent, not as fact, and must not be restated as fact in `README.md`.
- Real pain points, in priority order: (1) reconciling voice-logged entries against real bank/card
statements, (2) household expense splitting, (3) budgeting & visibility. FX conversion is *not* a
priority — statements already show converted rates.
- Statement format to support: **PDF exports** (OCBC, DBS, YouTrip and similar), chosen
deliberately over cleaner CSV exports — [ADR-0016](docs/decisions/0016-pdf-statements-over-csv.md).
- Matching approach: deterministic rules first, scored against a hand-labeled eval set; auto-match
only on a *unique* candidate, anything ambiguous routes to `needs_review` —
[ADR-0015](docs/decisions/0015-deterministic-matching-before-llm.md). Revisit once real-world
testing surfaces edge cases.
- Prior art for the matcher: [Actual Budget](https://github.com/actual-budget/actual)'s staged
match (exact id, then amount + a ±7-day window + payee, then amount + window alone). Adopt the
staged structure; the window is a parameter the Phase 2 harness tunes, not a constant to copy.
- PDF parsing strategy: rule-based extraction per bank format first (layouts are fairly
consistent within a bank); LLM-assisted extraction only as a fallback for lines the rules can't
parse. Cheaper than always calling the LLM.
- Budget: infra stays on free tiers (GCP Cloud Run, Neon Postgres free tier, GCS free tier). A
small, usage-based OpenAI spend is acceptable (already paying for voice-to-text; PDF fallback
extraction is the same category of cost) — explore free/local extraction first, LLM fallback
second.
- Data durability: Neon's free-tier point-in-time restore is a rolling **6-hour window, capped at
1GB of changes** — it protects against "I just made a mistake," not against account/provider
issues. Do not treat it as a backup. A scheduled logical backup (`pg_dump` → GCS free tier) is a
required Phase 0/1 deliverable, not optional, given the years-long intended lifetime of this data.
Built as [ADR-0012](docs/decisions/0012-github-actions-over-cloud-scheduler.md) /
[ADR-0014](docs/decisions/0014-pg-dump-custom-format.md).
- Reliability is in scope now, not deferred: automated backups, structured logging, basic CI, a
pytest test suite, and error alerting.
- Packaging: the project's presentation deliverable is a case-study `README.md` — problem →
architecture → key decisions and tradeoffs → what's live today → what's next — plus an inline
Mermaid architecture diagram and a short demo recording — not a docs site, blog series or slide
deck. The README and diagram have shipped; the demo recording is in Phase 5.
- Phasing: design backward from the full target architecture — don't bolt features on
incrementally. Phases 0-2 (foundation, reconciliation engine, evaluation harness) are the
differentiated technical core; Phases 3+ (household splitting, budgeting/visibility, packaging)
are real and committed, not cut — this keeps running as live household infrastructure
indefinitely. Scope discovered mid-phase is absorbed by extending that phase, not by inserting
fractional phases or renumbering.

## Known issues

Fixed items are removed from this list once they ship — the linked issue, PR or ADR is the
record of the fix.

Still outstanding:

- **Production migrations are manual, and `alembic` targets production by default.** The
`Dockerfile` only starts the server, so each migration is a hand-run `alembic upgrade head` that
must land before the code depending on it is merged (a merge auto-deploys). And `alembic/env.py`
loads `.env` — production — never `.env.local`. Found while shipping #9's `0002`; mitigated by a
printed target host and a `docs/LOCAL_TESTING.md` section, not guarded. A migrate-on-deploy step,
or a confirmation prompt when the target is production, would close it.
- Duplicate Telegram update delivery is deduped only in memory (`_seen_update_ids` in
`bot_webhook.py`), which doesn't survive a Cloud Run restart or a second instance. Now that
Postgres exists, this should become a persisted constraint. Deliberately deferred, not forgotten —
now a Phase 1 checklist item.
- Broad `except Exception` blocks throughout silently swallow errors via `print()` instead of
structured logging — failures are invisible in production.
- `app/add_expense.py` computes `was_duplicate` (whether `ON CONFLICT (idempotency_key)` suppressed
the insert) but only `print`s it and returns a bare `True` either way, so no caller can tell a
fresh insert from a duplicate. Left alone deliberately in the #29 fix — changing a signature shared
with the CLI and `app/main.py` to report on a call that no longer happens is the wrong layer (see
[ADR-0023](docs/decisions/0023-in-memory-confirm-card-state.md)) — but it is still a real gap.
- `app/bot_core.py::handle_button_click` never calls `authorize()`, unlike every message handler. Authorization is enforced only when the card is created, so the callback surface itself is
ungated. Found while fixing #29; deliberately not widened into that PR. It matters much less
since webhook deliveries are authenticated ([ADR-0025](docs/decisions/0025-webhook-secret-token.md)): a button press can now only arrive
through Telegram.
- `needs_review` is extracted by `app/services/extraction.py` but never acted on anywhere — the
human-in-the-loop claim doesn't hold until this actually gates bot behavior.
- `.venv/` and `data/ledger.db` are untracked as of 2026-08-01 but **still present in git
history** — purging needs a rewrite + force-push, deliberately deferred. Tracked as
[#61](https://github.com/sujayjangam/unified-ledger-pipeline/issues/61).
- The backup workflow (`.github/workflows/backup.yml`) has no failure alerting yet — deliberately
deferred to when the Phase 1 "Telegram alert on pipeline failure" item exists, which should be
extended to cover this workflow too, not just the reconciliation pipeline.
- **Overdue check (due ~2026-09-11):** confirm the GCS lifecycle rule actually deletes backups
older than 30 days — list the bucket and check the oldest object's date.
- `.github/workflows/backup.yml` pins its `pg_dump` client to Postgres 18 (`postgresql-client-18`
  from PGDG), matching Neon's actual server version confirmed 2026-08-12 — deliberately not the
  unversioned "latest" package, so behavior doesn't silently drift as PGDG publishes new majors.
  If the Neon project is ever upgraded to a new Postgres major version, this pin needs a matching
  update in the same PR as that upgrade, or backups will start failing (client older than server).

Found by inspection 2026-08-19, none filed as issues yet (the capture-side ones are already
sequenced in the phase checklists and aren't repeated here):

- No refund or reversal representation anywhere — `app/add_expense.py` rejects `amount <= 0`.
  Phase 1 reconciliation against real statements hits refunds almost immediately, so this is a
  Phase 1 blocker rather than a cosmetic gap. Now a Phase 1 checklist item.
- `reconciliation_status` is hardcoded to `'unsettled'` in both writers. The field Phase 1 is
  meant to populate currently has no writer at all.
- `benefit_of` and `split_ratio` exist in the schema and in `docs/SCHEMA.md` but are written by no
  code path — Phase 3 will need them, nothing populates them today.
- No income path exists: `transaction_type` is only ever `'Expense'` or `'Transfer'`. Income is
  planned with statement upload (see Phase 1).
- `app/main.py`'s POST handler omits `currency` entirely, so a row created through the REST API
  would land with a NULL currency and corrupt every currency-grouped aggregate in
  `app/services/ledger_queries.py`. Latent rather than live — the Dockerfile runs `bot_webhook`,
  not `main` — but it's a live landmine for whenever the API is deployed.
- No index on `date`. Irrelevant at current row counts; matters once Phase 1's statement staging
  table lands and date-window matching starts scanning.
- Chat commands have no discoverability — `/month` and `/cat_month` were forgotten by their own
  author despite existing. This is an interface problem, not a memory lapse, and it belongs with
  the capture-friction work: a ledger nobody can navigate is a ledger nobody keeps feeding.
  Candidate fix worth prototyping alongside text ingestion: a bot command that pre-fills the
  message box with a field template (date already filled in, remaining fields blank) so an entry
  is edit-and-send rather than type-from-scratch. Telegram exposes two mechanisms for this —
  `switch_inline_query_current_chat`, which genuinely pre-populates the input field but requires
  inline mode enabled on the bot, or sending the template as a tap-to-copy code block, which needs
  no bot configuration but costs the user an extra paste.

## Bugs (not urgent)

Real bugs, but scheduled for Phase 4-5. Phases 1-3 come first.

- **Transfers are counted as expenses in the summary commands.** `/today`, `/week`, `/month`, the
  `/cat_*` commands and `/recent` decide what is a transfer by checking `category = 'Transfer'`
  (`get_period_summary` and `get_category_summary` in `app/services/ledger_queries.py`,
  `recent_command` in `app/bot_core.py`). The bot never writes that category: it records a
  transfer as `transaction_type = 'Transfer'` with category `'YouTrip top-up'`
  (`resolve_payment` in `app/services/participants.py`, `apply_payment_defaults` before #53), and `'Transfer'` isn't one of the categories
  the extractor can pick (`ExpenseCategory` in `app/services/extraction.py`). So a YouTrip top-up
  is added to the expense totals, the Transfers line always reads "No transfers", `/cat_*` lists
  top-ups as a spending category, and `/recent` never shows the transfer label. Fix: check
  `transaction_type` instead in those places — `/recent` also needs it added to
  `get_recent_entries`' `SELECT`, which currently returns only the category. Found by reading the
  code on 2026-09-13; not yet confirmed against production rows.

## Plan

### Phase 0 — Foundation & ownership (closed 2026-11-10)

Closed when [#15](https://github.com/sujayjangam/unified-ledger-pipeline/issues/15) shipped (its last part was the date warning, #74), by the
rule in [ADR-0029](docs/decisions/0029-close-phase-0-on-date-parsing.md); every item still open then
moved to the phase whose work needs it, marked *(from Phase 0)* below. Two caveats from this phase
still hold: CI is a soft gate that catches import-time failures, not runtime ones
([ADR-0020](docs/decisions/0020-ci-scaffolding-before-remaining-capture-work.md)), and two
still-unconfirmed cards for the same spend don't warn each other
([ADR-0027](docs/decisions/0027-duplicate-warning-before-the-card.md)).

### Phase 1 — Reconciliation engine

In working order. Items marked *(from Phase 0)* moved here by
[ADR-0029](docs/decisions/0029-close-phase-0-on-date-parsing.md).

- [ ] Household participants and accounts as Postgres tables, replacing the `ACCOUNT_OWNERS` /
`PRIMARY_ACCOUNT_OWNER` / `ALLOWED_TG_IDS` env vars —
[#53](https://github.com/sujayjangam/unified-ledger-pipeline/issues/53). Goes before the staging
table so statement lines attach to an account row, not a string. The Phase 3 `participants` table
below is this one. In progress: steps 1 and 2 of 3 merged (#84, #90), see "Current status".
- [ ] **Group-trip splitting** (pulled forward from Phase 3 on 2026-11-14 for a real trip, see
"Current status"). Two people log; the other members are participants without a Telegram ID, and
only shared costs they pay get logged. Travel minimum, in build order:
  1. Trips: a trip is a group of participants in families, set up from the gitignored seed file.
     `/trip on <name>` and `/trip off`, with at most one active trip (enforced in the database) and
     one setting shared by both senders. An entry is tagged by when it is logged, not by its date,
     so pre-trip bookings count. The card shows the trip, with a "Not trip" button. The bot reads
     the active trip on every message, like the directory.
  2. Paid-by: who paid, separate from who logged it and which card. The payment method stays
     required. Each non-household payer gets a stand-in account. `accounts.owner_participant_id`
     may give the payer for free; shared accounts (Cash) have no owner, so an explicit paid-by is
     still needed there. Stand-in names join the model's account list, so run
     `scripts/check_extraction`. Until #53 drops them, new writers fill the old text columns too.
  3. Uneven splits from the start, with an even split as the easy case. Per-person shares, rolling
     up to family. A message that says nothing about splitting is personal, and the card gets an
     "Add split" button. That opens a menu of the active trip's members only, one row per family,
     with an "all of this family" toggle and a tap to toggle individuals. Trip members are copied
     into the pending entry (a tap never loads the directory). Button data stays inside Telegram's
     64-byte limit. Shares are in whole cents and must add up to the amount (see the whole-cents
     item under Phase 3). Decide the leftover-cent rule for a payer who isn't in the split, and how
     refunds split.
  4. `/balances`: per person and per family, in separate currencies (no FX conversion), with a
     breakdown that can be forwarded to the group ("why do I owe 340?"). The suggested payments are
     simplified: the fewest payments that settle everyone. Matching the largest debtor to the
     largest creditor is enough at this size. Families settle by default, and a person marked as
     settling alone gets their own line.
  5. Settlement as its own entry type: one person paying another back, in full or in part. It moves
     balances and never counts as spending.
  6. `/recent` (today the last 5 entries) gets paging and a delete button per entry. A soft delete
     is suggested: the row stays but drops out of balances and totals.

  Stretch: split instructions in the message text ("split between me, Alice and Bob").
  The model picks names from the member list only, "me" resolves in Python from the sender, and
  Python works out the cents. Also an "Edit" button on the card before saving (reply with the
  corrected text, reprocessed through `process_expense_text`), and fixing the split and payer
  after saving.

  Must not break: `/today`, `/week`, `/month` and `cat_*` must not count other people's payments or
  the full amount of a shared bill as household spending; decide between own-share and excluding
  stand-in accounts. Settlements and deleted rows are filtered out of every query, including
  `find_recent_duplicate`. Entries already logged before this ships get their splits added by hand
  in SQL. A database check on the splits table rejects shares that don't add up.

  Open: a settlement paid in a different currency from the balance it clears (deferred). Not
  planned for now: more than one payer per bill (log two entries), itemised bills, FX conversion.
  After the trip: a chat flow for creating trips, and the expense dashboard (Phase 4).
- [ ] Refund and reversal representation — `app/add_expense.py` rejects `amount <= 0` today, and
real statements contain refunds almost immediately (see "Known issues"). Needs an ADR on how a
refund is stored before the staging table is designed.
- [ ] PDF statement parser: rule-based extraction per bank format (OCBC, DBS, YouTrip) first. Pin
dependencies ([#27](https://github.com/sujayjangam/unified-ledger-pipeline/issues/27)) no later
than the PR that adds the PDF library *(from Phase 0)*.
- [ ] LLM-assisted extraction as fallback, only for lines the rule-based parser can't handle
- [ ] Normalize parsed statement lines into a staging table (Postgres)
- [ ] Migrations applied from scratch (`alembic upgrade head` against a throwaway Postgres
container), and the `ON CONFLICT (idempotency_key)` path against a real database — the staging
table is the next migration. Nothing in `tests/` touches a real database today:
`tests/test_migrations.py` checks only the revision chain's structure *(from Phase 0)*.
- [ ] Income as a `transaction_type`, so salary and other money coming in can be recorded once
statement upload is live. Today only `'Expense'` and `'Transfer'` exist.
- [ ] Deterministic matcher: amount + date window against existing ledger entries
- [ ] Tie-breaking rule: auto-match only on a unique candidate; 0 or 2+ candidates → `needs_review`
- [ ] Wire matcher output into the `reconciliation_status` field
- [ ] Wire `needs_review` so it actually gates bot behavior — the matcher is what produces review
cases, and the human-in-the-loop framing doesn't hold up until this exists *(from Phase 0)*
- [ ] `base_amount` correctness — currently written as the raw amount regardless of currency,
contradicting `docs/SCHEMA.md`. Prefer absent (`NULL`) over silently incorrect; statements are the
first source of converted amounts *(from Phase 0)*.
- [ ] Structured logging to replace silent `except`/`print` error handling *(from Phase 0)*
- [ ] Telegram alert on pipeline failure, reporting through that logging
- [ ] Webhook idempotency — persist the `update_id` dedupe in Postgres instead of process memory.
Originally deferred because voice-only ingestion capped volume; text ingestion ended that premise
*(from Phase 0)*.
- [ ] Edit and delete path for *saved* rows —
[#22](https://github.com/sujayjangam/unified-ledger-pipeline/issues/22). No `UPDATE` or `DELETE`
exists anywhere in `app/`, so a wrong extraction is permanent; the mismatches the matcher flags are
the rows that need correcting. First step: the Confirm / Edit / Cancel card
([#66](https://github.com/sujayjangam/unified-ledger-pipeline/issues/66), `context.user_data` only,
no DB write), whose field picker the saved-row edit reuses. Saved-row edits need a
hard-vs-soft-delete decision first. Add routing coverage for the new callback handlers with it
*(from Phase 0)*.
- [ ] Receipt image capture (Telegram photo message) as a second ingestion path alongside voice
notes — store the image (GCS) and link it to the transaction row; reuses the rule-based +
LLM-fallback extraction architecture above rather than building a separate one-off pipeline.
Good to have, not needed for the trip (2026-11-14). The first use is as a memory aid ("why did this
cost $100?"): store the photo with the entry and show it again later, with no extraction. Storing
Telegram's `file_id` may be enough. Check how long Telegram keeps photos before relying on it; GCS
is the fallback.

### Phase 2 — Evaluation harness

- [ ] Hand-label a golden set of real statement-line → ledger-entry matches/non-matches
- [ ] Score the matcher: precision/recall/F1
- [ ] Tune the date window against the golden set rather than inheriting a constant — Actual
Budget's ±7 days is a starting point to measure, not a value to copy (see Constraints)
- [ ] Expand the pytest suite to cover matcher edge cases surfaced by the golden set
- [ ] **Auto-categorisation rules** — derive `token → category` rules from confirmed
`transactions` history (`description` holds the raw input, `category` holds the human-confirmed
answer), so common entries resolve without an LLM call at all. Moving categories into their own
table is a candidate that goes with this item. Sits in this phase rather than
Phase 0 because the support/purity thresholds want the eval harness to tune them rather than being
guessed. Note nothing in the codebase consults history for anything today — the card → owner
lookup (`whose_card_paid` in `app/services/participants.py`, an env var before #53) is the same
*shape* but is hand-maintained, not derived.
- [ ] Document the methodology

### Phase 3 — Household splitting

The trip, split and settle-up items here moved to Phase 1 ("Group-trip splitting") on 2026-11-14.
What stays here builds on them: the household as a group with no end date, and the items below that
weren't pulled forward.

- [ ] Schema: `transaction_splits` child table, `split_type` enum. *Moved to Phase 1, Group-trip
splitting.*
- [ ] Application logic: who-owes-who calculation. *Moved to Phase 1, Group-trip splitting, with
uneven splits from the start.*
- [ ] Extend as real usage surfaces the need for more flexible splitting
- [ ] Split in whole cents, so the parts always add up to the entry's amount (reconciliation
depends on it; the rule is recorded in [ADR-0028](docs/decisions/0028-exact-cent-conversion-half-up.md)). Store each person's share as integer
cents in `transaction_splits`, with a check that the shares sum to the entry. Work the shares out by
rounding every share down and handing the leftover cents out one at a time, never by rounding each
share: SGD 5.55 split two ways would otherwise be 2.78 + 2.78 = 5.56. This replaces the
`split_ratio` Decimal column, which nothing writes today. Who gets a leftover cent must be a fixed
rule, not random, so the same entry always splits the same way; decide it here (suggested:
whoever's card paid).
- [ ] A database view that shows each entry's sender by name: `entered_by_participant_id` joined to
the `participants` table from [#53](https://github.com/sujayjangam/unified-ledger-pipeline/issues/53), so the base table keeps storing only the id
([ADR-0033](docs/decisions/0033-entered-by-participant-id.md)) while anything reading the ledger sees names. Possible now that #84 put names in the
`participants` table. Deliberately after Phase 2 (decided 2026-09-12).
- [ ] Trips and groups (design note from #53, 2026-11-11). *Trips moved to Phase 1 on
2026-11-14; the household as a group stays here.* A trip is
a group of participants with optional start and end dates. Expenses tagged to it are split and
settled within it, and one dated outside the trip gets a non-blocking ⚠️ line on the card, since
post-trip expenses are real. The household becomes a group with no end date, so splitting is
written once for both. #53 adds participants without a Telegram ID for exactly this: people who
are split with but never use the bot.
- [ ] Fewer false duplicate warnings. Today any two entries with the same amount and currency
within 5 minutes are flagged, even unrelated ones
([ADR-0027](docs/decisions/0027-duplicate-warning-before-the-card.md)). Candidates: also require the
same category, or compare the descriptions' words after dropping filler words. Either should only
soften the warning, not suppress it, because two people describing one shared bill differently is
one of the cases it exists for. Measure against real entries the way Phase 2 measures the matcher
before choosing (decided 2026-09-12).

### Phase 4 — Budgeting & visibility

- [ ] Spend-vs-budget view
- [ ] Weekly digest (Telegram)
- [ ] Reconciled vs. unreconciled breakdown
- [ ] Expense dashboard, filterable by trip, category, person and date (asked for 2026-11-14). It
could be read-only, served by the existing FastAPI app on Cloud Run. Access control is the open
question: a hard-to-guess link per trip, or a real login.
- [ ] Creating trips from Telegram (a chat flow), after the trip build. Until then, trips come from
the seed file.

**Capture conveniences** (moved from Phase 0 by
[ADR-0029](docs/decisions/0029-close-phase-0-on-date-parsing.md); both already have a workaround —
send the message again):

- [ ] Missing-amount recovery — when extraction returns no amount, `process_expense_text` abandons
the entry with a text prompt, so the user has to start over from scratch. Offer
`[Manual Entry]` / `[New Voice Note]` buttons instead, keeping the raw text already captured.
Salvaged from the MVP flow doc that
[#28](https://github.com/sujayjangam/unified-ledger-pipeline/issues/28) retired. No issue filed yet.
- [ ] Drop the one-expense-per-message guardrail in `app/bot_core.py`
([ADR-0010](docs/decisions/0010-one-expense-per-message.md)) — `TransactionList` already models
multiple; this is a product restriction, not a technical limit.
- [ ] Every ingestion path tested end to end, against the capture paths as they end up.

### Phase 5 — Reliability hardening & packaging refresh

The case-study README and architecture diagram moved to the end of Phase 0 on 2026-08-20, so this
phase is no longer the project's first packaging pass — it's the pass that brings the published
material back in line with a system that by then has reconciliation, an eval harness, splitting and
budgeting in it.

- [ ] Refresh the case-study `README.md` and architecture diagram against the shipped Phase 1-4
system — in particular the eval metrics, which don't exist yet at the Phase 0 writeup
- [ ] Error alerting beyond the Phase 1 Telegram pipeline-failure alert, extended to cover
`.github/workflows/backup.yml` (see "Still outstanding")
- [ ] Short demo recording → GIF at the top of `README.md`, showing the working system rather than
the friction-limited one. Recording early as a private friction-finding exercise is still
worthwhile; the published one comes here (moved from Phase 0 by ADR-0029).
- [ ] Keep or remove the unused REST API —
[#17](https://github.com/sujayjangam/unified-ledger-pipeline/issues/17) (moved from Phase 0 by
ADR-0029)
- [ ] Revisit the deferred git-history purge of `.venv/` and `data/ledger.db` — [#61](https://github.com/sujayjangam/unified-ledger-pipeline/issues/61)
