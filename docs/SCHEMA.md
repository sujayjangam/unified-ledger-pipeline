# Ledger Canonical Schema (v1.3 - Household Unified)

## Core Design Decisions
1. **Multi-Entity Tracking**: Records ownership (account_owner) vs. beneficiary (benefit_of).
2. **Flexible Splitting**: 'split_ratio' handles shared (0.5) or personal (1.0) allocation.
3. **Multi-Currency Support**: Tracks raw transaction data vs. base SGD amount.
4. **Integer Cents**: All money stored as integers for mathematical precision. Dollars are
   converted only by `app/add_expense.py::dollars_to_cents` (exact decimal maths; half a cent
   rounds up) - see [ADR-0028](decisions/0028-exact-cent-conversion-half-up.md).

## Fields
- **transaction_id**: UUID (Primary Key)
- **date**: ISO 8601 string (YYYY-MM-DD). The *business* date - when the spend happened, as
  inferred from the message. See "Business date vs. write time" below.
- **description**: Raw bank text
- **amount**: Positive integer (cents)
- **currency**: ISO 3-letter code
- **base_amount**: Final value in SGD (Integer Cents)
- **account_owner**: Card owner's display name (e.g., 'Alice', 'Bob'). Being replaced by
  `account_id` (#53): still written alongside it until it is dropped.
- **benefit_of**: Beneficiary (e.g., 'Alice', 'Bob', 'Shared')
- **split_ratio**: Decimal (e.g., 0.5). Written by no code today. Phase 3 plans to replace it
  with each person's share stored as whole cents that add up to `amount`, because a ratio
  multiplied out later can't guarantee that: 0.5 of SGD 5.55 rounds to 2.78 for both people
  (ADR-0028).
- **category**: Budget group
- **transaction_type**: 'income' or 'expense'
- **source**: Data origin
- **reconciliation_status**: 'settled' or 'unsettled'
- **account_desc**: Payment method / card the transaction was charged to (e.g. 'YouTrip',
  'Cash'). Nullable. Being replaced by `account_id` (#53): still written alongside it until it
  is dropped.
- **account_id**: BIGINT, nullable, FK → `accounts.id` (revision `0004_participants_accounts`).
  The account that paid. Written by the bot, CLI and REST API; older rows are linked by
  `scripts/seed_directory.py`, which matches their `account_desc` text.
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
  created the row, as a string (revision `0003_add_entered_by`). Being replaced by
  `entered_by_participant_id` ([ADR-0033](decisions/0033-entered-by-participant-id.md)). Display names are never stored; they're looked up from the ID when
  shown. NULL means "sender not recorded": every row that predates the column, and every row
  from the CLI or REST API. Not to be confused with `account_owner`, which records whose card
  paid, is inferred from the payment method, and is often not the sender.
- **entered_by_participant_id**: BIGINT, nullable, FK → `participants.id` (revision
  `0004_participants_accounts`). The sender as a participant. NULL means "not recorded", as for
  `entered_by`.

## participants and accounts (#53)

Who may use the bot, and the accounts money is paid from, are rows rather than settings
([ADR-0032](decisions/0032-participants-and-accounts-in-postgres.md)). Ids are
`GENERATED ALWAYS AS IDENTITY`: a new row always gets a new id, and an id is never reused.

**participants**
- **id**: BIGINT identity, primary key
- **display_name**: TEXT, NOT NULL, UNIQUE. What the card and the duplicate warning show.
- **telegram_user_id**: TEXT, nullable, UNIQUE. Set → this person may use the bot. NULL → a
  person who only appears in the ledger.
- **default_account_id**: BIGINT, nullable, FK → `accounts.id`. Used when a message names no
  card. Anyone with a `telegram_user_id` must have one, and it must be open.
- **created_at**: TIMESTAMPTZ, NOT NULL, `DEFAULT now()`

**accounts**
- **id**: BIGINT identity, primary key
- **name**: TEXT, NOT NULL. The name the extraction prompt offers and the card shows.
- **owner_participant_id**: BIGINT, nullable, FK → `participants.id`. NULL means shared (Cash,
  the YouTrip wallet): the money is the sender's.
- **funded_by_account_id**: BIGINT, nullable, FK → `accounts.id`. Only on the wallet: the card a
  YouTrip top-up is paid from, whoever sends it.
- **closed_on**: DATE, nullable. A closed card keeps its real name and its id, so old rows stay
  linked; the bot asks before saving a new entry against it.
- **created_at**: TIMESTAMPTZ, NOT NULL, `DEFAULT now()`

Two open accounts can't share a name, ignoring case (`uq_accounts_open_name_ci`, a partial
unique index on `lower(name)` where `closed_on IS NULL`). A closed card and its open replacement
can.

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
