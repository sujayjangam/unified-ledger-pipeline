# Roadmap

Use GitHub Issues to track fixes, feature pushes, etc. to keep progress clean and trackable.

This document is the single source of truth for where this project stands and where it's going.
Read "Current status" first when resuming work — don't re-derive it by reading the whole repo.
This file supersedes any prior version of ROADMAP.md.

## Why this project exists (two goals, one codebase)

1. A live financial tool: my wife and I actually use this to log and manage household expenses,
and intend to keep using it for years. This is not a demo that gets abandoned after the job hunt —
reliability and correctness matter as much as portfolio signal, indefinitely.
2. A portfolio piece to land a GenAI/ML/Data Engineering role in Perth's enterprise market
($120k+, e.g. BHP/Rio Tinto tier). It needs to demonstrate real depth — not "just data
extraction" — and skills that don't evaporate as AI tooling improves.

Both goals point the same direction, so priorities are set by genuine usefulness first — that
naturally produces the stronger interview story too.

## About (for README / LinkedIn / GitHub description)

Old description ("A local-first ETL system... sensor-fusion logic") is retired — it's inaccurate
(the data is not local, it's Neon Postgres) and uses borrowed jargon from an unrelated domain that
doesn't map to anything in the actual system. Use, and refine once Phase 1/2 land:

> A cloud-native financial reconciliation pipeline that unifies voice-logged expenses and
> multi-currency bank statements into a single, verifiable ledger — combining deterministic
> rule-based matching with LLM-assisted extraction, backed by a hand-labeled precision/recall
> evaluation harness.

## Current status

**Phase:** Phase 0 — Postgres migration is **code-complete but not yet cut over** (as of
2026-08-01).
**Next action:** Provision the Neon project, then run the cutover + verification sequence below.
Nothing else in Phase 0 should start until the ledger is actually reading and writing from
Postgres.

The code no longer talks to SQLite: `app/database.py` is a pooled SQLAlchemy Core engine reading
`DATABASE_URL`, Alembic owns the schema (`alembic/versions/0001_create_transactions_table.py`
records the true live schema including the previously-undocumented `account_desc`), and every
query is `text()` with named binds. Verified against in-memory SQLite — including that
`ON CONFLICT` dedupe inserts exactly one row for a repeated `idempotency_key` — but **not yet
against real Postgres**.

Remaining, in order:
1. Create the Neon project, take the **direct/unpooled** connection string, put it in `.env` as
   `DATABASE_URL` (see `.env.example`).
2. `alembic upgrade head`.
3. `python -m scripts.migrate_to_postgres` — copies the 21 live rows, verifies counts and amount
   checksums, exits non-zero on any mismatch. Expected: 21 rows, `SUM(amount)` = 100087720,
   dates 2023-10-01 → 2026-05-18.
4. Work through the verification list in the session plan: `view_ledger`, a throwaway
   `add_expense`, a **real duplicate-`idempotency_key` test against Postgres** (the one behavior
   that's genuinely driver-dependent), the bot's `/recent /today /week /month /cat_today` under
   `bot_polling`, one real voice note end-to-end, and both FastAPI routes.
5. Only then rename `data/ledger.db` → `data/ledger.db.pre-migration-backup` (already gitignored).

[Issue #1](https://github.com/sujayjangam/unified-ledger-pipeline/issues/1) (Telegram double-insert
bug) is fixed and closed as of 2026-07-30. Issue #2 (Neon Postgres migration) is in progress.

Also cleaned up this session: `.venv/` (6,427 files, ~30MB) and `data/ledger.db` were both tracked
in git and are now untracked — **but they remain in git history**, which is a deliberate open
decision, not an oversight. Purging them needs a history rewrite and force-push.

## Constraints (agreed, don't relitigate without a reason)

- Target: GenAI/ML/Data Engineering in Perth, open to any of the three — explicitly not a project
that reads as "just data extraction."
- Timeline: ~5-10 hrs/week. Two horizons, not one deadline (see "Two horizons" below).
- Background: SQL + a Python bootcamp finished ~3 months ago, no formal CS/DS training.
Comfortable with Git/GitHub (branches, PRs, CI) and basic ML libraries (scikit-learn, pandas).
Much of the current codebase was written with AI assistance — ownership is solid on the simple
parts, weaker on async/await, Pydantic schemas, and FastAPI's `lifespan` handling. Close that gap
deliberately in Phase 0, not by skipping the code.
- This is live infrastructure for two real users, not a demo.
- Real pain points, in priority order: (1) reconciling voice-logged entries against real bank/card
statements, (2) household expense splitting, (3) budgeting & visibility. FX conversion is *not* a
priority — statements already show converted rates.
- Statement format to support: **PDF exports** (OCBC, DBS, YouTrip and similar) — chosen
deliberately over cleaner CSV exports because messy real-world PDFs make a stronger
document-extraction story.
- Matching approach: deterministic rules (amount + date window) first, backed by a hand-labeled
eval set reporting precision/recall — not semantic/LLM-based matching from day one. Auto-match
only on a *unique* candidate hit; anything ambiguous (0 or 2+ candidates) routes to
`needs_review` rather than guessing. Revisit this rule once real-world testing surfaces edge cases.
- PDF parsing strategy: rule-based extraction per bank format first (layouts are fairly
consistent within a bank); LLM-assisted extraction only as a fallback for lines the rules can't
parse. Cheaper, and a stronger interview story than "always call the LLM."
- Budget: infra stays on free tiers (GCP Cloud Run, Neon Postgres free tier, GCS free tier). A
small, usage-based OpenAI spend is acceptable (already paying for voice-to-text; PDF fallback
extraction is the same category of cost) — explore free/local extraction first, LLM fallback
second.
- Data durability: Neon's free-tier point-in-time restore is a rolling **6-hour window, capped at
1GB of changes** — it protects against "I just made a mistake," not against account/provider
issues. Do not treat it as a backup. A scheduled logical backup (`pg_dump` → GCS free tier) is a
required Phase 0/1 deliverable, not optional, given the years-long intended lifetime of this data.
- Reliability is in scope now, not deferred: automated backups, structured logging, basic CI, a
pytest test suite, and error alerting.
- Packaging: rewrite `README.md` as a case study with an architecture diagram. Build-in-public
content runs continuously from Phase 0 onward (see "Build-in-public track" below), not bolted on
at the end.

## Two horizons

Design backward from the full target architecture — don't bolt features on incrementally — but
track two different deadlines against it:

- **v1 / interview-ready (Phases 0-2):** foundation, reconciliation engine, evaluation harness.
This is the differentiated technical story and should be solid before applications start.
- **Ongoing (Phases 3+):** household splitting, budgeting/visibility, packaging. Real, committed,
not cut — this is because the project keeps running as live household infrastructure regardless
of the job search timeline. It just doesn't gate when applications begin.

## Known issues

Fixed as part of the Postgres migration (2026-08-01):

- ~~`app/database.py`'s `CREATE TABLE` is missing the `account_desc` column~~ — the Alembic
baseline now records the true schema, and `test_queries.py` (the ad hoc `ALTER TABLE` that caused
the drift) is deleted.
- ~~`app/bot_core.py`: on `JSONDecodeError`, the except block sets `TG_USERS` instead of
`ALLOWED_TG_IDS`~~.
- ~~`app/sample_data.py` imports `from database import get_connection`~~ — import fixed, and its
positional `INSERT` (which silently depended on column order) now names its columns.
- ~~No connection pooling anywhere `get_connection()` is used~~ — SQLAlchemy `QueuePool`,
`pool_size=5`, `pool_pre_ping=True` for Neon's idle auto-suspend.
- ~~No migrations tooling~~ — Alembic, with the convention that migrations are hand-written
(Core, not ORM, so there's no metadata for `--autogenerate` to diff against).

Still outstanding:

- Duplicate Telegram update delivery is deduped only in memory (`_seen_update_ids` in
`bot_webhook.py`), which doesn't survive a Cloud Run restart or a second instance. Now that
Postgres exists, this should become a persisted constraint.
- Broad `except Exception` blocks throughout silently swallow errors via `print()` instead of
structured logging — failures are invisible in production.
- `needs_review` is extracted by `app/services/extraction.py` but never acted on anywhere — the
human-in-the-loop claim doesn't hold until this actually gates bot behavior.
- No automated test suite. The in-memory-SQLite smoke test written during the migration was
throwaway; it should be turned into real pytest coverage.
- `README.md` currently contains accidental `requirements.txt` content, not real documentation.
- A stray empty `ledger.db` sits at the repo root (untracked, harmless).
- `.venv/` and `data/ledger.db` are untracked as of 2026-08-01 but **still present in git
history** — purging needs a rewrite + force-push, deliberately deferred.
- Cloud Run still runs against the old SQLite code path until the deployment session lands; until
then production transactions are still lost on restart/redeploy.

## Plan

### Phase 0 — Foundation & ownership

- [x] **Postgres migration (Neon)** — code-complete 2026-08-01 (SQLAlchemy Core, all SQL converted
to `text()` with named binds). **Cutover to the real Neon instance still pending** — see
"Current status".
- [x] Fix the DB-layer "Known issues" as part of the migration, not after it
- [x] Connection pooling — SQLAlchemy `QueuePool`
- [x] Migrations tooling — **Alembic** (schema will keep changing: staging table next phase,
splits tables after that)
- [ ] Webhook idempotency — persist the `update_id` dedupe in Postgres instead of process memory
- [ ] Structured logging to replace silent `except`/`print` error handling
- [ ] Wire `needs_review` so it actually gates bot behavior (prerequisite for the
human-in-the-loop framing to hold up under questioning)
- [ ] Scheduled logical backup: `pg_dump` → GCS free tier, rolling retention (e.g. 30 days)
- [ ] Pytest test suite — starts here, grows with each phase (this is a distinct artifact from
the Phase 2 evaluation harness: this is correctness/regression, the harness is match *quality*)
- [ ] Basic CI: lint + test suite on push
- [ ] Deliberate pass through the AI-assisted async/Pydantic/FastAPI-lifespan code — rewrite or
annotate until it can be defended live, not just described
- [ ] `README.md` placeholder fix (full case-study rewrite happens in Phase 5)
- [ ] Profile readiness (see "Before Phase 0 posting begins" below) — do this in parallel, it
gates the build-in-public track, not the engineering work

### Phase 1 — Reconciliation engine

*(Blocked on Phase 0's Postgres migration — do not build this against SQLite.)*

- [ ] PDF statement parser: rule-based extraction per bank format (OCBC, DBS, YouTrip) first
- [ ] LLM-assisted extraction as fallback, only for lines the rule-based parser can't handle
- [ ] Normalize parsed statement lines into a staging table (Postgres)
- [ ] Deterministic matcher: amount + date window against existing ledger entries
- [ ] Tie-breaking rule: auto-match only on a unique candidate; 0 or 2+ candidates → `needs_review`
- [ ] Wire matcher output into the `reconciliation_status` field
- [ ] Telegram alert on pipeline failure

### Phase 2 — Evaluation harness

- [ ] Hand-label a golden set of real statement-line → ledger-entry matches/non-matches
- [ ] Score the matcher: precision/recall/F1
- [ ] Expand the pytest suite to cover matcher edge cases surfaced by the golden set
- [ ] Document the methodology — this is the headline interview artifact

### Phase 3 — Household splitting

- [ ] Schema: `participants` table, `transaction_splits` child table, `split_type` enum
- [ ] Application logic: even-split and one uneven-split mode to start (who-owes-who calculation)
- [ ] Extend as real usage surfaces the need for more flexible splitting

### Phase 4 — Budgeting & visibility

- [ ] Spend-vs-budget view
- [ ] Weekly digest (Telegram)
- [ ] Reconciled vs. unreconciled breakdown

### Phase 5 — Packaging & capstone

- [ ] Rewrite `README.md` as a case study: problem, architecture, decisions, eval metrics, what's
next
- [ ] Architecture diagram
- [ ] Consolidate the build-in-public series into a linked wrap-up post
- [ ] Rehearse the verbal narrative

## Build-in-public track (continuous, starts in Phase 0)

Runs alongside every phase from the start — documented as decisions happen, not reconstructed
afterward. Real-time "here's what went wrong and how I fixed it" reads as more credible than a
retrospective.

- **Format:** short-form posts by default (low friction, steady visibility). Full-length blog
posts reserved for Phase 1 (PDF parsing strategy) and Phase 2 (eval methodology) — the two genuine
differentiators worth the depth.
- **Phase 0:** the audit-and-halt story — found N critical bugs, stopped feature work to fix
foundations before building on top of them. Good engineering-maturity signal.
- **Phase 1:** rule-based-first vs. LLM-fallback decision, real messy-statement examples (full blog).
- **Phase 2:** eval methodology, precision/recall results (full blog, flagship post).
- **Phase 3:** splitting-schema design tradeoffs.
- **Phase 4:** budgeting/dashboard build.
- **Phase 5:** wrap-up post linking the series, alongside the README case study.

### Before Phase 0 posting begins

LinkedIn, resume, and profile photo are currently outdated. Do this before the first public post,
not in parallel with it — recruiters who click through from a post to a stale profile is a wasted
impression.

- [ ] LinkedIn photo, headline, and About section updated
- [ ] Resume tailored to Perth DE/GenAI roles, reflecting this project once Phase 0-1 have shipped
- [ ] GitHub profile README (if using one) reflects current focus

## How to resume a session

1. Read this file's "Current status" section first.
2. Check `git log --oneline -10` for what actually landed since status was last updated (this file
can drift from reality if an update was forgotten).
3. Only read the specific files relevant to the next unchecked task — not the whole repo.
4. Before ending a session, update "Current status" and check off finished tasks.
