# Roadmap

Use GitHub Issues to track fixes, feature pushes, etc. to keep progress clean and trackable.

This document is the single source of truth for where this project stands. Read the "Current
status" section first when resuming work — don't re-derive it by reading the whole repo.

## Why this project exists (two goals, one codebase)
1. A live financial tool: my wife and I actually use this to log and manage household expenses,
   and intend to keep using it for years.
2. A portfolio piece to land a GenAI/ML/Data Engineering role in Perth. It needs to demonstrate
   real depth (not "just data extraction") and skills that don't evaporate as AI tooling improves.

Both goals point the same direction here, so priorities are set by genuine usefulness first —
that naturally produces the stronger interview story too.

## Current status
**Phase:** Phase 0 in progress.
**Next action:** Not yet decided — pick the next item from "Known issues to fix" below or start
issue #2 (Neon Postgres migration) at the start of the next session.

[Issue #1](https://github.com/sujayjangam/unified-ledger-pipeline/issues/1) (Telegram double-insert
bug) is fixed, merged via [PR #3](https://github.com/sujayjangam/unified-ledger-pipeline/pull/3),
and closed as of 2026-07-30. Issue #2 (Neon Postgres migration) is assigned to Sujay but not yet
started.

## Constraints (agreed, don't relitigate without a reason)
- Target: GenAI/ML/Data Engineering in Perth, open to any of the three — explicitly not a project
  that reads as "just data extraction."
- Timeline: ~5-10 hrs/week, aiming to start applying in Perth within ~2 months (~8 weeks).
- Background: SQL + a Python bootcamp finished ~3 months ago, no formal CS/DS training.
  Comfortable with Git/GitHub (branches, PRs, CI) and basic ML libraries (scikit-learn, pandas).
  Much of the current codebase was written with Gemini/ChatGPT assistance — ownership is solid on
  the simple parts, weaker on async/await, Pydantic schemas, and FastAPI's `lifespan` handling.
- This is live infrastructure for two real users, not a demo — reliability and correctness matter
  as much as portfolio signal.
- Real pain points, in priority order: (1) reconciling voice-logged entries against real bank/card
  statements, (2) budgeting & visibility (dashboards/alerts/reports). FX conversion is *not* a
  priority — statements already show converted rates.
- Statement format to support: **PDF exports** (OCBC, DBS, YouTrip and similar) — chosen
  deliberately over cleaner CSV exports because messy real-world PDFs make a stronger
  document-extraction story.
- Matching approach: deterministic rules (amount + date window) first, backed by a hand-labeled
  eval set reporting precision/recall — not semantic/LLM-based matching from day one.
- Reliability is in scope now, not deferred: automated backups, structured logging, basic CI,
  error alerting.
- Packaging: rewrite `README.md` as a case study with an architecture diagram, plus a
  build-in-public series (LinkedIn/blog) specifically on the PDF-parsing and eval-methodology work.

## Known issues to fix (found during initial review, not yet fixed)
- `app/database.py`'s `CREATE TABLE` is missing the `account_desc` column that `app/add_expense.py`
  inserts into — it was added via an ad hoc `ALTER TABLE` in `test_queries.py`, never codified in
  `database.py`.
- `app/sample_data.py` imports `from database import get_connection` instead of
  `from app.database import get_connection` — broken since the module-path fixes in earlier
  commits.
- `README.md` currently contains accidental `requirements.txt` content, not real documentation.
- A stray empty `ledger.db` sits at the repo root; the real database is `data/ledger.db`.
- **[Issue #2](https://github.com/sujayjangam/unified-ledger-pipeline/issues/2)** — transactions are
  lost on Cloud Run restart/redeploy because SQLite lives on the container's ephemeral local disk
  (compounded by `data/ledger.db` being accidentally committed to git and baked into every image
  build). Direction locked in: migrate to Neon Postgres (free tier fits this project's scale
  indefinitely; also the right foundation for future multi-party split reconciliation).

## Plan

### Phase 0 — Foundation & ownership (Week 1)
- [ ] Fix the known issues above
- [ ] Deliberate pass through the AI-written async/Pydantic/FastAPI-lifespan code — rewrite or
      annotate until it can be defended live, not just described
- [ ] Basic CI: lint + smoke tests on push
- [ ] Fix `README.md` with a placeholder (full case-study rewrite happens in Phase 4)

### Phase 1 — Reconciliation engine (Weeks 2-4)
- [ ] PDF statement parser (OCBC, DBS, YouTrip) — likely LLM-assisted extraction given irregular
      real-world layouts
- [ ] Normalize parsed statement lines into a staging table
- [ ] Deterministic matcher: amount + date window against existing ledger entries
- [ ] Wire matcher output into the existing (currently unused) `reconciliation_status` field
- [ ] Reliability: automated `data/ledger.db` backups, structured logging, Telegram alert on
      failure

### Phase 2 — Evaluation harness (Week 4-5)
- [ ] Hand-label a golden set of real statement-line → ledger-entry matches/non-matches
- [ ] Score the matcher: precision/recall/F1
- [ ] Document the methodology — this is the headline interview artifact

### Phase 3 — Budgeting & visibility (Week 5-6)
- [ ] Spend-vs-budget view
- [ ] Weekly digest (Telegram)
- [ ] Reconciled vs. unreconciled breakdown

### Phase 4 — Packaging (Weeks 6-8)
- [ ] Rewrite `README.md` as a case study: problem, architecture, decisions, eval metrics, what's
      next
- [ ] Architecture diagram
- [ ] Build-in-public content on PDF parsing and eval methodology specifically
- [ ] Rehearse the verbal narrative

## How to resume a session
1. Read this file's "Current status" section first.
2. Check `git log --oneline -10` for what actually landed since status was last updated (this file
   can drift from reality if an update was forgotten).
3. Only read the specific files relevant to the next unchecked task — not the whole repo.
4. Before ending a session, update "Current status" and check off finished tasks.
