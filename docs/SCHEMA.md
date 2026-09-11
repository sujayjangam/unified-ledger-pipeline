# Ledger Canonical Schema (v1.3 - Household Unified)

## Core Design Decisions
1. **Multi-Entity Tracking**: Records ownership (account_owner) vs. beneficiary (benefit_of).
2. **Flexible Splitting**: 'split_ratio' handles shared (0.5) or personal (1.0) allocation.
3. **Multi-Currency Support**: Tracks raw transaction data vs. base SGD amount.
4. **Integer Cents**: All money stored as integers for mathematical precision.

## Fields
- **transaction_id**: UUID (Primary Key)
- **date**: ISO 8601 string (YYYY-MM-DD). The *business* date - when the spend happened, as
  inferred from the message. See "Business date vs. write time" below.
- **description**: Raw bank text
- **amount**: Positive integer (cents)
- **currency**: ISO 3-letter code
- **base_amount**: Final value in SGD (Integer Cents)
- **account_owner**: Card owner (e.g., 'Sujay', 'Wife')
- **benefit_of**: Beneficiary (e.g., 'Sujay', 'Wife', 'Shared')
- **split_ratio**: Decimal (e.g., 0.5)
- **category**: Budget group
- **transaction_type**: 'income' or 'expense'
- **source**: Data origin
- **reconciliation_status**: 'settled' or 'unsettled'
- **account_desc**: Payment method / card the transaction was charged to (e.g. 'YouTrip',
  'Cash'). Nullable — only the Telegram bot populates it today.
- **idempotency_key**: UUID, nullable, UNIQUE. Set by the Telegram bot (one per confirm prompt,
  generated when the transaction is presented for confirmation, reused on every save attempt for
  that same prompt) so a double-tap or webhook redelivery can't insert the same transaction twice.
  Manual/API inserts leave this NULL — the UNIQUE constraint allows multiple NULLs.
- **created_at**: TIMESTAMPTZ, NOT NULL, `DEFAULT now()`. The *write* time - when the row was
  inserted. Set only by the database default; no application code ever supplies it (every writer
  names its columns and omits this one). Rows that already existed when the column was added
  (migration `0002_add_created_at`) were backfilled to midnight Singapore time on their own `date`,
  so an exact `00:00:00+08` means "time of day unknown", not "logged at midnight".
- **entered_by**: TEXT, nullable. The Telegram user ID of the household member whose message
  created the row, in the same string form `ALLOWED_TG_IDS` uses for its keys (revision
  `0003_add_entered_by`). Display names are never stored; they're looked up from the ID when
  shown. NULL means "sender not recorded": every row that predates the column, and every row
  from the CLI or REST API. Not to be confused with `account_owner`, which records whose card
  paid, is inferred from the payment method, and is often not the sender.

## Business date vs. write time

`date` and `created_at` answer different questions and must not be substituted for each other:

| Column | Question it answers | Set by |
|---|---|---|
| `date` | When did the spend happen? | Inferred from the message (LLM extraction) |
| `created_at` | When was the row written? | Postgres, at insert |

Today they almost always fall on the same day, because every entry is dated the day it was sent.
They diverge once backdated entries are supported (#15): "coffee yesterday", logged this morning,
has yesterday's `date` and this morning's `created_at`.

Ordering follows from the distinction:

- **"Most recent"** (`/recent`) orders by `created_at` - what was most recently *logged*.
- **The ledger** (`view_ledger`) orders by `date`, with `created_at` breaking ties within a day.
- Anything that means "within the last N minutes" (e.g. duplicate detection) must use
  `created_at`; `date` has no time component.

`transaction_id` is a random UUID and carries no ordering at all.
