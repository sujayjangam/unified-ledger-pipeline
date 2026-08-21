# Architecture Decision Records

One file per decision: what the constraint was, what was chosen, what it cost.
Each record is immutable once accepted — to change a decision, add a new record that
supersedes the old one rather than editing history.

`ROADMAP.md` says where the project is going. These say why it is built the way it is.

| # | Decision | Status | Date |
|---|---|---|---|
| [0001](0001-cloud-run-over-local-nas.md) | Deploy the ingestion layer to Cloud Run, not a local NAS | Accepted | 2026-07 |
| [0002](0002-openai-api-over-local-whisper.md) | Use the OpenAI API for transcription, not a local Whisper model | Accepted | 2026-07 |
| [0003](0003-split-bot-transport-from-logic.md) | Split the bot into a transport layer and a logic layer | Accepted | 2026-07 |
| [0004](0004-money-as-integer-cents.md) | Store money as integer cents, never floats | Accepted | 2026-07 |
| [0005](0005-neon-postgres-over-sqlite.md) | Move the ledger from SQLite to Neon Postgres | Accepted | 2026-08-03 |
| [0006](0006-psycopg3-url-scheme.md) | Use the postgresql+psycopg:// URL scheme, not postgresql:// | Accepted | 2026-08-05 |
| [0007](0007-alembic-hand-written-migrations.md) | Let Alembic own the schema, with hand-written migrations | Accepted | 2026-08-03 |
| [0008](0008-raw-transcript-as-description.md) | Keep the raw input as the description, not an LLM summary | Accepted | 2026-07 |
| [0009](0009-pin-whisper-language-en.md) | Pin Whisper to language="en" | Accepted | 2026-07 |
| [0010](0010-one-expense-per-message.md) | Reject input containing more than one expense (V1) | Accepted | 2026-07 |
| [0011](0011-idempotency-key-over-update-id.md) | Dedupe saves with an idempotency key; defer persisting the webhook update_id | Accepted | 2026-07-30 |
| [0012](0012-github-actions-over-cloud-scheduler.md) | Run the scheduled backup from GitHub Actions, not Cloud Scheduler | Accepted | 2026-08-12 |
| [0013](0013-workload-identity-federation.md) | Authenticate the backup with Workload Identity Federation, not a stored key | Accepted | 2026-08-12 |
| [0014](0014-pg-dump-custom-format.md) | Take pg_dump custom format (-Fc) over plain SQL, and pin the client version | Accepted | 2026-08-12 |
| [0015](0015-deterministic-matching-before-llm.md) | Match statement lines with deterministic rules before reaching for an LLM | Accepted (not yet implemented) | 2026-08 |
| [0016](0016-pdf-statements-over-csv.md) | Support PDF statement exports rather than cleaner CSV exports | Accepted (not yet implemented) | 2026-08 |
| [0017](0017-extend-phase-0-for-capture-friction.md) | Extend Phase 0 for capture friction rather than opening Phase 1 | Accepted | 2026-08-20 |
| [0018](0018-text-ingestion-before-date-parsing.md) | Ship text ingestion before backdated date parsing, and treat LLM cost as a non-constraint | Accepted | 2026-08-21 |
| [0019](0019-separate-bot-token-for-local-testing.md) | Use a separate bot token and database branch for local testing | Accepted | 2026-08-21 |
| [0020](0020-ci-scaffolding-before-remaining-capture-work.md) | Split the test-suite phase; land CI scaffolding before the rest of capture reliability | Accepted (not yet implemented) | 2026-08-21 |

## Writing a new one

Copy the structure of any existing record: a metadata block (status, date, issues, PRs,
code), then **Context** (the constraint or problem), **Decision** (what was chosen),
**Alternatives considered**, and **Consequences** (what it bought and what it cost —
this is the section worth the most later).

Link code by file and symbol (`app/bot_core.py::process_expense_text`) rather than by
line number, which rots. Number sequentially; never renumber.
