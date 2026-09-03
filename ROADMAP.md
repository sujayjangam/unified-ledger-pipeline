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

**Phase:** Phase 0 — Foundation & ownership. Scope was extended on 2026-08-20 to cover capture
friction before moving to Phase 1; see
[ADR-0017](docs/decisions/0017-extend-phase-0-for-capture-friction.md).

**Shipped:** the Neon Postgres migration ([#2](https://github.com/sujayjangam/unified-ledger-pipeline/issues/2)) and Cloud Run cutover ([#4](https://github.com/sujayjangam/unified-ledger-pipeline/issues/4)); the
6-hourly `pg_dump` → GCS backup ([#7](https://github.com/sujayjangam/unified-ledger-pipeline/issues/7)), infra-verified and restore-verified 2026-08-13;
text ingestion ([#16](https://github.com/sujayjangam/unified-ledger-pipeline/issues/16)), 2026-08-21; CI on pull requests ([#32](https://github.com/sujayjangam/unified-ledger-pipeline/issues/32)), 2026-08-26;
the case-study `README.md` rewrite with inline Mermaid architecture diagram (Phase 0 §4),
2026-08-29 — pulled ahead of the remaining capture work deliberately, since it only claims
what has already shipped; the demo GIF stays gated on capture reliability; §3a in full
([#31](https://github.com/sujayjangam/unified-ledger-pipeline/issues/31) parent,
[#32](https://github.com/sujayjangam/unified-ledger-pipeline/issues/32),
[#33](https://github.com/sujayjangam/unified-ledger-pipeline/issues/33),
[#34](https://github.com/sujayjangam/unified-ledger-pipeline/issues/34)), 2026-09-03 — the pytest
suite (money conversion, extraction schema parsing, period boundaries, payment-method/account-owner
inference, `is_authorized`, and handler routing, wired into the CI workflow, with the "a broken
assertion turns the check red" criterion verified live in CI, not assumed), and `main` is now a
repository ruleset requiring `check-PR-before-merge`, verified against a throwaway PR rather than
assumed from the settings page (see
[ADR-0021](docs/decisions/0021-rulesets-over-classic-branch-protection.md) for why a ruleset
rather than classic branch protection, and a since-fixed discrepancy in #34's own verification
text).

**Next action:** back to capture, per the original ordering: [#15](https://github.com/sujayjangam/unified-ledger-pipeline/issues/15)
(backdated date parsing), [#9](https://github.com/sujayjangam/unified-ledger-pipeline/issues/9) (business date vs. ingestion timestamp), then the
pending-transaction edit path. Full ordering in the Phase 0 checklist below.

**Open top-level issues:** [#9](https://github.com/sujayjangam/unified-ledger-pipeline/issues/9) ordering · [#15](https://github.com/sujayjangam/unified-ledger-pipeline/issues/15) backdated dates ·
[#17](https://github.com/sujayjangam/unified-ledger-pipeline/issues/17) unused REST API · [#22](https://github.com/sujayjangam/unified-ledger-pipeline/issues/22) entries can't be corrected ·
[#27](https://github.com/sujayjangam/unified-ledger-pipeline/issues/27) unpinned dependencies ·
[#29](https://github.com/sujayjangam/unified-ledger-pipeline/issues/29) double-tap Confirm shows a
false error.
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

### Environment

The working environment is **`ledger-env`**, a micromamba env at
`~/AppData/Roaming/mamba/envs/ledger-env` (Python 3.11.15, plus the Postgres client tools). Run
`micromamba activate ledger-env`. The project-local `.venv/` is a **stale, broken leftover** —
never activate it.

Always run project commands **from the repo root**: implicit namespace packages with an `app.`
prefix, no `__init__.py` files, and relative paths (`alembic.ini`, `.env`) all assume it.

Local testing must not use the production bot token or `DATABASE_URL` — `run_polling()` deletes
the live webhook, and there is no delete path for rows written by mistake. See
[ADR-0019](docs/decisions/0019-separate-bot-token-for-local-testing.md).


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
- Statement format to support: **PDF exports** (OCBC, DBS, YouTrip and similar), chosen
deliberately over cleaner CSV exports — [ADR-0016](docs/decisions/0016-pdf-statements-over-csv.md).
- Matching approach: deterministic rules first, scored against a hand-labeled eval set; auto-match
only on a *unique* candidate, anything ambiguous routes to `needs_review` —
[ADR-0015](docs/decisions/0015-deterministic-matching-before-llm.md). Revisit once real-world
testing surfaces edge cases.
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
Built as [ADR-0012](docs/decisions/0012-github-actions-over-cloud-scheduler.md) /
[ADR-0014](docs/decisions/0014-pg-dump-custom-format.md).
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

Fixed 2026-09-03:

- ~~No automated test suite, and no CI: a change is verified only by a person running the bot by
hand once, and nothing gates a merge~~ — CI now runs lint, an import smoke check, and a pytest
suite on every PR, and `main` is a repository ruleset that refuses a merge whose
`check-PR-before-merge` check failed (admin bypass retained; not required reviewers/signed
commits/CODEOWNERS, deliberately out of scope). Tracked as
[#31](https://github.com/sujayjangam/unified-ledger-pipeline/issues/31) (parent, now closed) via
[#32](https://github.com/sujayjangam/unified-ledger-pipeline/issues/32),
[#33](https://github.com/sujayjangam/unified-ledger-pipeline/issues/33),
[#34](https://github.com/sujayjangam/unified-ledger-pipeline/issues/34). Migration-against-a-real-
database and idempotency-path coverage remain unaddressed — see the §3a checklist below.

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
- ~~`app/bot_core.py` hardcodes `ACCOUNT_OWNERS["Sujay"][0]`, which raises a bare `KeyError` if
  that key is ever renamed in the env~~ — fixed 2026-09-03: the YouTrip-top-up funder is now the
  configurable `PRIMARY_ACCOUNT_OWNER` env var, not a literal name, as part of extracting
  `apply_payment_defaults()` for #33's test suite work.
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

Found 2026-09-03, while reviewing #33's test suite PRs, not yet actioned:

- `app/services/extraction.py`'s `ALLOWED_ACCOUNTS` default and the YouTrip-top-up prompt text
  both say `OCBC Infinity`, which is the wrong card name — it should be `OCBC Rewards`. Needs a
  code fix (also touches the `OCBC Infinity` example value in `tests/test_extraction.py`) *and* a
  one-off correction of existing rows already written with `OCBC Infinity` in the live Neon
  database — the latter needs a specific, reviewed plan before running, not an ad hoc `UPDATE`.
- Candidate future work: move payment methods/account owners (currently `ACCOUNT_OWNERS`,
  hand-maintained JSON in `.env`) and categories into queryable/updatable Postgres tables instead
  of static env-var config, so they can change without a redeploy, and so a user editing a
  category could feed that back into future extraction automatically. This overlaps with Phase
  2's already-planned auto-categorisation work below, which is the more natural home for it — see
  that section rather than treating this as separate scope. Rough latency read: a lookup against
  a small, indexed reference table adds low-single-digit milliseconds, negligible next to the
  existing OpenAI extraction call (500ms–2s) — not yet benchmarked against a real implementation.

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

**Ordering within the rest of Phase 0 (set 2026-08-20, amended 2026-08-21):** capture reliability
first, then correctness/observability, then the test suite, then packaging. The suite proves the
recording paths work; the packaging items publish that claim, so the evidence is produced before
the claim.

Amended 2026-08-21: the CI scaffolding and the tests that don't depend on the capture paths move
*ahead* of the rest of capture reliability — see §3a for why the original reasoning doesn't reach
them. Everything else keeps its place.

**1. Capture reliability** — the binding constraint on data quality; see 2026-08-20 above.

- [ ] Backdated/relative date parsing from the transcript ("yesterday", "last Tuesday", explicit
spoken dates) — [#15](https://github.com/sujayjangam/unified-ledger-pipeline/issues/15). Highest
leverage of the group: without it every entry must be logged at the moment of spend.
- [ ] Business date vs. system ingestion timestamp, and reliable ordering —
[#9](https://github.com/sujayjangam/unified-ledger-pipeline/issues/9) (parent) + sub-issues
[#10](https://github.com/sujayjangam/unified-ledger-pipeline/issues/10)-[#14](https://github.com/sujayjangam/unified-ledger-pipeline/issues/14).
- [ ] Edit and delete path for *saved* rows — [#22](https://github.com/sujayjangam/unified-ledger-pipeline/issues/22).
No `UPDATE` or `DELETE` statement exists anywhere in `app/`, so a wrong extraction is permanent,
which is also what hollows out the human-in-the-loop claim: the human is in the loop for a few
seconds at confirm time and never again. Needs a hard-vs-soft-delete decision first, which is what
separates it from the two `context.user_data`-only recovery paths below.
- [x] Text ingestion alongside voice. Voice is unusable in most real spending moments (restaurant,
office, public transport), so voice-only capture caps volume by design — [#16](https://github.com/sujayjangam/unified-ledger-pipeline/issues/16).
Shipped 2026-08-21: `handle_voice` and the new `handle_text` both feed one shared
`process_expense_text`, and a third handler replies to input types the bot can't read instead of
dropping them silently.
- [ ] Pending-transaction **edit** path — the confirm card should be Confirm/Edit/Cancel, so a wrong
extraction can be corrected before saving rather than only accepted whole or discarded. This is
`context.user_data` state only, no DB write, which is what makes it separable from
[#22](https://github.com/sujayjangam/unified-ledger-pipeline/issues/22) (editing *saved* rows, which
needs a hard-vs-soft-delete decision first). The field picker built here is reusable for #22. No
issue filed yet.
- [ ] Missing-amount recovery — when extraction returns no amount, `process_expense_text` abandons
the entry with a text prompt, so the user has to start over from scratch. Offer
`[Manual Entry]` / `[New Voice Note]` buttons instead, keeping the raw text already captured.
Specified but never built in the MVP flow doc that
[#28](https://github.com/sujayjangam/unified-ledger-pipeline/issues/28) retired — salvaged here
before deleting it, so the idea outlives the file. Belongs with the edit path above: both are
recovery paths over `context.user_data` with no DB write. No issue filed yet.
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

**3. Test suite and CI** — split in three on 2026-08-21. This section originally sat entirely
after capture reliability, on the grounds that the suite's primary job is to prove every
expense-recording path works, so it should run against the finished capture paths rather than the
ones being replaced. That reasoning holds — but only for end-to-end ingestion coverage. It doesn't
reach the CI scaffolding, or tests over code the capture work never touches. Meanwhile nothing
stops a merge that breaks the bot on startup from reaching production: `main` is unprotected and
the deploy trigger is GCP-side and invisible from this repo (see
[ADR-0012](docs/decisions/0012-github-actions-over-cloud-scheduler.md), written about exactly that
blind spot). So the parts that don't depend on capture move ahead of it, and the parts that do stay
where they were.

The pytest suite starts here and grows with each phase. It is a distinct artifact from the Phase 2
evaluation harness: this is correctness/regression, the harness is match *quality*.

**3a. Before the rest of §1** — scaffolding and stable-target tests, tracked as
[#31](https://github.com/sujayjangam/unified-ledger-pipeline/issues/31) (parent), with
[#32](https://github.com/sujayjangam/unified-ledger-pipeline/issues/32),
[#33](https://github.com/sujayjangam/unified-ledger-pipeline/issues/33) and
[#34](https://github.com/sujayjangam/unified-ledger-pipeline/issues/34) as the minimum slice.

**This is a soft gate, deliberately.** It stops a merge that fails to load — syntax errors, bad
imports, a missing dependency, an import-time crash — which is the class that takes the deployed
bot down on startup. It does **not** catch a handler that throws at runtime, a malformed query, or
a broken prompt, and a green check must not be read as "the bot works". Coverage of the
expense-recording paths themselves is §3b, and waits until the `area:capture` work is finished so
it tests the paths that survive rather than the ones being replaced.

Bounded deliberately to what the capture work won't rewrite:

- [x] CI on pull request: clean install from `requirements.txt`, lint, and an import smoke check
across `app/`. Shipped 2026-08-26 in `.github/workflows/ci.yml`
([#32](https://github.com/sujayjangam/unified-ledger-pipeline/issues/32)). The import check is what
catches a merge that would take the deployed bot down on startup; the clean install doubles as the
evidence [#27](https://github.com/sujayjangam/unified-ledger-pipeline/issues/27) needs.
- [x] Pytest harness, with test-only dependencies in a separate `requirements-dev.txt` so
`requirements.txt` keeps meaning "what production needs" — otherwise the check above can't answer
#27. Shipped 2026-09-03: `pytest`/`pytest-asyncio` pinned in `requirements-dev.txt`, and
`.github/workflows/ci.yml` installs it and runs `pytest` on every PR
([#46](https://github.com/sujayjangam/unified-ledger-pipeline/pull/46)) — verified live in CI, not
assumed, by deliberately breaking an assertion and watching the check go red, then reverting it.
- [x] Stable-target tests: money conversion, the period boundaries in `app/services/utils.py` /
`app/services/ledger_queries.py`, handler routing, and extraction schema parsing against recorded
response JSON. No network and no billable API calls anywhere in the suite. Shipped 2026-09-03
across [#38](https://github.com/sujayjangam/unified-ledger-pipeline/pull/38),
[#40](https://github.com/sujayjangam/unified-ledger-pipeline/pull/40)-[#42](https://github.com/sujayjangam/unified-ledger-pipeline/pull/42),
[#44](https://github.com/sujayjangam/unified-ledger-pipeline/pull/44)-[#46](https://github.com/sujayjangam/unified-ledger-pipeline/pull/46)
(one category per PR); also covers `is_authorized` and payment-method/account-owner inference,
beyond #33's original minimum slice.
- [ ] Migrations apply from scratch (`alembic upgrade head` against a throwaway Postgres service
container), and the `ON CONFLICT (idempotency_key)` path against a real database — explicitly out
of scope for #33 ("not part of the minimum slice"), still not covered by anything in `tests/`.
`add_expense`'s pure-validation logic (amount conversion/rejection) is covered by
`tests/test_add_expense.py`.
- [x] Branch protection on `main` requiring `check-PR-before-merge`, enabled only once it had run
green on real PRs. Shipped 2026-09-03 as a repository ruleset (not classic branch protection — see
[ADR-0021](docs/decisions/0021-rulesets-over-classic-branch-protection.md)) with an admin bypass
list, verified against a throwaway PR (#47): `mergeStateStatus` was `BLOCKED` and `gh pr merge` was
rejected while the check failed, then flipped to `CLEAN` once fixed
([#34](https://github.com/sujayjangam/unified-ledger-pipeline/issues/34)).

**3b. After §1 completes** — coverage that has to run against the finished capture paths:

- [ ] Every ingestion path end to end, against the capture paths as §1 leaves them — not the ones
being replaced.
- [ ] Extend routing coverage to the callback handlers the pending-transaction edit path adds.

**3c. Ownership pass** — not a testing task, and it needs to survive §3a/§3b being ticked off:

- [ ] Deliberate pass through the AI-assisted async/Pydantic/FastAPI-lifespan code — rewrite or
annotate until it can be defended live, not just described.

**4. Packaging** — moved here from Phase 5 on 2026-08-20.

- [x] `README.md` placeholder fix
- [x] Rewrite `README.md` as a case study: problem → architecture → key decisions and tradeoffs →
what's live today → what's next. Done 2026-08-29. The standing rule still binds — `README.md`
describes only what has shipped, and forward-looking work sits under an explicit "What's next"
heading.
- [x] Inline Mermaid architecture diagram, authored with the README rewrite — done 2026-08-29,
one flowchart covering the capture pipeline and the scheduled backup pipeline
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
- [ ] **Auto-categorisation rules** — derive `token → category` rules from confirmed
`transactions` history (`description` holds the raw input, `category` holds the human-confirmed
answer), so common entries resolve without an LLM call at all. Sits in this phase rather than
Phase 0 because the support/purity thresholds want the eval harness to tune them rather than being
guessed. Note nothing in the codebase consults history for anything today — the `ACCOUNT_OWNERS`
reverse-lookup in `bot_core.py` is the same *shape* but is hand-maintained from env, not derived.
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
