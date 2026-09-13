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

**Shipped** (the linked issues and ADRs hold the detail):

- Neon Postgres migration ([#2](https://github.com/sujayjangam/unified-ledger-pipeline/issues/2)) and Cloud Run cutover ([#4](https://github.com/sujayjangam/unified-ledger-pipeline/issues/4)), 2026-08-05
- 6-hourly `pg_dump` → GCS backup ([#7](https://github.com/sujayjangam/unified-ledger-pipeline/issues/7)), restore-verified 2026-08-13
- Text ingestion ([#16](https://github.com/sujayjangam/unified-ledger-pipeline/issues/16)), 2026-08-21
- CI on pull requests ([#32](https://github.com/sujayjangam/unified-ledger-pipeline/issues/32)), 2026-08-26
- Case-study `README.md` with an inline Mermaid diagram, 2026-08-29 — ahead of the remaining
  capture work, since it only claims what has shipped; the demo GIF still waits for capture
  reliability
- The pytest suite, and `main` protected by a ruleset that requires it to pass
  ([#31](https://github.com/sujayjangam/unified-ledger-pipeline/issues/31) via #32-#34,
  [ADR-0021](docs/decisions/0021-rulesets-over-classic-branch-protection.md)), 2026-09-03
- Confirm double-tap fix ([#29](https://github.com/sujayjangam/unified-ledger-pipeline/issues/29), [#55](https://github.com/sujayjangam/unified-ledger-pipeline/issues/55)), 2026-09-10
- `created_at` write-time column, with `/recent` ordered by it ([#9](https://github.com/sujayjangam/unified-ledger-pipeline/issues/9) via #10-#14,
  [ADR-0024](docs/decisions/0024-created-at-write-time-column.md)), 2026-09-10
- Authenticated webhook deliveries ([#63](https://github.com/sujayjangam/unified-ledger-pipeline/issues/63), [ADR-0025](docs/decisions/0025-webhook-secret-token.md)), 2026-09-11
- `entered_by`, recording who sent each entry ([#60](https://github.com/sujayjangam/unified-ledger-pipeline/issues/60), [ADR-0026](docs/decisions/0026-entered-by-telegram-user-id.md)), 2026-09-11
- Duplicate-entry warning ([#58](https://github.com/sujayjangam/unified-ledger-pipeline/issues/58), closing [#57](https://github.com/sujayjangam/unified-ledger-pipeline/issues/57), [ADR-0027](docs/decisions/0027-duplicate-warning-before-the-card.md)), 2026-09-12
- Exact cent conversion, half a cent rounding up, one conversion shared by the bot, CLI and REST
  API ([#39](https://github.com/sujayjangam/unified-ledger-pipeline/issues/39), [ADR-0028](docs/decisions/0028-exact-cent-conversion-half-up.md)), 2026-09-13

**In review (2026-09-13):** cleanup from a whole-repo bloat review — dead and risky files removed
([#68](https://github.com/sujayjangam/unified-ledger-pipeline/pull/68)), and `CLAUDE.md` /
`ROADMAP.md` trimmed.

**Next action:** the Confirm / Edit / Cancel card ([#66](https://github.com/sujayjangam/unified-ledger-pipeline/issues/66), the first step of [#22](https://github.com/sujayjangam/unified-ledger-pipeline/issues/22)),
then Phase 1. [#15](https://github.com/sujayjangam/unified-ledger-pipeline/issues/15) (backdated date parsing) is not urgent and waits (decided 2026-09-12).
Full ordering in the Phase 0 checklist below.

**Open top-level issues:** [#15](https://github.com/sujayjangam/unified-ledger-pipeline/issues/15) backdated dates ·
[#17](https://github.com/sujayjangam/unified-ledger-pipeline/issues/17) unused REST API · [#22](https://github.com/sujayjangam/unified-ledger-pipeline/issues/22) entries can't be corrected ·
[#27](https://github.com/sujayjangam/unified-ledger-pipeline/issues/27) unpinned dependencies ·
[#61](https://github.com/sujayjangam/unified-ledger-pipeline/issues/61) git-history purge (Phase 5) ·
[#53](https://github.com/sujayjangam/unified-ledger-pipeline/issues/53)
household accounts live in env secrets (Phase 1).
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
`micromamba activate ledger-env`; there is no project-local environment.

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
see the Phase 0 checklist note on why usage is currently too low for the race window to matter in
practice.
- Broad `except Exception` blocks throughout silently swallow errors via `print()` instead of
structured logging — failures are invisible in production.
- `app/add_expense.py` computes `was_duplicate` (whether `ON CONFLICT (idempotency_key)` suppressed
the insert) but only `print`s it and returns a bare `True` either way, so no caller can tell a
fresh insert from a duplicate. Left alone deliberately in the #29 fix — changing a signature shared
with the CLI and `app/main.py` to report on a call that no longer happens is the wrong layer (see
[ADR-0023](docs/decisions/0023-in-memory-confirm-card-state.md)) — but it is still a real gap.
- `app/bot_core.py::handle_button_click` never calls `is_authorized()`, unlike every message
handler. Authorization is enforced only when the card is created, so the callback surface itself is
ungated. Found while fixing #29; deliberately not widened into that PR. It matters much less
since webhook deliveries are authenticated ([ADR-0025](docs/decisions/0025-webhook-secret-token.md)): a button press can now only arrive
through Telegram.
- `needs_review` is extracted by `app/services/extraction.py` but never acted on anywhere — the
human-in-the-loop claim doesn't hold until this actually gates bot behavior.
- `.venv/` and `data/ledger.db` are untracked as of 2026-08-01 but **still present in git
history** — purging needs a rewrite + force-push, deliberately deferred. Tracked as
[#61](https://github.com/sujayjangam/unified-ledger-pipeline/issues/61).
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

- A one-off correction of rows in the live Neon database written with the wrong `account_desc`
  while a hardcoded payment-method default was in force. The code cause was removed on 2026-09-04,
  when the prompt's list started coming from `ACCOUNT_OWNERS`. Needs a specific, reviewed plan and
  a row count before running, not an ad hoc `UPDATE`; it is a data fix only. Account names stay out
  of this file — they live only in gitignored `.env`, since this repo is public.
- Move household participants and accounts (currently the `ACCOUNT_OWNERS` and `ALLOWED_TG_IDS`
  JSON objects plus the `PRIMARY_ACCOUNT_OWNER` string, all hand-maintained in `.env`) into
  Postgres tables — filed as
  [#53](https://github.com/sujayjangam/unified-ledger-pipeline/issues/53), scheduled for Phase 1
  (decided 2026-09-04: the matcher needs accounts as rows with stable ids, so it is built against
  that model from the start rather than migrated onto it). Moving *categories* into a table stays
  a candidate tied to Phase 2's auto-categorisation work below, not part of #53.

## Plan

### Phase 0 — Foundation & ownership

- [x] **Postgres migration (Neon)**, live on Cloud Run 2026-08-05 —
[#2](https://github.com/sujayjangam/unified-ledger-pipeline/issues/2),
[#4](https://github.com/sujayjangam/unified-ledger-pipeline/issues/4)
([ADR-0005](docs/decisions/0005-neon-postgres-over-sqlite.md)). SQLAlchemy Core with a pooled
engine, and Alembic for migrations ([ADR-0007](docs/decisions/0007-alembic-hand-written-migrations.md)).
- [x] Scheduled `pg_dump` → GCS backup with 30-day retention —
[#7](https://github.com/sujayjangam/unified-ledger-pipeline/issues/7), built 2026-08-12 as a GitHub
Actions workflow ([ADR-0012](docs/decisions/0012-github-actions-over-cloud-scheduler.md)); restore
procedure in `docs/BACKUP_RESTORE.md`.

**Ordering for the rest of Phase 0:** capture reliability first, then correctness/observability,
then the test suite, then packaging — the suite proves the recording paths work before the
packaging publishes that claim. The CI scaffolding and the tests over code the capture work won't
touch moved ahead of capture
([ADR-0020](docs/decisions/0020-ci-scaffolding-before-remaining-capture-work.md)).

**1. Capture reliability** — the binding constraint on data quality
([ADR-0017](docs/decisions/0017-extend-phase-0-for-capture-friction.md)).

- [ ] Backdated/relative date parsing from the transcript ("yesterday", "last Tuesday", explicit
spoken dates) — [#15](https://github.com/sujayjangam/unified-ledger-pipeline/issues/15). Highest
leverage of the group: without it every entry must be logged at the moment of spend.
- [x] Business date vs. write time, and reliable ordering —
[#9](https://github.com/sujayjangam/unified-ledger-pipeline/issues/9) via #10-#14, shipped
2026-09-10 ([ADR-0024](docs/decisions/0024-created-at-write-time-column.md)). Pulled ahead of #15
because the duplicate warning needed a time of day.
- [x] Record who entered each row (`entered_by`) —
[#60](https://github.com/sujayjangam/unified-ledger-pipeline/issues/60), shipped 2026-09-11
([ADR-0026](docs/decisions/0026-entered-by-telegram-user-id.md)).
- [x] Warn before saving an entry that matches one saved in the last 5 minutes —
[#58](https://github.com/sujayjangam/unified-ledger-pipeline/issues/58), under
[#57](https://github.com/sujayjangam/unified-ledger-pipeline/issues/57), shipped 2026-09-12
([ADR-0027](docs/decisions/0027-duplicate-warning-before-the-card.md)). Known gap, accepted: two
still-unconfirmed cards for the same spend don't warn each other.
- [ ] Edit and delete path for *saved* rows — [#22](https://github.com/sujayjangam/unified-ledger-pipeline/issues/22).
No `UPDATE` or `DELETE` statement exists anywhere in `app/`, so a wrong extraction is permanent,
which is also what hollows out the human-in-the-loop claim: the human is in the loop for a few
seconds at confirm time and never again. Needs a hard-vs-soft-delete decision first, which is what
separates it from the two `context.user_data`-only recovery paths below.
- [x] Text ingestion alongside voice, since voice is unusable in most real spending moments —
[#16](https://github.com/sujayjangam/unified-ledger-pipeline/issues/16), shipped 2026-08-21.
- [ ] Pending-transaction **edit** path — the confirm card should be Confirm/Edit/Cancel, so a wrong
extraction can be corrected before saving rather than only accepted whole or discarded. This is
`context.user_data` state only, no DB write, which is what makes it separable from
[#22](https://github.com/sujayjangam/unified-ledger-pipeline/issues/22) (editing *saved* rows, which
needs a hard-vs-soft-delete decision first). The field picker built here is reusable for #22.
Filed 2026-09-12 as [#66](https://github.com/sujayjangam/unified-ledger-pipeline/issues/66), the first sub-issue of #22.
- [ ] Missing-amount recovery — when extraction returns no amount, `process_expense_text` abandons
the entry with a text prompt, so the user has to start over from scratch. Offer
`[Manual Entry]` / `[New Voice Note]` buttons instead, keeping the raw text already captured.
Specified but never built in the MVP flow doc that
[#28](https://github.com/sujayjangam/unified-ledger-pipeline/issues/28) retired — salvaged here
before deleting it, so the idea outlives the file. Belongs with the edit path above: both are
recovery paths over `context.user_data` with no DB write. No issue filed yet.
- [ ] Drop the one-expense-per-voice-note guardrail in `app/bot_core.py` — `TransactionList`
already models multiple; this is a product restriction, not a technical limit.
- [x] Authenticate webhook deliveries with Telegram's secret token —
[#63](https://github.com/sujayjangam/unified-ledger-pipeline/issues/63), shipped 2026-09-11
([ADR-0025](docs/decisions/0025-webhook-secret-token.md)). The prerequisite for trusting the sender
ID `is_authorized()` checks.
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

**3. Test suite and CI** — split so the parts that don't depend on the capture paths could go
first ([ADR-0020](docs/decisions/0020-ci-scaffolding-before-remaining-capture-work.md)). The suite
grows with each phase, and is separate from the Phase 2 evaluation harness: this is correctness,
the harness is match *quality*.

**3a. Scaffolding and stable-target tests, done ahead of the capture work** —
[#31](https://github.com/sujayjangam/unified-ledger-pipeline/issues/31) (parent), with #32, #33 and
#34.

This is a soft gate, deliberately: it stops a merge that fails to load (syntax errors, bad imports,
a missing dependency, an import-time crash). It does **not** catch a handler that throws at
runtime, a malformed query or a broken prompt — a green check doesn't mean "the bot works".

- [x] CI on pull requests: clean install, lint, and an import smoke check across `app/` —
[#32](https://github.com/sujayjangam/unified-ledger-pipeline/issues/32), 2026-08-26. The clean
install doubles as evidence for [#27](https://github.com/sujayjangam/unified-ledger-pipeline/issues/27).
- [x] Pytest harness, with test-only dependencies in `requirements-dev.txt` —
[PR #46](https://github.com/sujayjangam/unified-ledger-pipeline/pull/46), 2026-09-03, verified by
breaking an assertion and watching CI go red.
- [x] Stable-target tests (money conversion, period boundaries, handler routing, extraction schema
parsing, `is_authorized`, payment-default inference), with no network or billable API calls —
2026-09-03, one category per PR (#38, #40-#42, #44-#46).
- [ ] Migrations applied from scratch (`alembic upgrade head` against a throwaway Postgres
container), and the `ON CONFLICT (idempotency_key)` path against a real database. Nothing in
`tests/` touches a real database: `tests/test_migrations.py` checks only the revision chain's
structure, and `0002_add_created_at` was run by hand against a Neon branch — evidence, not
automated coverage.
- [x] `main` protected by a repository ruleset requiring `check-PR-before-merge`, verified against
a throwaway PR — [#34](https://github.com/sujayjangam/unified-ledger-pipeline/issues/34),
2026-09-03 ([ADR-0021](docs/decisions/0021-rulesets-over-classic-branch-protection.md)).

**3b. After capture reliability is finished** — coverage that has to run against the finished
capture paths:

- [ ] Every ingestion path end to end, against the capture paths as they end up — not the ones
being replaced.
- [ ] Extend routing coverage to the callback handlers the pending-transaction edit path adds.

**3c. Ownership pass** — not a testing task, and it stays open after 3a and 3b are ticked off:

- [ ] Deliberate pass through the AI-assisted async/Pydantic/FastAPI-lifespan code — rewrite or
annotate until it can be defended live, not just described.

**4. Packaging** — moved here from Phase 5 on 2026-08-20.

- [x] Case-study `README.md` with an inline Mermaid architecture diagram — 2026-08-29. `README.md`
describes only what has shipped; forward-looking work sits under "What's next".
- [ ] Short demo recording → GIF at the top of `README.md`. Last item in the phase: it should show
a working expense recorder, not the friction-limited one. Recording it early against the current
build is still worthwhile as a private friction-finding exercise — it surfaces exactly the UX
problems the capture work above is meant to fix — but the published artifact comes last.

### Phase 1 — Reconciliation engine

- [ ] PDF statement parser: rule-based extraction per bank format (OCBC, DBS, YouTrip) first
- [ ] LLM-assisted extraction as fallback, only for lines the rule-based parser can't handle
- [ ] Receipt image capture (Telegram photo message) as a second ingestion path alongside voice
notes — store the image (GCS) and link it to the transaction row; reuses the rule-based +
LLM-fallback extraction architecture above rather than building a separate one-off pipeline.
- [ ] Household participants and accounts as Postgres tables, replacing the `ACCOUNT_OWNERS` /
`PRIMARY_ACCOUNT_OWNER` / `ALLOWED_TG_IDS` env vars —
[#53](https://github.com/sujayjangam/unified-ledger-pipeline/issues/53). Goes before the staging
table so statement lines attach to an account row, not a string. The Phase 3 `participants` table
below is this one.
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

- [ ] Schema: `transaction_splits` child table, `split_type` enum (the `participants` table itself
lands in Phase 1 via [#53](https://github.com/sujayjangam/unified-ledger-pipeline/issues/53))
- [ ] Application logic: even-split and one uneven-split mode to start (who-owes-who calculation)
- [ ] Extend as real usage surfaces the need for more flexible splitting
- [ ] Split in whole cents, so the parts always add up to the entry's amount (reconciliation
depends on it; the rule is recorded in [ADR-0028](docs/decisions/0028-exact-cent-conversion-half-up.md)). Store each person's share as integer
cents in `transaction_splits`, with a check that the shares sum to the entry. Work the shares out by
rounding every share down and handing the leftover cents out one at a time, never by rounding each
share: SGD 5.55 split two ways would otherwise be 2.78 + 2.78 = 5.56. This replaces the
`split_ratio` Decimal column, which nothing writes today. Who gets a leftover cent must be a fixed
rule, not random, so the same entry always splits the same way; decide it here (suggested:
whoever's card paid).
- [ ] A database view that shows each entry's sender by name: `entered_by` joined to the
household-members table from [#53](https://github.com/sujayjangam/unified-ledger-pipeline/issues/53), so the base table keeps storing only the ID
([ADR-0026](docs/decisions/0026-entered-by-telegram-user-id.md)) while anything reading the ledger sees names. Not possible before #53, since names
live only in env secrets until then. Deliberately after Phase 2 (decided 2026-09-12).
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

### Phase 5 — Reliability hardening & packaging refresh

The case-study README and architecture diagram moved to the end of Phase 0 on 2026-08-20, so this
phase is no longer the project's first packaging pass — it's the pass that brings the published
material back in line with a system that by then has reconciliation, an eval harness, splitting and
budgeting in it.

- [ ] Refresh the case-study `README.md` and architecture diagram against the shipped Phase 1-4
system — in particular the eval metrics, which don't exist yet at the Phase 0 writeup
- [ ] Error alerting beyond the Phase 1 Telegram pipeline-failure alert, extended to cover
`.github/workflows/backup.yml` (see "Still outstanding")
- [ ] Revisit the deferred git-history purge of `.venv/` and `data/ledger.db` — [#61](https://github.com/sujayjangam/unified-ledger-pipeline/issues/61)

## How to resume a session

1. Read this file's "Current status" section first.
2. Check `git log --oneline -10` for what actually landed since status was last updated (this file
can drift from reality if an update was forgotten).
3. Only read the specific files relevant to the next unchecked task — not the whole repo.
4. Before ending a session, update "Current status" and check off finished tasks.
