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
| [0020](0020-ci-scaffolding-before-remaining-capture-work.md) | Split the test-suite phase; land CI scaffolding before the rest of capture reliability | Accepted | 2026-08-21 |
| [0021](0021-rulesets-over-classic-branch-protection.md) | Enforce the merge gate with a repository ruleset, not classic branch protection | Accepted | 2026-09-03 |
| [0022](0022-artifact-registry-cleanup-policy.md) | Cap Artifact Registry to the 3 most recent container images, no age condition | Accepted | 2026-09-03 |
| [0023](0023-in-memory-confirm-card-state.md) | Keep confirmation-card state in process memory, keyed by message id | Accepted | 2026-09-10 |
| [0024](0024-created-at-write-time-column.md) | Record write time in its own column, backfilled to midnight SGT | Accepted | 2026-09-10 |
| [0025](0025-webhook-secret-token.md) | Authenticate webhook deliveries with Telegram's secret token | Accepted | 2026-09-10 |
| [0026](0026-entered-by-telegram-user-id.md) | Record the sender as their Telegram user ID, and leave existing rows unrecorded | Superseded by [0033](0033-entered-by-participant-id.md) | 2026-09-11 |
| [0027](0027-duplicate-warning-before-the-card.md) | Warn about a likely duplicate before the card is shown, matching on amount and currency | Accepted | 2026-09-12 |
| [0028](0028-exact-cent-conversion-half-up.md) | Convert amounts to cents with exact decimal maths, rounding half a cent up; split in whole cents | Accepted | 2026-09-13 |
| [0029](0029-close-phase-0-on-date-parsing.md) | Close Phase 0 when backdated dates ship; move the rest of its capture work to later phases | Accepted | 2026-09-23 |
| [0030](0030-llm-reads-dates-python-resolves.md) | Let the model describe the date and Python calculate it; warn on implausible dates | Accepted | 2026-09-23 |
| [0031](0031-last-week-from-message-text.md) | Tell "last Tuesday" from "Tuesday last week" with a text match, not the model; check prompts against the real model | Accepted | 2026-10-11 |
| [0032](0032-participants-and-accounts-in-postgres.md) | Keep participants and accounts in Postgres tables, read on every message, and fail closed | Accepted | 2026-11-11 |
| [0033](0033-entered-by-participant-id.md) | Record the sender as a participant id, not a Telegram user ID | Accepted | 2026-11-11 |
| [0034](0034-drop-line-by-line-ownership-gate.md) | Drop the line-by-line ownership gate before commit | Accepted | 2026-11-14 |
| [0035](0035-trips-tagged-at-log-time.md) | Trips: one active trip in the database, entries tagged when logged, families per trip | Partly superseded by [0036](0036-trip-switch-per-person.md) | 2026-11-14 |
| [0036](0036-trip-switch-per-person.md) | Trip mode is a switch per person, picked from buttons, shown by a pinned banner | Accepted | 2026-11-14 |
| [0037](0037-paid-by-person-read-from-text.md) | The payer is a person column, read from the message by Python; an owned account decides the payer | Accepted | 2026-11-15 |
| [0038](0038-paid-by-button-starts-from-the-message.md) | A tapped payer starts from the account the message named, using the card's own directory snapshot | Accepted | 2026-11-16 |
| [0039](0039-splits-read-by-the-model-worked-out-by-python.md) | The model reads who shares an entry; Python matches the names and hands out the cents | Partly superseded by [0054](0054-paid-for-someone-means-they-owe.md), [0055](0055-between-list-read-by-python-first.md) | 2026-11-16 |
| [0040](0040-model-as-second-reader-for-the-payer.md) | The model is a second reader for the payer, used only when Python finds nobody | Accepted | 2026-11-16 |
| [0041](0041-split-menu-and-wider-people-menus.md) | The split menu, and people menus that offer every participant when no trip is on | Accepted | 2026-11-16 |
| [0042](0042-uneven-shares-by-count.md) | Uneven shares by a count per person, read by the model and checked in Python | Accepted (edited 2026-11-18) | 2026-11-16 |
| [0043](0043-everyday-groups-for-the-split-menu.md) | Everyday groups for the split menu, and no relationships between people | Accepted | 2026-11-17 |
| [0044](0044-trip-balances-from-splits.md) | Trip balances worked out from the splits, and totals stay "what was paid" | Partly superseded by [0051](0051-debts-per-person-then-families.md), [0056](0056-trip-bals-one-currency-per-screen.md) | 2026-11-17 |
| [0045](0045-family-labels-the-rest-and-stricter-wording.md) | Family labels, "the rest", and stricter wording over a model double-check | Accepted | 2026-11-17 |
| [0046](0046-settlements-as-their-own-records.md) | Paybacks are their own records, and can be in a different currency | Partly superseded by [0052](0052-settle-up-by-typed-message.md), [0053](0053-other-currency-paybacks-at-a-reference-rate.md) | 2026-11-17 |
| [0047](0047-pay-back-only-who-you-owe.md) | Pay back only who you owe, and paying on someone's behalf | Partly superseded by [0051](0051-debts-per-person-then-families.md) | 2026-11-17 |
| [0048](0048-exact-split-amounts-by-reply.md) | Exact split amounts, typed as a reply | Accepted | 2026-11-18 |
| [0049](0049-prompt-holds-only-what-python-cant-read.md) | The extraction prompt holds only what Python can't read from the words | Accepted | 2026-11-18 |
| [0050](0050-soft-delete-behind-a-view.md) | Deleted entries are marked, and a view hides them | Partly superseded by [0052](0052-settle-up-by-typed-message.md) | 2026-11-18 |
| [0051](0051-debts-per-person-then-families.md) | Debts are worked out per person, then rolled up to families | Accepted | 2026-11-19 |
| [0052](0052-settle-up-by-typed-message.md) | Settling up any bill by a typed "settle" message, linked to the bills it clears | Accepted | 2026-11-19 |
| [0053](0053-other-currency-paybacks-at-a-reference-rate.md) | A payback in another currency is converted at a reference rate, then checked against the statement | Accepted | 2026-11-19 |
| [0054](0054-paid-for-someone-means-they-owe.md) | A cost paid "for" someone means they owe it | Accepted | 2026-11-21 |
| [0055](0055-between-list-read-by-python-first.md) | A "between" list is read by Python first | Accepted | 2026-11-21 |
| [0056](0056-trip-bals-one-currency-per-screen.md) | `/trip_bals` shows one currency per screen, with payments and paybacks behind buttons | Accepted | 2026-11-21 |
| [0057](0057-change-card-button.md) | A "Change card" button picks the account on the card, and becomes the account a payer tap starts from | Accepted | 2026-11-21 |

## Writing a new one

Keep it lean, like [0042](0042-uneven-shares-by-count.md) and
[0048](0048-exact-split-amounts-by-reply.md) (layout adopted 2026-11-18; older records are
longer): a metadata block (status, date, issue, code), then **Problem** in a line or two,
**Decision** as a Mermaid diagram plus a few short bullets, one line of evidence with a link if
any, and **Rejected** with one line per option. Implementation rules belong in the code, tests
and `CLAUDE.md`; test runs belong in the PR.

Link code by file and symbol (`app/bot_core.py::process_expense_text`) rather than by
line number, which rots. Number sequentially; never renumber.
