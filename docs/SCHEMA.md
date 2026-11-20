# Ledger Canonical Schema (v1.3 - Household Unified)

## Core Design Decisions
1. **Multi-Entity Tracking**: Records the paying account (account_id, whose owner is in `accounts`) vs. beneficiary (benefit_of).
2. **Flexible Splitting**: 'split_ratio' handles shared (0.5) or personal (1.0) allocation.
3. **Multi-Currency Support**: Tracks raw transaction data vs. base SGD amount.
4. **Integer Cents**: All money stored as integers for mathematical precision. Dollars are
   converted only by `app/add_expense.py::dollars_to_cents` (exact decimal maths; half a cent
   rounds up) - see [ADR-0028](decisions/0028-exact-cent-conversion-half-up.md).

## transactions: a view over raw_transactions (revision `0010_soft_delete`)

Since [ADR-0050](decisions/0050-soft-delete-behind-a-view.md) the table is `raw_transactions`,
and `transactions` is a view of its live rows (`WHERE deleted_at IS NULL`) with the columns
below. Every read, insert and join uses `transactions`; only the delete and undo queries in
`app/services/ledger_queries.py` (`soft_delete_entry`, `restore_entry`) name `raw_transactions`,
plus `get_trip_settlements`, which reads only who paid a payback row's bill, deleted or not
([ADR-0052](decisions/0052-settle-up-by-typed-message.md)).
Foreign keys and the `idempotency_key` unique constraint are on the real table, so a deleted row
still blocks a retry of its Confirm.

Two columns are on `raw_transactions` only, never in the view:
- **deleted_at**: TIMESTAMPTZ, nullable. NULL means live.
- **deleted_by_participant_id**: BIGINT, nullable, FK → `participants.id`
  (`fk_raw_transactions_deleted_by`). Who deleted it. A live row can't have one
  (`ck_raw_transactions_deleter_only_when_deleted`).

A new column goes on `raw_transactions`, then into the view with `CREATE OR REPLACE VIEW`, which
can only add columns at the end; dropping, renaming or retyping one means dropping and
recreating the view in the same migration.

## Fields
- **transaction_id**: UUID (Primary Key)
- **date**: ISO 8601 string (YYYY-MM-DD). The *business* date - when the spend happened, as
  inferred from the message. See "Business date vs. write time" below.
- **description**: Raw bank text
- **amount**: Positive integer (cents)
- **currency**: ISO 3-letter code
- **base_amount**: Final value in SGD (Integer Cents)
- **benefit_of**: Beneficiary (e.g., 'Alice', 'Bob', 'Shared')
- **split_ratio**: Decimal (e.g., 0.5). Written by no code today. Phase 3 plans to replace it
  with each person's share stored as whole cents that add up to `amount`, because a ratio
  multiplied out later can't guarantee that: 0.5 of SGD 5.55 rounds to 2.78 for both people
  (ADR-0028).
- **category**: Budget group
- **transaction_type**: 'Expense' or 'Transfer' (a YouTrip top-up or other money moved between
  accounts). Nullable, with no check constraint. 'Income' is planned for statement upload (see
  `ROADMAP.md`).
- **source**: Data origin
- **reconciliation_status**: 'settled' or 'unsettled'
- **account_id**: BIGINT, NOT NULL, FK → `accounts.id` (added nullable in revision
  `0004_participants_accounts`, made NOT NULL by `0005_drop_text_account_columns`). The account
  that paid; its name and owner are looked up from `accounts` when shown. Written by the bot, CLI
  and REST API.
- **idempotency_key**: UUID, nullable, UNIQUE. Set by the Telegram bot (one per confirm prompt,
  generated when the transaction is presented for confirmation, reused on every save attempt for
  that same prompt) so a double-tap or webhook redelivery can't insert the same transaction twice.
  Manual/API inserts leave this NULL — the UNIQUE constraint allows multiple NULLs.
- **created_at**: TIMESTAMPTZ, NOT NULL, `DEFAULT now()`. The *write* time - when the row was
  inserted. Set only by the database default; no application code ever supplies it (every writer
  names its columns and omits this one). Rows that already existed when the column was added
  (migration `0002_add_created_at`) were backfilled to midnight Singapore time on their own `date`,
  so an exact `00:00:00+08` means "time of day unknown", not "logged at midnight".
- **entered_by_participant_id**: BIGINT, nullable, FK → `participants.id` (revision
  `0004_participants_accounts`). The participant whose message created the row
  ([ADR-0033](decisions/0033-entered-by-participant-id.md)); display names are looked up from it
  when shown. NULL means "sender not recorded": rows from before the sender was recorded, and
  every row from the CLI or REST API. Not to be confused with the account's owner, which is
  whose card paid and is often not the sender.
- **trip_id**: BIGINT, nullable, FK → `trips.id` (revision `0006_add_trips`). The sender's trip
  when the bot logged the entry ([ADR-0036](decisions/0036-trip-switch-per-person.md)), unless "Not trip" was tapped on the card
  ([ADR-0035](decisions/0035-trips-tagged-at-log-time.md)). Tagged by when the entry was logged,
  not by its `date`. NULL for everyday entries, and always for the CLI and REST API.
- **paid_by_participant_id**: BIGINT, nullable, FK → `participants.id`
  (`fk_transactions_paid_by`, revision `0007_add_paid_by`). Who paid
  ([ADR-0037](decisions/0037-paid-by-person-read-from-text.md)): the account's owner, or for an
  unowned account (Cash, the wallet) the payer the message names, else the sender; for an outside
  payer, that person, on the shared "Paid by someone else" account. Every new bot entry sets it.
  NULL means "not recorded" (older rows, the CLI and REST API) and is read as the account's owner,
  or the sender for an unowned account. Totals skip a row whose payer is set and not household.
- **Dropped** by `0005_drop_text_account_columns` (#53): `account_desc` (the card's name as
  text), `account_owner` (the card owner's display name, inferred by first match) and
  `entered_by` (the sender's Telegram user ID as text). The ids above replace them.

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
- **is_household**: BOOLEAN, NOT NULL, default false (revision `0007_add_paid_by`). Whose
  spending the totals count. The check `ck_participants_bot_user_is_household`
  (`telegram_user_id IS NULL OR is_household`) means every bot user is household; a household
  person need not use Telegram. The migration marked existing bot users true before adding it.
- **active_trip_id**: BIGINT, nullable (revision `0006_add_trips`). This person's trip mode: the
  trip their new bot entries are tagged with, NULL when off. A composite FK
  (`fk_participants_active_trip_member`: `(active_trip_id, id)` → `trip_members (trip_id,
  participant_id)`) allows only a trip they are a member of. Switched from `/trip`'s buttons,
  never by the seed script ([ADR-0036](decisions/0036-trip-switch-per-person.md)).
- **trip_banner_message_id**: BIGINT, nullable (revision `0006_add_trips`). The Telegram message
  pinned in this person's private chat while their trip mode is on, so the old banner can be
  removed on a switch (deleted, or unpinned and edited to say the trip ended if it is too old).
- **created_at**: TIMESTAMPTZ, NOT NULL, `DEFAULT now()`

**accounts**
- **id**: BIGINT identity, primary key
- **name**: TEXT, NOT NULL. The name the extraction prompt offers and the card shows.
- **owner_participant_id**: BIGINT, nullable, FK → `participants.id`. NULL means shared (Cash,
  the YouTrip wallet): the money is the sender's, or that of the payer the message names. One
  shared row, "Paid by someone else", holds every entry paid by someone outside the household. It
  is left out of the extraction prompt's list: only the outside-payer rule puts an entry on it
  ([ADR-0037](decisions/0037-paid-by-person-read-from-text.md)).
- **funded_by_account_id**: BIGINT, nullable, FK → `accounts.id`. Only on the wallet: the card a
  YouTrip top-up is paid from, whoever sends it.
- **closed_on**: DATE, nullable. A closed card keeps its real name and its id, so old rows stay
  linked; the bot asks before saving a new entry against it.
- **expires_on**: DATE, nullable. The last day the card is valid: printed `09/29` is stored as
  `2029-09-30`. NULL for anything without an expiry (cash, PayNow, a bank account). Recorded only;
  nothing acts on it yet.
- **created_at**: TIMESTAMPTZ, NOT NULL, `DEFAULT now()`

Two open accounts can't share a name, ignoring case (`uq_accounts_open_name_ci`, a partial
unique index on `lower(name)` where `closed_on IS NULL`). A closed card and its open replacement
can.

## trips and trip_members (revision `0006_add_trips`)

A trip is a named group of participants in families
([ADR-0035](decisions/0035-trips-tagged-at-log-time.md)). Rows come from the gitignored seed file
via `scripts/seed_directory.py`.

**trips**
- **id**: BIGINT identity, primary key
- **name**: TEXT, NOT NULL. Unique ignoring case (`uq_trips_name_ci`), so two trips can't look
  the same on `/trip`'s buttons.
- **start_date**, **end_date**: DATE, NOT NULL, with `end_date >= start_date` (`ck_trips_dates`).
  They sort `/trip`'s list and label its buttons; they never tag an entry
  ([ADR-0036](decisions/0036-trip-switch-per-person.md)).
- **created_at**: TIMESTAMPTZ, NOT NULL, `DEFAULT now()`

**trip_members**
- **trip_id**: BIGINT, FK → `trips.id`; with `participant_id`, the primary key
- **participant_id**: BIGINT, FK → `participants.id`
- **family**: TEXT, NOT NULL. A label shared by everyone on the trip who settles together. Stored
  per trip, so a person can be grouped differently on another trip.

## transaction_splits (revision `0008_add_transaction_splits`)

One whole-cent share per person on an entry. No rows means "not split": the entry is the
payer's alone. Shares add up to the entry's amount, checked in Python before the shares are
inserted and written in the same database transaction as the entry, only when it was inserted.

- **transaction_id**: TEXT, NOT NULL, FK → `raw_transactions.transaction_id` (was
  `transactions` until revision `0010_soft_delete`), ON DELETE CASCADE. A soft delete leaves
  these rows in place; readers reach them through the view, so they drop out with the entry.
- **participant_id**: BIGINT, NOT NULL, FK → `participants.id`
- **share_cents**: BIGINT, NOT NULL, with `share_cents >= 0`
  (`ck_transaction_splits_share_not_negative`)
- Primary key: (`transaction_id`, `participant_id`)

## settlements (revision `0009_add_settlements`)

One person-to-person payback, separate from spending (ADR-0046). The clears side reduces
trip debt; the paid side records what changed hands, with no exchange-rate lookup.

- **id**: BIGINT GENERATED ALWAYS AS IDENTITY, primary key
- **trip_id**: BIGINT, nullable, FK → `trips.id`; indexed by `ix_settlements_trip_id`
- **from_participant_id**, **to_participant_id**: BIGINT, NOT NULL, FK → `participants.id`;
  must differ (`ck_settlements_different_people`)
- **for_participant_id**: BIGINT, nullable, FK → `participants.id`; a member of the unit
  whose debt this payback clears on their behalf (ADR-0047). NULL means the payer's unit, or,
  on a payback with `settlement_bills` rows, that the rows say whose shares it pays. Must
  differ from `to_participant_id` (`ck_settlements_different_beneficiary`).
- **clears_cents**, **paid_cents**: BIGINT, NOT NULL, positive whole cents
  (`ck_settlements_positive_clears`, `ck_settlements_positive_paid`)
- **clears_currency**, **paid_currency**: TEXT, NOT NULL. When equal, the amounts must be
  equal too (`ck_settlements_same_currency_amount`).
- **date**: DATE, NOT NULL, business date
- **created_at**: TIMESTAMPTZ, NOT NULL, `DEFAULT now()`, write time only
- **entered_by_participant_id**: BIGINT, nullable, FK → `participants.id`
- **source**: TEXT, NOT NULL; the bot writes `telegram_bot`
- **note**: TEXT, nullable
- **idempotency_key**: TEXT, NOT NULL, UNIQUE (`uq_settlements_idempotency_key`)
- **fx_rate**: NUMERIC, nullable; bills' currency per 1 of the paid currency, positive
  (`ck_settlements_positive_fx_rate`). Added by `0011_settlement_bills` for ADR-0053.
- **fx_rate_date**: DATE, nullable; the date of that rate
- **fx_source**: TEXT, nullable; where the rate came from (`frankfurter` or `typed`). The three
  fx columns are all NULL or all set (`ck_settlements_fx_all_or_none`); NULL on every
  same-currency payback and every row from before ADR-0053 ships.
- **needs_statement_check**: BOOLEAN, NOT NULL, `DEFAULT false`; true for a two-currency
  payback until its bill's real rate is checked against the card statement

Overpayments are allowed: the excess is owed back to the person who paid. The writer checks
positive money, different people and equal same-currency amounts before connecting. Retries
use `ON CONFLICT (idempotency_key) DO NOTHING`; paybacks never affect spending totals.

## settlement_bills (revision `0011_settlement_bills`)

Which bill shares a payback clears (ADR-0052). One row per person's share of one bill; one
payback has as many rows as shares it pays. Old paybacks have none and still count, clearing a
line's oldest bills first.

- **settlement_id**: BIGINT, NOT NULL, FK → `settlements.id`
- **transaction_id**: TEXT, NOT NULL, FK → `raw_transactions.transaction_id` (a foreign key
  can't point at the `transactions` view); indexed by `ix_settlement_bills_transaction_id`
- **for_participant_id**: BIGINT, NOT NULL, FK → `participants.id`; whose share it clears
- **cents**: BIGINT, NOT NULL, positive (`ck_settlement_bills_positive_cents`), in the
  payback's `clears_currency`
- Primary key: (`settlement_id`, `transaction_id`, `for_participant_id`)

No `ON DELETE` on any key: bills are only soft-deleted, so the keys never fire in normal use. A
row on a deleted bill clears nothing; its cents are owed back to the giver. If someone other
than the receiver paid that bill, the payback's offset chain fails as a whole (ADR-0052). A row clears only a
share owed to the payback's receiver, on a bill in `clears_currency`, and never more than is
still open on that share. The rows add up to at most `clears_cents`; the rest is
an overpayment. They are written in the same database transaction as the payback, only when
the payback row was really inserted.

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
