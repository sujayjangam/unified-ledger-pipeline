# Roadmap

Use GitHub Issues to track fixes, feature pushes, etc. to keep progress clean and trackable.

This document is the single source of truth for where this project stands and where it's going.
Read "Current status" first when resuming work — don't re-derive it by reading the whole repo.
This file supersedes any prior version of ROADMAP.md.

## About blurb (draft — move into README.md once Phase 1/2 ship)

Old description ("A local-first ETL system... sensor-fusion logic") is retired — it's inaccurate
(the data is not local, it's Neon Postgres) and uses borrowed jargon from an unrelated domain that
doesn't map to anything in the actual system. Use, and refine once Phase 1/2 land:

> A cloud-native financial reconciliation pipeline that unifies voice-logged expenses and
> multi-currency bank statements into a single, verifiable ledger — combining deterministic
> rule-based matching with LLM-assisted extraction, backed by a hand-labeled precision/recall
> evaluation harness.

## Current status

**Phase:** Phase 0 — scope extended 2026-08-20, see below. Postgres migration is fully closed out
(see 2026-08-05 below). The scheduled `pg_dump` → GCS backup (#7) is built, infra-verified, and now
restore-verified as of 2026-08-13 — see "What happened today (2026-08-13)" below. #7 is closed.
**Next action:** Capture reliability — [#15](https://github.com/sujayjangam/unified-ledger-pipeline/issues/15)
(backdated date parsing) and [#9](https://github.com/sujayjangam/unified-ledger-pipeline/issues/9)
(timestamp/ordering), then edit/delete and text ingestion. Full ordering in the Phase 0 checklist.

**What happened today (2026-08-20) — extending Phase 0 instead of opening Phase 1:**
- **Capture friction is the binding constraint on ledger data quality**, not capacity or
  reliability. Real usage sits below what the system was built for because entering an expense is
  harder than it should be — voice is the only ingestion path, nothing can be corrected after it's
  saved, and every entry is stamped with today's date regardless of what was actually said.
- This reclassifies two open issues. #15 ("voice notes always get today's date") is not a
  date-parsing nicety: because dates can't be backdated, the system forces logging *at the moment
  of spend*, which is exactly when a voice note is least usable (restaurant, office, public
  transport). #9 (no reliable ordering) is the integrity half of the same problem — what does get
  captured can't be reliably sequenced. Both gate whether captured data is correct and whether
  capture happens at all, so neither is deferrable behind Phase 1.
- **README case study, architecture diagram and demo recording moved out of Phase 5 to the end of
  Phase 0.** They were scheduled last on the assumption that documentation should describe a
  finished system. That assumption has aged badly: the decision log has grown large enough
  (Postgres migration, the `postgresql+psycopg://` scheme trap, WIF over stored service-account
  keys, `-Fc` over plain SQL, the pooled-endpoint restore gotcha) that writing it up while the
  reasoning is fresh is both cheaper and more accurate than reconstructing it several phases later.
  Placing it at the *end* of Phase 0 rather than the start means it documents a working expense
  recorder instead of the current friction-limited one.
- **Pytest suite sequenced immediately before those documentation items.** The suite's job is to
  prove the expense-recording paths actually work — which is the same claim the README makes, so
  the evidence should exist before the claim is published.
- The webhook idempotency deferral is now explicitly time-limited rather than open-ended — its
  justification depends on low volume, and low volume is the thing being fixed. See the Phase 0
  checklist.

**What happened today (2026-08-13) — restore-verifying the scheduled backup (#7):**
- Ran the Neon-branch restore drill from `docs/BACKUP_RESTORE.md`: created a scratch branch,
  emptied `public` via `DROP SCHEMA ... CASCADE` / `CREATE SCHEMA public`, downloaded the latest
  `pg_dump` object from GCS, and `pg_restore`'d it in.
- Verification used `SELECT currency, COUNT(*), SUM(amount) FROM transactions GROUP BY currency`
  instead of the plain `SUM(amount)` in the doc's original example — this is a multi-currency
  ledger with no FX conversion anywhere in the codebase, so an un-grouped `SUM(amount)` mixes
  currencies and is meaningless as a check. Grouped by currency, every value matched between the
  branch and `main` except MYR, which was short exactly one row/amount — the one transaction
  recorded after the backup ran. That mismatch matching the known gap exactly is what confirms the
  dump/restore round-trip is faithful, not corrupting or dropping data.
- Hit a false failure first: ran the "empty the branch" step in the Neon console SQL Editor and the
  restore/verify steps locally via `psql`/`pg_restore` against the pooled (`-pooler`) endpoint. The
  local session couldn't see the restored table at all (`relation "transactions" does not exist`)
  even after a clean `pg_restore` exit, while the console still showed the branch's original
  pre-drop data. Root cause not fully isolated, but redoing the entire drill in one
  session/connection (local `psql`, direct/unpooled endpoint, no console SQL Editor involved)
  resolved it cleanly. `docs/BACKUP_RESTORE.md` updated with this as a documented gotcha.
- Postgres client tools (`psql`, `pg_restore` 18.4 — version-matched to Neon and the pinned
  `pg_dump` client) installed into the `ledger-env` micromamba env for this
  (`micromamba install -n ledger-env -c conda-forge postgresql`); they weren't present locally
  before. Also discovered the project's own `.venv/` is stale/broken (no interpreter) — `ledger-env`
  is the real working environment; `.venv` should be ignored or cleaned up in a future session.

**What happened today (2026-08-12) — building the scheduled backup (#7):**
- Chose GitHub Actions (scheduled `cron`, every 6 hours) over Cloud Scheduler + Cloud Run Job,
  specifically because the repo is public and this project already had one incident from
  invisible GCP-side config (the `DATABASE_URL` secret gap below) — a GCP-side-only backup job
  would repeat that blind spot. GitHub Actions keeps the schedule versioned and reviewable in the
  repo instead.
- Auth via Workload Identity Federation, not a stored service-account key — no long-lived GCP
  credential exists anywhere in GitHub, which matters more than usual since this repo is public.
  Built: GCS bucket `unified-ledger-pg-backups-458614017842` (asia-southeast1,
  uniform-bucket-level-access, public-access-prevention enforced, 30-day Delete lifecycle rule),
  service account `pg-backup-runner` (bucket-scoped `storage.objectAdmin` + secret-scoped
  `secretmanager.secretAccessor` on `DATABASE_URL` only, not project-wide), Workload Identity Pool
  `github-actions-pool` + OIDC provider `github-actions-provider` with an attribute condition
  restricted to this exact repo *and* `refs/heads/main`.
- Dump format: `pg_dump -Fc` (custom format), not plain SQL — chosen for TOC-based inspection
  (`pg_restore -l`) and selective/parallel restore, which starts to matter once the schema grows
  past one table (Phase 1's staging table, Phase 3's `participants`/`transaction_splits` — already
  committed phases, not speculation).
- `.github/workflows/backup.yml` added (first CI/CD file in this repo) and
  `docs/BACKUP_RESTORE.md` added, covering why a Neon branch (not local Postgres) is the right
  restore-drill target, and the manual row-level reconciliation pattern for getting recovered data
  back into production (a `pg_dump` backup is a full-database snapshot, not a per-transaction undo
  tool — production is never `pg_restore`'d into directly).
- Failure alerting deliberately deferred — Phase 1 already plans a Telegram alert for pipeline
  failures; extending it to cover this workflow is future work, not part of this task.
- Verification still open at time of writing: the actual Neon-branch restore drill (download a
  backup, empty a branch, `pg_restore` into it, confirm row count/`SUM(amount)`) hasn't run yet —
  that result is the evidence that actually closes #7, not just the workflow existing.

**What happened today (2026-08-05) — closing the `DATABASE_URL` gap:**
- Created `DATABASE_URL` in Secret Manager and attached it to `unified-ledger-bot`
  (`gcloud run services update --update-secrets`) — same pattern as `OPENAI_API_KEY`/
  `TELEGRAM_BOT_TOKEN`. First deploy (revision `...-00031`) started cleanly.
- First real voice-note confirm attempt then failed with `No module named 'psycopg2'`. Root cause
  wasn't the secret's GUI "type" field (a red herring) — it was the connection string's scheme.
  `requirements.txt` installs `psycopg` (v3), but a plain `postgresql://` URL makes SQLAlchemy
  default to the (uninstalled) `psycopg2` dialect. Fix: `postgresql+psycopg://...`.
- The secret was deleted and recreated with the corrected string, which silently wiped its IAM
  binding (a new secret resource needs its own grant even with the same name) — re-granted
  `roles/secretmanager.secretAccessor` to the Cloud Run runtime service account
  (`458614017842-compute@developer.gserviceaccount.com`), then redeployed (revision `...-00032`),
  clean startup logs.
- Forced one more redeploy (revision `...-00033`) to simulate a restart per the verification
  checklist below — clean startup, no errors.
- A real voice note through the live Cloud Run webhook was confirmed and saved to Neon Postgres
  without error, closing out the last open verification item. Issues
  [#4](https://github.com/sujayjangam/unified-ledger-pipeline/issues/4) and
  [#2](https://github.com/sujayjangam/unified-ledger-pipeline/issues/2) closed as a result. (Note:
  the correct "latest rows" query is `ORDER BY date DESC` — there is no `id` column, and
  `transaction_id` is a random UUID, not sequential.)

**GitHub issues filed today** (tracked in GitHub, not narrated in full here — see the "Roadmap vs.
GitHub issues" note below):
- [#7](https://github.com/sujayjangam/unified-ledger-pipeline/issues/7) — Scheduled pg_dump backup
  to GCS.
- [#9](https://github.com/sujayjangam/unified-ledger-pipeline/issues/9) (parent) — Schema
  conflates business date with system ingestion timestamp → sub-issues
  [#10](https://github.com/sujayjangam/unified-ledger-pipeline/issues/10)-[#14](https://github.com/sujayjangam/unified-ledger-pipeline/issues/14).
- [#15](https://github.com/sujayjangam/unified-ledger-pipeline/issues/15) (parent, sub-issues
  TBD) — Parse relative/backdated transaction dates from voice transcript (covers "yesterday",
  "day before yesterday", "Tuesday last week", and explicit spoken dates).
- #8 (an early single-issue draft of #9's scope) closed as duplicate of #9.

**Note on this file vs. GitHub issues as sources of truth:** as of today, task-level detail (repro
steps, sub-task breakdowns, verification criteria) lives in GitHub issues, not here. This file
should stay the narrative/phase-level layer — current phase, what's blocking, and a pointer to the
relevant issue number — not a second copy of issue bodies. See "Known issues" below, which still
needs a pass to convert into issue links rather than restated detail (open discussion, not done
yet).

**The Cloud Run gap (found 2026-08-03, closed 2026-08-05):** Cloud Run has built-in continuous
deployment from this repo (a GCP-side Cloud Build trigger, invisible to a repo scan), so the
Postgres code auto-deployed on merge — but the service was missing `DATABASE_URL`, so it ran
without a working DB connection until today. Full detail in "What happened today" above.

**Verification checklist for the fix (all items closed, 2026-08-05):**
1. [x] GCP Console → Cloud Run → Revisions: new revision created after the secret is attached, with
   a fresh timestamp — confirmed 3x today (revisions `...00031`, `...00032`, `...00033`).
2. [x] Send one real voice note through the actual bot (the Cloud Run webhook, not local polling)
   and tap Confirm — confirmed saving to Neon Postgres with no error, post-psycopg-driver-fix.
3. [x] Row landed correctly (superseded by #2 — direct Neon SQL console spot-check wasn't needed
   once the webhook round-trip itself confirmed a successful write with no error).
4. [x] Force a restart (deployed a no-op revision `...00033`) — clean startup, and the subsequent
   voice-note save (step 2) confirms data isn't lost across it.
5. [x] Check Cloud Run logs for a clean startup (no `RuntimeError`/`NameError` during boot) —
   clean on all three revisions today.

The ledger runs on Neon Postgres. `app/database.py` is a pooled SQLAlchemy Core engine reading
`DATABASE_URL`, Alembic owns the schema (`alembic/versions/0001_create_transactions_table.py`
records the true live schema including the previously-undocumented `account_desc`), and every
query is `text()` with named binds. All 21 rows migrated with checksums matching the
pre-migration SQLite file exactly (`SUM(amount)` = 100087720, dates 2023-10-01 → 2026-05-18),
verified locally via `python -m app.bot_polling` before merging.

Still to do, unverified from this machine (no `data/` directory present here to check either way):
rename `data/ledger.db` → `data/ledger.db.pre-migration-backup` (gitignored) once confirmed which
machine still has the pre-migration file.

**Also done this session (2026-08-03):**
- PR #5 (`chore/untrack-venv-and-db`) merged into `main` first, via a real merge commit (not
  squash) — `postgres-migration` was built directly on top of that commit, so merging it first
  collapsed PR #6's diff down to just the Postgres-related commits, making it reviewable.
- **Found and fixed a real PII leak**: `.env.example` on the `postgres-migration` branch had real
  names (first name, "Wife") and real bank/card names (OCBC 90N, DBS Altitude, YouTrip) instead of
  placeholders, already pushed to this **public** repo. Rewrote the branch's git history
  (cherry-pick + amend onto `main`, not `rebase -i`) so the real values don't appear in any commit
  reachable from `main`, force-pushed, then merged. A local-only backup branch
  (`postgres-migration-backup-before-rewrite`) still holds the old history if ever needed — never
  pushed anywhere.
- Filed sub-issue #4 ("Redeploy Cloud Run with the Postgres-backed image") under issue #2 via
  GitHub's native sub-issues (`gh issue create --parent 2`). Slightly misnamed in hindsight — see
  "Cloud Run gap" above, the deploy already happens automatically — but it's still the right place
  to track finishing the cutover.
- `gcloud` CLI installed locally (`winget install --id Google.CloudSDK`, v578.0.0), authenticated
  as `jayyjangam117@gmail.com`, active project `project-25d90722-14a9-4eca-8a0`. Existing service:
  `unified-ledger-bot` in `asia-southeast1`.
- Fixed a recurring environment quirk permanently, not just for this project: bare `python`/
  `python3` was hitting a Windows **App Execution Alias** stub pointing at the Microsoft Store
  (this broke `gcloud auth login` too — it's a machine-wide issue, not specific to this repo).
  Disabled via Settings → Apps → Advanced app settings → App execution aliases. Also set
  `CLOUDSDK_PYTHON` (persistent user env var) to the Cloud SDK's bundled Python so `gcloud` doesn't
  depend on the system Python at all going forward.

Environment note for future sessions (**corrected 2026-08-20** — the instruction that used to sit
here told you to activate `.\.venv`, which stopped being true on 2026-08-13; see that entry above):
the working environment is **`ledger-env`**, a micromamba env registered at
`~/AppData/Roaming/mamba/envs/ledger-env` (Python 3.11.15, plus the Postgres client tools). Run
`micromamba activate ledger-env` from the repo root. The project-local `.venv/` is a **stale,
broken leftover** — no interpreter, just two orphaned console scripts — and should be cleaned up
rather than activated. Always run project commands from the repo root: implicit namespace packages
with an `app.` prefix, no `__init__.py` files, and relative paths (`alembic.ini`, `.env`) all
assume it. (The Microsoft Store `python` alias issue above is fixed machine-wide, so a bare
`python` no longer hits a Store stub — activation is still required to get this project's deps.)

[Issue #1](https://github.com/sujayjangam/unified-ledger-pipeline/issues/1) (Telegram double-insert
bug) is fixed and closed as of 2026-07-30.
[Issue #2](https://github.com/sujayjangam/unified-ledger-pipeline/issues/2) (Neon Postgres
migration) and sub-issue
[#4](https://github.com/sujayjangam/unified-ledger-pipeline/issues/4) (Cloud Run redeploy) are
both closed as of 2026-08-05 — the ledger fully runs on Neon Postgres in production, verified via
the checklist above. Open follow-on issues:
[#7](https://github.com/sujayjangam/unified-ledger-pipeline/issues/7) (backup),
[#9](https://github.com/sujayjangam/unified-ledger-pipeline/issues/9) (timestamp/ordering, parent),
[#15](https://github.com/sujayjangam/unified-ledger-pipeline/issues/15) (backdated date parsing,
parent).

`.venv/` (6,427 files, ~30MB) and `data/ledger.db` were untracked from git on 2026-08-01 but
**remain in git history** on `main` — a deliberate open decision, not an oversight. Purging them
needs a history rewrite and force-push, same technique used for the `.env.example` PII fix above,
just deferred for now since nothing in that history is as sensitive as real names/bank data.

## Constraints (agreed, don't relitigate without a reason)

- Timeline: ~10-15 hrs/week, ongoing — the project runs indefinitely as live household
infrastructure.
- Ownership: much of the current codebase was written with AI assistance — ownership is solid on
the simple parts, weaker on async/await, Pydantic schemas, and FastAPI's `lifespan` handling. Close
that gap deliberately in Phase 0, not by skipping the code. This applies to new AI-assisted work
as well as old: nothing gets committed that couldn't be explained, line by line, without the
assistant in the room.
- Intended as live household infrastructure for two real users, not a demo — but **currently below
that bar**, and honestly so. Real usage is limited by capture friction, not by capacity or
reliability. Until that closes (Phase 0), claims about production usage belong in this file as
intent, not as fact, and must not be restated as fact in `README.md`.
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
- Prior art for the matcher: [Actual Budget](https://github.com/actual-budget/actual) runs a
three-stage match — exact imported transaction id, then amount + a ±7-day window + payee, then
amount + the same window ignoring payee. Adopt the *staged* structure rather than a single rule,
but treat the window as a parameter the Phase 2 eval harness tunes, not a constant to copy.
[Firefly III](https://github.com/firefly-iii/firefly-iii) is the other reference point: editing,
reconciliation and rules are first-class there from the start rather than later phases — the
opposite of this project's original ordering, and part of why capture and edit/delete moved
earlier. Neither tool supports Australian or Singaporean bank feeds, which is where this project's
ingestion work is actually differentiated.
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
- Reliability is in scope now, not deferred: automated backups, structured logging, basic CI, a
pytest test suite, and error alerting.
- Packaging: the project's presentation deliverable is a case-study `README.md` — problem →
architecture → key decisions and tradeoffs → what's live today → what's next — plus an inline
Mermaid architecture diagram and a short demo recording. This fixes *what* the deliverable is (not
a docs site, not a blog series, not a slide deck) so it doesn't get redesigned mid-project.
Originally scheduled for Phase 5; moved to the **end of Phase 0** on 2026-08-20.
- Phasing: design backward from the full target architecture — don't bolt features on
incrementally. Phases 0-2 (foundation, reconciliation engine, evaluation harness) are the
differentiated technical core; Phases 3+ (household splitting, budgeting/visibility, packaging)
are real and committed, not cut — this keeps running as live household infrastructure
indefinitely. Scope discovered mid-phase is absorbed by extending that phase, not by inserting
fractional phases or renumbering.

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

Fixed 2026-08-05:

- ~~Cloud Run service missing the `DATABASE_URL` secret~~ — Secret Manager secret created, attached
via `--update-secrets`, corrected to the `postgresql+psycopg://` scheme (this project uses
`psycopg` v3, not `psycopg2`), IAM re-granted after a secret delete/recreate. See "What happened
today" in Current status and the verification checklist below (all items closed).

Fixed 2026-08-07:

- ~~`README.md` currently contains accidental `requirements.txt` content, not real
documentation~~ — rewritten with the corrected About blurb, stack, commands, and links to
`ARCHITECTURE.md`/`docs/`/this file.
- ~~`CLAUDE.md` described the pre-Postgres SQLite schema~~ (`data/ledger.db`, `CREATE TABLE IF NOT
EXISTS`, the `account_desc` drift) a full migration cycle after the Neon Postgres cutover shipped
— updated to describe the actual `app/database.py` engine + Alembic-owned schema, and added a
`DATABASE_URL` entry to its env var list. Also added the `doc-checker` subagent
(`.claude/agents/doc-checker.md`) to catch this class of drift going forward.
- ~~Non-engineering framing was mixed into `ROADMAP.md`~~ ("Why this project exists," "Two
horizons," "Build-in-public track," and other narrative asides, plus two Phase 5 plan items caught
in a follow-up pass) — removed; this file now stays scoped to the engineering plan.

Still outstanding:

- Duplicate Telegram update delivery is deduped only in memory (`_seen_update_ids` in
`bot_webhook.py`), which doesn't survive a Cloud Run restart or a second instance. Now that
Postgres exists, this should become a persisted constraint. Deliberately deferred, not forgotten —
see the Phase 0 checklist note on why usage is currently too low for the race window to matter in
practice.
- Broad `except Exception` blocks throughout silently swallow errors via `print()` instead of
structured logging — failures are invisible in production.
- `needs_review` is extracted by `app/services/extraction.py` but never acted on anywhere — the
human-in-the-loop claim doesn't hold until this actually gates bot behavior.
- No automated test suite. The in-memory-SQLite smoke test written during the migration was
throwaway; it should be turned into real pytest coverage.
- A stray empty `ledger.db` sits at the repo root (untracked, harmless).
- `.venv/` and `data/ledger.db` are untracked as of 2026-08-01 but **still present in git
history** — purging needs a rewrite + force-push, deliberately deferred.
- No reliable "latest transaction" ordering — `transaction_id` is a random UUID and `date` has no
time component. Tracked as [#9](https://github.com/sujayjangam/unified-ledger-pipeline/issues/9)
(parent) + sub-issues #10-#14.
- Voice notes always get today's date regardless of what's said ("yesterday", "last Tuesday",
etc.). Tracked as [#15](https://github.com/sujayjangam/unified-ledger-pipeline/issues/15).
- The backup workflow (`.github/workflows/backup.yml`) has no failure alerting yet — deliberately
deferred to when the Phase 1 "Telegram alert on pipeline failure" item exists, which should be
extended to cover this workflow too, not just the reconciliation pipeline.
- The GCS lifecycle rule's 30-day deletion can't be verified same-day by construction — follow up
in ~30 days (from 2026-08-12) to confirm the oldest backup objects actually age out.
- `.github/workflows/backup.yml` pins its `pg_dump` client to Postgres 18 (`postgresql-client-18`
  from PGDG), matching Neon's actual server version confirmed 2026-08-12 — deliberately not the
  unversioned "latest" package, so behavior doesn't silently drift as PGDG publishes new majors.
  If the Neon project is ever upgraded to a new Postgres major version, this pin needs a matching
  update in the same PR as that upgrade, or backups will start failing (client older than server).

Found by inspection 2026-08-19, none filed as issues yet (the capture-side ones are already
sequenced in the Phase 0 checklist and aren't repeated here):

- No refund or reversal representation anywhere — `app/add_expense.py` rejects `amount <= 0`.
  Phase 1 reconciliation against real statements hits refunds almost immediately, so this is a
  Phase 1 blocker rather than a cosmetic gap.
- `reconciliation_status` is hardcoded to `'unsettled'` in both writers. The field Phase 1 is
  meant to populate currently has no writer at all.
- `benefit_of` and `split_ratio` exist in the schema and in `docs/SCHEMA.md` but are written by no
  code path — Phase 3 will need them, nothing populates them today.
- `docs/SCHEMA.md` says `transaction_type` is `'income'`/`'expense'`; the code writes
  `'Expense'`/`'Transfer'` (`app/services/extraction.py`). The doc and the data disagree, and no
  income path exists at all.
- `app/main.py`'s POST handler omits `currency` entirely, so a row created through the REST API
  would land with a NULL currency and corrupt every currency-grouped aggregate in
  `app/services/ledger_queries.py`. Latent rather than live — the Dockerfile runs `bot_webhook`,
  not `main` — but it's a live landmine for whenever the API is deployed.
- `app/bot_core.py` hardcodes `ACCOUNT_OWNERS["Sujay"][0]`, which raises a bare `KeyError` if that
  key is ever renamed in the env.
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

## Plan

### Phase 0 — Foundation & ownership

- [x] **Postgres migration (Neon)** — code-complete 2026-08-01, merged into `main` 2026-08-03
(SQLAlchemy Core, all SQL converted to `text()` with named binds). Fully live on Cloud Run as of
2026-08-05 — the missing `DATABASE_URL` secret and the `psycopg` scheme bug are both closed, issue
[#4](https://github.com/sujayjangam/unified-ledger-pipeline/issues/4) closed with it.
- [x] Fix the DB-layer "Known issues" as part of the migration, not after it
- [x] Connection pooling — SQLAlchemy `QueuePool`
- [x] Migrations tooling — **Alembic** (schema will keep changing: staging table next phase,
splits tables after that)
- [x] Scheduled logical backup: `pg_dump` → GCS free tier, rolling retention (e.g. 30 days) —
tracked in [#7](https://github.com/sujayjangam/unified-ledger-pipeline/issues/7). Built
2026-08-12 as a GitHub Actions scheduled workflow (`.github/workflows/backup.yml`), not a
GCP-side Cloud Scheduler job — see "What happened today (2026-08-12)" above for why, and
`docs/BACKUP_RESTORE.md` for the restore procedure.

**Ordering within the rest of Phase 0 (set 2026-08-20):** capture reliability first, then
correctness/observability, then the test suite, then packaging. The suite proves the recording
paths work; the packaging items publish that claim, so the evidence is produced before the claim.

**1. Capture reliability** — the binding constraint on data quality; see 2026-08-20 above.

- [ ] Backdated/relative date parsing from the transcript ("yesterday", "last Tuesday", explicit
spoken dates) — [#15](https://github.com/sujayjangam/unified-ledger-pipeline/issues/15). Highest
leverage of the group: without it every entry must be logged at the moment of spend.
- [ ] Business date vs. system ingestion timestamp, and reliable ordering —
[#9](https://github.com/sujayjangam/unified-ledger-pipeline/issues/9) (parent) + sub-issues
[#10](https://github.com/sujayjangam/unified-ledger-pipeline/issues/10)-[#14](https://github.com/sujayjangam/unified-ledger-pipeline/issues/14).
- [ ] Edit and delete path. No `UPDATE` or `DELETE` statement exists anywhere in `app/` — a wrong
extraction is currently permanent, which is also what hollows out the human-in-the-loop claim: the
human is in the loop for a few seconds at confirm time and never again. No issue filed yet.
- [ ] Text ingestion alongside voice. Voice is unusable in most real spending moments (restaurant,
office, public transport), so voice-only capture caps volume by design — [#16](https://github.com/sujayjangam/unified-ledger-pipeline/issues/16).
- [ ] Drop the one-expense-per-voice-note guardrail in `app/bot_core.py` — `TransactionList`
already models multiple; this is a product restriction, not a technical limit.
- [ ] Webhook idempotency — persist the `update_id` dedupe in Postgres instead of process memory.
Previously deferred on the grounds that voice-only ingestion naturally caps volume. **That premise
expires with the items above**: they exist specifically to raise capture volume, so the deferral is
now time-limited rather than open-ended, and this lands in the same phase as the work that
invalidates it — not at some later reliability pass.

**2. Correctness and observability**

- [ ] Structured logging to replace silent `except`/`print` error handling
- [ ] Wire `needs_review` so it actually gates bot behavior (prerequisite for the
human-in-the-loop framing to hold up under questioning)
- [ ] `base_amount` correctness — currently written as the raw amount regardless of currency,
contradicting `docs/SCHEMA.md`. Prefer absent (`NULL`) over silently incorrect until an FX source
exists.

**3. Test suite** — sequenced after the capture work and before packaging, deliberately: its
primary job is to prove every expense-recording path actually works, so it has to run against the
finished capture paths rather than the ones being replaced.

- [ ] Pytest test suite — starts here, grows with each phase (this is a distinct artifact from
the Phase 2 evaluation harness: this is correctness/regression, the harness is match *quality*).
First coverage targets: every ingestion path end to end, `add_expense` validation and the
idempotency-key path, extraction schema parsing, and the period boundaries in
`app/services/utils.py` / `app/services/ledger_queries.py`.
- [ ] Basic CI: lint + test suite on push
- [ ] Deliberate pass through the AI-assisted async/Pydantic/FastAPI-lifespan code — rewrite or
annotate until it can be defended live, not just described

**4. Packaging** — moved here from Phase 5 on 2026-08-20.

- [x] `README.md` placeholder fix
- [ ] Rewrite `README.md` as a case study: problem → architecture → key decisions and tradeoffs →
what's live today → what's next. The standing rule still binds — `README.md` describes only what
has shipped, and forward-looking work sits under an explicit "What's next" heading.
- [ ] Inline Mermaid architecture diagram, authored with the README rewrite
- [ ] Short demo recording → GIF at the top of `README.md`. Last item in the phase: it should show
a working expense recorder, not the friction-limited one. Recording it early against the current
build is still worthwhile as a private friction-finding exercise — it surfaces exactly the UX
problems the capture work above is meant to fix — but the published artifact comes last.

### Phase 1 — Reconciliation engine

*(Blocked on Phase 0's Postgres migration — do not build this against SQLite.)*

- [ ] PDF statement parser: rule-based extraction per bank format (OCBC, DBS, YouTrip) first
- [ ] LLM-assisted extraction as fallback, only for lines the rule-based parser can't handle
- [ ] Receipt image capture (Telegram photo message) as a second ingestion path alongside voice
notes — store the image (GCS) and link it to the transaction row; reuses the rule-based +
LLM-fallback extraction architecture above rather than building a separate one-off pipeline.
- [ ] Normalize parsed statement lines into a staging table (Postgres)
- [ ] Deterministic matcher: amount + date window against existing ledger entries
- [ ] Tie-breaking rule: auto-match only on a unique candidate; 0 or 2+ candidates → `needs_review`
- [ ] Wire matcher output into the `reconciliation_status` field
- [ ] Telegram alert on pipeline failure

### Phase 2 — Evaluation harness

- [ ] Hand-label a golden set of real statement-line → ledger-entry matches/non-matches
- [ ] Score the matcher: precision/recall/F1
- [ ] Tune the date window against the golden set rather than inheriting a constant — Actual
Budget's ±7 days is a starting point to measure, not a value to copy (see Constraints)
- [ ] Expand the pytest suite to cover matcher edge cases surfaced by the golden set
- [ ] Document the methodology

### Phase 3 — Household splitting

- [ ] Schema: `participants` table, `transaction_splits` child table, `split_type` enum
- [ ] Application logic: even-split and one uneven-split mode to start (who-owes-who calculation)
- [ ] Extend as real usage surfaces the need for more flexible splitting

### Phase 4 — Budgeting & visibility

- [ ] Spend-vs-budget view
- [ ] Weekly digest (Telegram)
- [ ] Reconciled vs. unreconciled breakdown

### Phase 5 — Reliability hardening & packaging refresh

The case-study README and architecture diagram moved to the end of Phase 0 on 2026-08-20, so this
phase is no longer the project's first packaging pass — it's the pass that brings the published
material back in line with a system that by then has reconciliation, an eval harness, splitting and
budgeting in it.

- [ ] Refresh the case-study `README.md` and architecture diagram against the shipped Phase 1-4
system — in particular the eval metrics, which don't exist yet at the Phase 0 writeup
- [ ] Error alerting beyond the Phase 1 Telegram pipeline-failure alert, extended to cover
`.github/workflows/backup.yml` (see "Still outstanding")
- [ ] Revisit the deferred git-history purge of `.venv/` and `data/ledger.db`

## How to resume a session

1. Read this file's "Current status" section first.
2. Check `git log --oneline -10` for what actually landed since status was last updated (this file
can drift from reality if an update was forgotten).
3. Only read the specific files relevant to the next unchecked task — not the whole repo.
4. Before ending a session, update "Current status" and check off finished tasks.
