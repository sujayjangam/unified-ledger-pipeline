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

**Where we are.** Phase 0 (foundation) closed on 2026-11-10
([ADR-0029](docs/decisions/0029-close-phase-0-on-date-parsing.md)). Phase 1 (the reconciliation
engine) is open, but its group-trip work goes first. There's a real group trip with a fixed date,
kept out of this public repo, so on 2026-11-14 trips, splitting and settling up were pulled
forward from Phase 3. The scope is under "Group-trip splitting" in Phase 1 below.

**Deploy freeze.** A merge to `main` deploys the bot straight away, and a broken bot mid-trip
means nothing gets logged. So nothing merges in the last two days before the trip. Anything new
is tried on a Neon branch with a fictional trip-sized directory first.

**Next up**, in this order:

1. Settling up redesigned: a typed "settle" message read by Python, each payback linked to the
   bills it clears, no "gift" wording
   ([#134](https://github.com/sujayjangam/unified-ledger-pipeline/issues/134), which takes in
   [#113](https://github.com/sujayjangam/unified-ledger-pipeline/issues/113) and
   [#114](https://github.com/sujayjangam/unified-ledger-pipeline/issues/114); design in
   [ADR-0052](docs/decisions/0052-settle-up-by-typed-message.md), agreed 2026-11-19). The same
   method serves everyday bills, for a later `/bals`. Typed path first, with a new
   `settlement_bills` table (migration 0011, on production). PR 1, PR 2 (the reader) and PR 3
   (the settle card with its change buttons and rates,
   [#142](https://github.com/sujayjangam/unified-ledger-pipeline/pull/142)) are merged.
   A payback in another currency is converted at a reference rate and flagged for a statement
   check ([#138](https://github.com/sujayjangam/unified-ledger-pipeline/issues/138),
   [ADR-0053](docs/decisions/0053-other-currency-paybacks-at-a-reference-rate.md)). Build plan,
   three PRs (the change buttons moved into PR 3):
   `docs/superpowers/plans/2026-11-19-settle-up.md`. The bot's screen speed is timed on a
   phone before the freeze
   ([#137](https://github.com/sujayjangam/unified-ledger-pipeline/issues/137)). A "Settle up"
   button on `/trip_bals` waits until after the trip.
2. Simplify trip debts to the fewest payments, only along existing debt lines
   ([#129](https://github.com/sujayjangam/unified-ledger-pipeline/issues/129), the last part of
   [#127](https://github.com/sujayjangam/unified-ledger-pipeline/issues/127); rule in
   [ADR-0051](docs/decisions/0051-debts-per-person-then-families.md)). Done 2026-11-19
   ([#144](https://github.com/sujayjangam/unified-ledger-pipeline/pull/144)): loops of debts
   cancel too, and each shortcut gets a 💡 note line under "To settle".
   Required before the freeze, in its own PR: offset rows (ADR-0052,
   [#145](https://github.com/sujayjangam/unified-ledger-pipeline/issues/145)) that mark the
   bills on a shortened line as paid when it is paid, so the settle card's bill list matches
   `/trip_bals`, and a shortcut paid by any family member clears in full. Worked out again
   from the bills when the payback card opens, kept on the card, never stored. Built on `feat/offset-rows` (2026-11-20, gate green, in
   review). Tried on a Neon branch and in the test bot, and a read-only production check
   found no saved payback with a row on a bill its receiver didn't pay, so no old payback
   changes meaning. The review found that deleting a bill behind an offset row hid money owed
   back; fixed in the same PR
   ([ADR-0052](docs/decisions/0052-settle-up-by-typed-message.md)).

Later: load a finished trip's hand-kept spreadsheet as real bills, and work out the trip from the
message itself for costs logged before it starts.

**After the trip:**

- A split reader written only in Python
  ([#122](https://github.com/sujayjangam/unified-ledger-pipeline/issues/122)). It gets scored
  offline against the check cases and the trip's real messages before it replaces the model's
  split fields. First, stop reading per-person counts: an uneven stay is logged as separate
  even entries ([#130](https://github.com/sujayjangam/unified-ledger-pipeline/issues/130)).
- Everyday groups in the split menu ("splits PR 2b"), designed in
  [ADR-0043](docs/decisions/0043-everyday-groups-for-the-split-menu.md) and parked. An unfinished
  first pass sits on the local branch `feat/splits-family-group`; the splits plan says how to pick
  it up.
- Aliases per sender ("dad", "mummy"; see Phase 1 below).

**Recently shipped**, newest first. Each PR and ADR has the full story.

- **2026-11-19: trip debts worked out per person, then rolled up to families**
  ([#128](https://github.com/sujayjangam/unified-ledger-pipeline/issues/128), PR
  [#133](https://github.com/sujayjangam/unified-ledger-pipeline/pull/133),
  [ADR-0051](docs/decisions/0051-debts-per-person-then-families.md)). Relatives who paid for
  each other now settle up too ("Bob pays Alice"), two families' debts to different people no
  longer cancel, and anyone but the receiver can pay on someone's behalf. No migration.

- **2026-11-18: delete and undo saved entries from `/recent`**
  ([#115](https://github.com/sujayjangam/unified-ledger-pipeline/issues/115), PR
  [#125](https://github.com/sujayjangam/unified-ledger-pipeline/pull/125),
  [ADR-0050](docs/decisions/0050-soft-delete-behind-a-view.md)). `/recent` pages 5 at a time
  with a 🗑 per entry, a second tap to confirm, and Undo. The table is now `raw_transactions`
  behind a `transactions` view of live rows, so every total, `/trip_bals` and the duplicate
  check skip deleted entries with no query changed. Migration 0010 was applied to production by
  hand after a backup, before the merge; tried on the test bot first.
- **2026-11-18: a smaller extraction prompt, run at temperature 0**
  ([#120](https://github.com/sujayjangam/unified-ledger-pipeline/issues/120), PR
  [#121](https://github.com/sujayjangam/unified-ledger-pipeline/pull/121),
  [ADR-0049](docs/decisions/0049-prompt-holds-only-what-python-cant-read.md)). The model now reads
  about 8,300 characters per message instead of 12,600. The unused `needs_review` flag and the
  model's "uneven" flag are gone; Python reads uneven wording from the message instead.
  `check_extraction` gained `--only` and `--margins`. Checked against the real model before the
  merge.
- **2026-11-18: exact amounts per person in a split**
  ([#112](https://github.com/sujayjangam/unified-ledger-pipeline/issues/112), PR
  [#118](https://github.com/sujayjangam/unified-ledger-pipeline/pull/118),
  [ADR-0048](docs/decisions/0048-exact-split-amounts-by-reply.md)). "Set amounts" sends a reply
  prompt, Python reads replies like "Bob 90", and anyone not set shares what's left. No
  migration. Tried on the test bot first.
- **2026-11-18: paybacks** (PR
  [#111](https://github.com/sujayjangam/unified-ledger-pipeline/pull/111),
  [ADR-0046](docs/decisions/0046-settlements-as-their-own-records.md),
  [ADR-0047](docs/decisions/0047-pay-back-only-who-you-owe.md)). A "Mark paid" button under each
  suggested payment opens a payback card, saved only on Confirm. Paybacks are their own records,
  never spending. Tried on the test bot against a Neon branch; migration 0009 was applied to
  production by hand on 2026-11-17.
- **2026-11-17: `/trip_bals`**, who owes whom on one trip (PR
  [#108](https://github.com/sujayjangam/unified-ledger-pipeline/pull/108),
  [ADR-0044](docs/decisions/0044-trip-balances-from-splits.md)). Money goes back to the person who
  paid, not their family, and shares inside the payer's own family cancel out. The totals still
  count a shared bill in full when the household paid it. No migration.
- **2026-11-17: splits by nights that name trip families, and `/help`** (PR
  [#110](https://github.com/sujayjangam/unified-ledger-pipeline/pull/110),
  [ADR-0045](docs/decisions/0045-family-labels-the-rest-and-stricter-wording.md)). "Reds and Blues
  5 nights, rest all 6" now works. A count read loosely gets a "⚠️ Check …" line on the card.
  `/help` teaches the wordings that work, so stricter wording stands in for a second model check.
- **2026-11-17: uneven shares by a count per person**, such as nights (PR
  [#106](https://github.com/sujayjangam/unified-ledger-pipeline/pull/106),
  [ADR-0042](docs/decisions/0042-uneven-shares-by-count.md)). No migration.
- **2026-11-16: one-person split wording** (PR
  [#105](https://github.com/sujayjangam/unified-ledger-pipeline/pull/105)). The card says "Carol
  owes Alice 5.55" instead of "Split: only Carol". A split whose only person is the payer isn't a
  split, so nothing is shown or saved.
- **2026-11-16: the split menu**, and people menus offering everyone when no trip is on (PR
  [#103](https://github.com/sujayjangam/unified-ledger-pipeline/pull/103),
  [ADR-0041](docs/decisions/0041-split-menu-and-wider-people-menus.md)).
- **2026-11-16: even splits read, stored and shown** (PR
  [#101](https://github.com/sujayjangam/unified-ledger-pipeline/pull/101),
  [ADR-0039](docs/decisions/0039-splits-read-by-the-model-worked-out-by-python.md),
  [ADR-0040](docs/decisions/0040-model-as-second-reader-for-the-payer.md)). Migration 0008
  (`transaction_splits`) applied to production by hand after a backup.
- **2026-11-16: the "Change who paid" button** (PR
  [#99](https://github.com/sujayjangam/unified-ledger-pipeline/pull/99),
  [ADR-0038](docs/decisions/0038-paid-by-button-starts-from-the-message.md)), with a lock per card
  so a Confirm can't land in the middle of a redraw.
- **2026-11-15: who paid** (PR
  [#97](https://github.com/sujayjangam/unified-ledger-pipeline/pull/97),
  [ADR-0037](docs/decisions/0037-paid-by-person-read-from-text.md)). Migration 0007, the payer read
  from the message in Python, and totals that leave out outside payers.
- **2026-11-15: trips and trip mode** (PR
  [#95](https://github.com/sujayjangam/unified-ledger-pipeline/pull/95),
  [ADR-0035](docs/decisions/0035-trips-tagged-at-log-time.md),
  [ADR-0036](docs/decisions/0036-trip-switch-per-person.md)). Migration 0006.
- **2026-11-15: a closed card no longer wins over an open one the message names** (PR
  [#96](https://github.com/sujayjangam/unified-ledger-pipeline/pull/96)).
- **2026-11-14: the "Saved to Ledger!" message shows the account and its owner**
  ([#92](https://github.com/sujayjangam/unified-ledger-pipeline/issues/92)).
- **2026-11-14: participants and accounts live in Postgres**, not env vars
  ([#53](https://github.com/sujayjangam/unified-ledger-pipeline/issues/53), PRs
  [#84](https://github.com/sujayjangam/unified-ledger-pipeline/pull/84),
  [#90](https://github.com/sujayjangam/unified-ledger-pipeline/pull/90),
  [#91](https://github.com/sujayjangam/unified-ledger-pipeline/pull/91);
  [ADR-0032](docs/decisions/0032-participants-and-accounts-in-postgres.md),
  [ADR-0033](docs/decisions/0033-entered-by-participant-id.md)). Migration 0005 dropped the old
  text columns.

**Loose ends:**

- "test 1 zebra test card" comes back with no amount: the model reads "test 1" as part of a
  name. The check case is on the unpushed branch `chore/check-extraction-zebra`, where it failed
  10 times out of 10 on 2026-11-14, before the prompt was trimmed. Not rerun since; the fix is
  still open.
- Someone outside the trip showing up in the people menus is covered by unit tests only. Every
  test participant on the test bot was a trip member.
- Menus are slow. From the laptop to the Neon test branch, one round trip is about 295ms, and
  each database block adds three more besides its queries (the pool's ping, BEGIN and ROLLBACK).
  So `/trip` makes about 10 round trips for 4 queries, around 3 seconds. Not started: autocommit
  for read-only reads, and fewer blocks per message. Production logs the same timing lines, so
  read those before choosing. Putting Cloud Run and Neon in the same region is the real fix, but
  a bigger job.
- Idea: when someone's trip mode is off but an entry's date falls inside one of their trips, the
  card could offer "Tag to <trip>?". Only ever a suggestion, never an automatic tag (ADR-0036).
- Adding and closing cards from Telegram is
  [#83](https://github.com/sujayjangam/unified-ledger-pipeline/issues/83).
- Pinning dependencies ([#27](https://github.com/sujayjangam/unified-ledger-pipeline/issues/27))
  has a first pass on the local branch `chore/pin-dependencies-27`.

**Open top-level issues:**
[#134](https://github.com/sujayjangam/unified-ledger-pipeline/issues/134) settle-up redesign, with
[#113](https://github.com/sujayjangam/unified-ledger-pipeline/issues/113) and
[#114](https://github.com/sujayjangam/unified-ledger-pipeline/issues/114) (next up, design first) ·
[#127](https://github.com/sujayjangam/unified-ledger-pipeline/issues/127) trip balances, [#145](https://github.com/sujayjangam/unified-ledger-pipeline/issues/145) offset rows left (in progress) ·
[#132](https://github.com/sujayjangam/unified-ledger-pipeline/issues/132) early ADRs too long ·
[#122](https://github.com/sujayjangam/unified-ledger-pipeline/issues/122) Python-only split reader (after the trip) ·
[#83](https://github.com/sujayjangam/unified-ledger-pipeline/issues/83) cards from Telegram ·
[#27](https://github.com/sujayjangam/unified-ledger-pipeline/issues/27) unpinned dependencies (Phase 1) ·
[#22](https://github.com/sujayjangam/unified-ledger-pipeline/issues/22) saved entries can't be corrected (Phase 1) ·
[#17](https://github.com/sujayjangam/unified-ledger-pipeline/issues/17) unused REST API (Phase 5) ·
[#61](https://github.com/sujayjangam/unified-ledger-pipeline/issues/61) git-history purge (Phase 5).
To see the list without sub-issues: `gh issue list --search "no:parent-issue"`.

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
  code path. `split_ratio` is replaced by `transaction_splits` (one whole-cent share per person),
  so it is dead and could be dropped by a migration. `reconciliation_status` (above) says nothing
  about paybacks: whether a bill is paid off isn't stored anywhere.
- No income path exists: `transaction_type` is only ever `'Expense'` or `'Transfer'`. Income is
  planned with statement upload (see Phase 1).
- `app/main.py`'s POST handler omits `currency` entirely, so a row created through the REST API
  would land with a NULL currency and corrupt every currency-grouped aggregate in
  `app/services/ledger_queries.py`. Latent rather than live — the Dockerfile runs `bot_webhook`,
  not `main` — but it's a live landmine for whenever the API is deployed.
- No index on `date`. Irrelevant at current row counts; matters once Phase 1's statement staging
  table lands and date-window matching starts scanning.
- Commands used to be hard to find: `/month` and `/cat_month` were forgotten by their own author.
  Now every command is in Telegram's "/" menu (`register_commands`), and `/help` shows how to word
  an entry. Typing an entry from scratch is still the slow part.
  Candidate fix worth prototyping alongside text ingestion: a bot command that pre-fills the
  message box with a field template (date already filled in, remaining fields blank) so an entry
  is edit-and-send rather than type-from-scratch. Telegram exposes two mechanisms for this —
  `switch_inline_query_current_chat`, which genuinely pre-populates the input field but requires
  inline mode enabled on the bot, or sending the template as a tap-to-copy code block, which needs
  no bot configuration but costs the user an extra paste.

## Bugs (not urgent)

Real bugs, but scheduled for Phase 4-5. Phases 1-3 come first.

- **Transfers are counted as expenses in the summary commands.** `/today`, `/week`, `/month`, the
  `/cat_*` commands decide what is a transfer by checking `category = 'Transfer'`
  (`get_period_summary` and `get_category_summary` in `app/services/ledger_queries.py`;
  `/recent` was fixed with #115 and checks `transaction_type`). The bot never writes that category: it records a
  transfer as `transaction_type = 'Transfer'` with category `'YouTrip top-up'`
  (`resolve_payment` in `app/services/participants.py`, `apply_payment_defaults` before #53), and `'Transfer'` isn't one of the categories
  the extractor can pick (`ExpenseCategory` in `app/services/extraction.py`). So a YouTrip top-up
  is added to the expense totals, the Transfers line always reads "No transfers", `/cat_*` lists
  top-ups as a spending category. Fix: check `transaction_type` instead in those places. Found by
  reading the code on 2026-09-13; not yet confirmed against production rows.

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

- [x] Household participants and accounts as Postgres tables, replacing the `ACCOUNT_OWNERS` /
`PRIMARY_ACCOUNT_OWNER` / `ALLOWED_TG_IDS` env vars —
[#53](https://github.com/sujayjangam/unified-ledger-pipeline/issues/53). Goes before the staging
table so statement lines attach to an account row, not a string. The Phase 3 `participants` table
below is this one. Done 2026-11-14 (#84, #90, #91).
- [ ] **Group-trip splitting** (pulled forward from Phase 3 on 2026-11-14 for a real trip, see
"Current status"). Two people log; the other members are participants without a Telegram ID, and
only shared costs they pay get logged. Travel minimum, in build order:
  1. *(Done 2026-11-15, [#95](https://github.com/sujayjangam/unified-ledger-pipeline/pull/95).)*
     Trips: a trip is a group of participants in families, set up from the gitignored seed file.
     Each person switches trip mode from `/trip`'s buttons; at most one trip on per person, only
     a trip they are a member of (enforced in the database), with a pinned banner while it is on.
     An entry is tagged by when it is logged, not by its date,
     so pre-trip bookings count. The card shows the trip, with a "Not trip" button. The bot reads
     the sender's trip on every message, like the directory.
  2. Paid-by: who paid, separate from who logged it and which card. The payment method stays
     required. Planned 2026-11-15 in `docs/superpowers/plans/2026-11-15-paid-by.md`: a payer
     column read by Python from the message, a "Paid by" button to fix it, an owned account
     always deciding the payer, and a shared "Paid by someone else" account for outside payers.
     PR 1 (everything but the button) done 2026-11-15,
     [#97](https://github.com/sujayjangam/unified-ledger-pipeline/pull/97); PR 2, the button, is
     done 2026-11-16,
     [#99](https://github.com/sujayjangam/unified-ledger-pipeline/pull/99).
  3. Uneven splits from the start, with an even split as the easy case. Per-person shares, rolling
     up to family. A message that says nothing about splitting is personal, and the card gets an
     "Add split" button. While trip mode is on, that opens a menu of the active trip's members
     only. While it is off, the menu offers every participant, so friends who were added for a
     trip can share an everyday bill too (decided 2026-11-16). One row per family,
     with an "all of this family" toggle and a tap to toggle individuals. Trip members are copied
     into the pending entry (a tap never loads the directory). Button data stays inside Telegram's
     64-byte limit. Shares are in whole cents and must add up to the amount (see the whole-cents
     item under Phase 3). Leftover cents go to the largest remainder, ties to the lowest
     participant id (ADR-0039). How refunds split is still open.
     PR 1 (read, store and show an even split) done 2026-11-16,
     [#101](https://github.com/sujayjangam/unified-ledger-pipeline/pull/101); PR 2, the split menu,
     done 2026-11-16, [#103](https://github.com/sujayjangam/unified-ledger-pipeline/pull/103).
     The one-person split wording fix merged 2026-11-16 (#105). PR 3, uneven shares by count
     (nights, ADR-0042), done 2026-11-17,
     [#106](https://github.com/sujayjangam/unified-ledger-pipeline/pull/106). Exact amounts per
     person, typed as a reply after "Set amounts" (ADR-0048), done 2026-11-18,
     [#118](https://github.com/sujayjangam/unified-ledger-pipeline/pull/118).
     After the trip: PR 2b (everyday groups in the split menu, designed in ADR-0043 and parked
     2026-11-17), aliases per sender, percentages, and a count editor in the split
     menu, so a newly ticked person's count can be changed without resending.
     - Candidate, after the trip: a trip read from the message text. Pre-trip costs are logged
       with trip mode off ("travel insurance for Sydney trip"), and nobody switches trip mode
       on for one entry. The bot would tag the entry to the upcoming trip whose destination the
       message names, with a button to pick another trip. Trip mode stays for while travelling
       and for splitting across everyone on the trip. Raised 2026-11-17; not designed yet.
     - Weighted shares, shipped in PR 3
       ([ADR-0042](docs/decisions/0042-uneven-shares-by-count.md)), for the trip's
       accommodation: a 5-night booking where some members stay only 4 nights. Ticking people
       gave only an even split, so the split carries a weight per person (nights, or any
       count). Each share is amount × weight ÷ total weight,
       handed out in whole cents like every other split; one card charge stays one entry, which
       keeps reconciliation against the statement simple. Logging it as two entries (4 nights
       across everyone, 1 night across the rest) was considered and rejected for that reason.
     - The person logging never does the maths. They say it in plain words; the model reads only
       who and how many (people, families, nights, a fixed amount), never a share, and Python
       works out the cents, the same way dates work
       ([ADR-0030](docs/decisions/0030-llm-reads-dates-python-resolves.md)). The card shows each
       share before Confirm, and the split menu can still change it. Messages it should handle
       (fictional names):
       - "Airbnb 2300 on my Visa, 5 nights, everyone except Carol's family who only stayed 4"
       - "Airbnb 2300, split by nights: Alice's and Bob's families 5, Carol and Dave 4"
       - "Dinner 480 cash, split evenly between all"
       - "Petrol 90, split between Alice's and Bob's families"
       - "Groceries 150, Carol's share is 50, rest split evenly"
       All five are cases in `scripts/check_extraction`. The last one isn't read as a fixed
       share: the card ticks the names evenly, with a warning and a "Set amounts" button
       (ADR-0048).
  4. `/trip_bals`: one trip's balances per family and per person, in separate currencies (no FX
     conversion), as plain text that can be forwarded to the group
     ([ADR-0044](docs/decisions/0044-trip-balances-from-splits.md)). It shows the active trip,
     or a list of trips to tap. The suggested payments match the largest debtor to the largest
     creditor. Families settle by default and pay back the person who paid, and a person who
     settles alone has a family label of their own. Done 2026-11-17,
     [#108](https://github.com/sujayjangam/unified-ledger-pipeline/pull/108).
     Later, for trips with friends rather than families: each friend (or couple) is a family
     of one today, which works but reads as "Bob family". Show a family of one by the person's
     name, in `/trip_bals` and the split menu. Not planned in detail.
     - Still to build: the per-entry breakdown ("why do I owe 340?") and the running tab
       between close family (`/bals`), where trip and everyday costs mix. Both are planned
       for a shared Google Sheet that the bot writes and everyone else can only view (anyone
       with the link), refreshed when the command is used. Python does the maths; the sheet
       holds finished numbers and is never read back.
  5. Settlement as its own entry type: one person paying another back, in full or in part. It moves
     balances and never counts as spending. Designed 2026-11-17
     ([ADR-0046](docs/decisions/0046-settlements-as-their-own-records.md)): a `settlements`
     table, a "Mark paid" tap under each suggested payment, the paid side may be in another
     currency (both amounts stated, no exchange-rate lookup), and an overpayment saves behind a
     "⚠️ Confirm overpayment" button. If time allows: an Edit step on the payback card.
     Done 2026-11-18, [#111](https://github.com/sujayjangam/unified-ledger-pipeline/pull/111).
     Found on the test bot, to fix: no record of which bill a payback was for
     ([#113](https://github.com/sujayjangam/unified-ledger-pipeline/issues/113)), and numbered
     "Mark paid" buttons ([#114](https://github.com/sujayjangam/unified-ledger-pipeline/issues/114)).
  6. `/recent` gets paging and a delete button per entry, with Undo. A soft delete: the row stays
     but drops out of balances, totals and the duplicate check, through a view
     ([#115](https://github.com/sujayjangam/unified-ledger-pipeline/issues/115),
     [ADR-0050](docs/decisions/0050-soft-delete-behind-a-view.md)). Done 2026-11-18,
     [#125](https://github.com/sujayjangam/unified-ledger-pipeline/pull/125).

  Stretch: an "Edit" button on the card before saving (reply with the corrected text,
  reprocessed through `process_expense_text`), and fixing the split and payer after saving.
  Splits read from the message text, once a stretch goal, shipped in step 3.

  Must not break: `/today`, `/week`, `/month` and `cat_*` must not count other people's payments
  as household spending. Paid-by (step 2) skips entries paid outside the household; a shared
  bill a household member paid counts in full, and stays that way (decided 2026-11-17,
  ADR-0044: money paid for someone else is the payer's spending until it is paid back).
  Settlements and deleted rows are filtered out of every query, including
  `find_recent_duplicate`. Entries already logged before this ships get their splits added by hand
  in SQL. Shares that don't add up are rejected in Python, inside the same database transaction
  as the entry (ADR-0039).

  A settlement paid in a different currency from the balance it clears is in step 5 (ADR-0046). Not
  planned for now: more than one payer per bill (log two entries), itemised bills, FX conversion.
  After the trip: a chat flow for creating trips, and the expense dashboard (Phase 4).

  Moved to after the trip (decided 2026-11-17): aliases per sender. People say "dad" or "mummy", not a display name,
  and the same word means different people for different senders, so one shared alias list would
  be wrong. An alias belongs to the sender: (sender, alias) → participant, set up from the
  gitignored seed like the directory, and checked by `match_person` after display names. Not the
  old-spellings `aliases` table ADR-0032 dropped; that was a different problem. Needs an ADR.
  Until then, name the person.
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
- [ ] A review state that actually changes what the bot does. The matcher is what produces cases
to review. The model's old `needs_review` flag was removed in #120 because nothing read it, so
this starts from the matcher, not the model *(from Phase 0)*
- [ ] `base_amount` correctness — currently written as the raw amount regardless of currency,
contradicting `docs/SCHEMA.md`. Prefer absent (`NULL`) over silently incorrect; statements are the
first source of converted amounts *(from Phase 0)*.
- [ ] Structured logging to replace silent `except`/`print` error handling *(from Phase 0)*
- [ ] Telegram alert on pipeline failure, reporting through that logging
- [ ] Webhook idempotency — persist the `update_id` dedupe in Postgres instead of process memory.
Originally deferred because voice-only ingestion capped volume; text ingestion ended that premise
*(from Phase 0)*.
- [ ] Edit path for *saved* rows —
[#22](https://github.com/sujayjangam/unified-ledger-pipeline/issues/22). A saved row can be
deleted from `/recent` (#115, a soft delete,
[ADR-0050](docs/decisions/0050-soft-delete-behind-a-view.md)), but not corrected: a wrong
extraction has to be deleted and logged again. The mismatches the matcher flags are the rows that
need correcting. First step: the Confirm / Edit / Cancel card
([#66](https://github.com/sujayjangam/unified-ledger-pipeline/issues/66), `context.user_data` only,
no DB write), whose field picker the saved-row edit reuses. Add routing coverage for the new
callback handlers with it *(from Phase 0)*.
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
- [ ] Candidate (2026-11-16, not planned): score a classification-only model against GPT-4o-mini
for the choices the bot makes from a fixed list (category, account, "is this a split?"), on real
confirmed messages. It can't replace the extraction of amounts, names or dates, which are values
in the message, not options from a list.
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

- [x] Schema: `transaction_splits` child table. *Moved to Phase 1, Group-trip splitting; shipped
2026-11-16 in migration 0008 (#101), storing whole-cent shares only. The `split_type` enum was not
built: the kind of split is worked out in Python and not stored. Uneven shares (PR 3,
ADR-0042) store cents only too, so it is not needed for now.*
- [x] Application logic: who-owes-who calculation. *Moved to Phase 1, Group-trip splitting;
shipped 2026-11-17 as `/trip_bals` (#108), for one trip. The running tab between close family
(`/bals`) is still to build.*
- [ ] Extend as real usage surfaces the need for more flexible splitting
- [x] *Shipped 2026-11-16 in #101 (`share_out` in `app/services/splits.py`).* Split in whole cents, so the parts always add up to the entry's amount (reconciliation
depends on it; the rule is recorded in [ADR-0028](docs/decisions/0028-exact-cent-conversion-half-up.md)). Store each person's share as integer
cents in `transaction_splits`, with a check that the shares sum to the entry. Work the shares out by
rounding every share down and handing the leftover cents out one at a time, never by rounding each
share: SGD 5.55 split two ways would otherwise be 2.78 + 2.78 = 5.56. This replaces the
`split_ratio` Decimal column, which nothing writes, and which a later migration could drop. Who gets a leftover cent must be a fixed
rule, not random, so the same entry always splits the same way. Decided 2026-11-16: the largest
remainders first, ties to the lowest participant id (`docs/superpowers/plans/2026-11-16-splits.md`).
- [ ] A database view that shows each entry's sender by name: `entered_by_participant_id` joined to
the `participants` table from [#53](https://github.com/sujayjangam/unified-ledger-pipeline/issues/53), so the base table keeps storing only the id
([ADR-0033](docs/decisions/0033-entered-by-participant-id.md)) while anything reading the ledger sees names. Possible now that #84 put names in the
`participants` table. Deliberately after Phase 2 (decided 2026-09-12).
- [ ] Trips and groups (design note from #53, 2026-11-11). *Trips moved to Phase 1 on
2026-11-14; the household as a group stays here.* A trip is
a group of participants with start and end dates. Expenses tagged to it are split and
settled within it, and one dated outside the trip gets a non-blocking ⚠️ line on the card, since
post-trip expenses are real. (Migration 0006 gives trips dates, but they only sort and label `/trip`'s buttons: an entry is
tagged by when it is logged, not by its date,
[ADR-0035](docs/decisions/0035-trips-tagged-at-log-time.md),
[ADR-0036](docs/decisions/0036-trip-switch-per-person.md). That warning would be a later
addition.) The household becomes a group with no end date, so splitting is
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
