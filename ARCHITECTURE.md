# Unified Ledger: Architecture & Design Decisions

Design decisions now live as numbered Architecture Decision Records in
[`docs/decisions/`](docs/decisions/) — one file per decision, each with its context, the
alternatives that were rejected, the issue and PR that implemented it, and the consequences.
See [`docs/decisions/README.md`](docs/decisions/README.md) for the full index.

This file previously held that content directly. It was split up on 2026-08-21 so that each
decision could be found, linked and superseded independently, rather than accumulating in one
document alongside everything else.

## Start here

**System shape**
- [ADR-0001](docs/decisions/0001-cloud-run-over-local-nas.md) — Cloud Run over a local NAS
- [ADR-0003](docs/decisions/0003-split-bot-transport-from-logic.md) — splitting the bot's
  transport layer from its logic layer (`bot_core` / `bot_polling` / `bot_webhook`)
- [ADR-0002](docs/decisions/0002-openai-api-over-local-whisper.md) — the OpenAI API over a
  locally hosted Whisper model

**Data**
- [ADR-0005](docs/decisions/0005-neon-postgres-over-sqlite.md) — Neon Postgres over SQLite
- [ADR-0004](docs/decisions/0004-money-as-integer-cents.md) — money as integer cents
- [ADR-0007](docs/decisions/0007-alembic-hand-written-migrations.md) — Alembic owns the schema,
  migrations written by hand
- [ADR-0012](docs/decisions/0012-github-actions-over-cloud-scheduler.md) — backups scheduled from
  GitHub Actions rather than GCP

**Where the project is going:** [`ROADMAP.md`](ROADMAP.md).
**How the code is laid out and why:** [`CLAUDE.md`](CLAUDE.md).
