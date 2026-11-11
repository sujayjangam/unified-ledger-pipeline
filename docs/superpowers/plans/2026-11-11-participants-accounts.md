# Participants and Accounts as Postgres Tables — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `ALLOWED_TG_IDS` / `ACCOUNT_OWNERS` / `PRIMARY_ACCOUNT_OWNER` env vars with
`participants` and `accounts` tables, and link every transaction to them by id.

**Architecture:** Two hand-written Alembic migrations bracket the change: 0004 adds the tables
and nullable id columns, 0005 makes `account_id` required and drops the old text columns. A new
`app/services/participants.py` holds a `Directory` value loaded from Postgres per message; every
rule that read the env vars becomes a pure function over a `Directory`. A one-off script seeds the
tables from a gitignored file and backfills the old rows.

**Tech Stack:** Python 3.11, SQLAlchemy Core + psycopg 3, Alembic, python-telegram-bot, FastAPI,
pytest.

**Spec:** `docs/superpowers/specs/2026-11-11-participants-accounts-design.md`

## Global Constraints

- No real names or card labels anywhere in the repo, including tests, examples and commit
  messages. Tests use fictional names (Alice, Bob, Card A, Card B) and fictional Telegram IDs
  (`"111111"`, `"222222"`).
- Seed data comes from the gitignored `directory_seed.local.json`, never from a migration.
- `tests/` never opens a database connection (`tests/conftest.py` enforces it). Only
  `load_directory` and the seed script touch Postgres.
- The three env vars are removed, not kept as a fallback.
- No cache: the directory is loaded per incoming update.
- Ids are `BIGINT GENERATED ALWAYS AS IDENTITY`, never set by hand.
- Run Python as `micromamba run -n ledger-env <cmd>` from the repo root.
- Never run `alembic upgrade` or the seed script against production except in the explicit
  operations tasks below. Every other run targets a Neon branch, with `alembic current` /
  the script's printed host checked first (`docs/LOCAL_TESTING.md`).
- Follow the repo's comment style: docstrings plus inline "why" comments.

## Corrections to the spec found while planning

- The card-name column on `transactions` is **`account_desc`**, not `payment_method`
  (`payment_method` is only the key on the in-memory pending entry). Migration 0005 drops
  `account_desc`, `account_owner`, `entered_by`.
- `/recent`, `/today`, `/week`, `/month` and `/cat_*` never show the card or owner, so they need no
  join. The only display that names a person is the duplicate warning banner.
- Migration 0005's downgrade can **refill** the three columns from the ids (card name from
  `accounts`, Telegram ID from `participants`, whose card paid as derived), rather than leaving
  them empty. Only the old first-match `account_owner` guesses on shared accounts are lost. The
  fresh `pg_dump` before applying it stays.

## Review Focus

1. **Directory unreachable between card and Confirm.** The pending entry already carries
   `account_id` and `entered_by_participant_id`, so Confirm must save without loading the
   directory. Pinned in Task 7.
2. **The model names a payment method that isn't an open account** (invented, or a closed card's
   name). It falls back to the default rule, and the card shows the account actually chosen.
   Pinned in Task 2.
3. **Name spacing and case**: `" card a "` and `"CARD A"` resolve to `Card A`, in the bot and in
   the backfill. Pinned in Tasks 2 and 3.
4. **Closed-date boundary**: an account is open on a date `d` when `closed_on` is NULL or
   `d <= closed_on` (`closed_on` is the last day it was used). A transaction dated exactly on
   `closed_on` matches it. Pinned in Task 3.
5. **One bad row locks everyone out** (fail-closed by design). The error must name the row so it
   can be fixed in the SQL editor in one go, and list every problem, not only the first. Pinned in
   Task 2.

## PRs and operations order

| PR | Tasks | Before merging | After merging |
|---|---|---|---|
| 1 | 1, 2, 3 | Apply 0004 to production (Task 4) | Seed + backfill production (Task 4) |
| 2 | 5, 6, 7, 8 | Seed run on production done | Rerun backfill; remove env vars (Task 9) |
| 3 | 10 | Task 9 done; bot used for real for a few days | — |
| 4 | 11 | PR 3 merged and deployed; fresh `pg_dump` | — (0005 applied right after merge, Task 11) |

PR 1 changes nothing the running bot uses. PR 2 is the switch-over and must not merge before the
production seed exists, or the fail-closed allowlist locks everyone out.

## Shared test fixture

Task 2 adds a `directory` fixture to `tests/conftest.py`, reused by Tasks 3, 5, 6, 7:

| Participant | id | telegram_user_id | default |
|---|---|---|---|
| Alice | 1 | `"111111"` | Card A (10) |
| Bob | 2 | `"222222"` | Card B (11) |
| Carol | 3 | None | None |

| Account | id | owner | funded_by | closed_on |
|---|---|---|---|---|
| Card A | 10 | Alice | — | — |
| Card B | 11 | Bob | — | — |
| Cash | 12 | shared | — | — |
| YouTrip | 13 | shared | Card A (10) | — |
| Old Card | 14 | Alice | — | 2026-03-31 |

---

### Task 1: Migration 0004 (additive)

**Files:**
- Create: `alembic/versions/0004_add_participants_and_accounts.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: tables `participants`, `accounts`; nullable `transactions.account_id`,
  `transactions.entered_by_participant_id`. Revision id `"0004_participants_accounts"`.

- [ ] **Step 1: Write the failing test**

```python
def test_participants_accounts_revises_entered_by():
    entered_by = _load(VERSIONS / "0003_add_entered_by.py")
    directory = _load(VERSIONS / "0004_add_participants_and_accounts.py")
    assert directory.down_revision == entered_by.revision
```

- [ ] **Step 2: Run it to verify it fails**

Run: `micromamba run -n ledger-env pytest tests/test_migrations.py -v`
Expected: FAIL, file not found.

- [ ] **Step 3: Write the migration**

`revision = "0004_participants_accounts"`, `down_revision = "0003_add_entered_by"`. The module
docstring explains the why, like 0003's: #53, why ids not text, why no seed data here (public repo).

`upgrade()`, in this order, because the two tables reference each other:
1. `participants`: `id` BigInteger `sa.Identity(always=True)` PK; `display_name` Text not null,
   unique; `telegram_user_id` Text nullable, unique; `default_account_id` BigInteger nullable (FK
   added in step 3); `created_at` TIMESTAMP(timezone=True) not null, `server_default=sa.func.now()`.
2. `accounts`: `id` as above; `name` Text not null; `owner_participant_id` BigInteger FK →
   `participants.id` nullable; `funded_by_account_id` BigInteger FK → `accounts.id` nullable;
   `closed_on` Date nullable; `created_at` as above.
3. `op.create_foreign_key("fk_participants_default_account", "participants", "accounts",
   ["default_account_id"], ["id"])`.
4. Partial unique index `uq_accounts_open_name_ci` on `sa.text("lower(name)")`,
   `postgresql_where=sa.text("closed_on IS NULL")`.
5. `transactions.account_id` BigInteger FK → `accounts.id` nullable (`fk_transactions_account`)
   and `transactions.entered_by_participant_id` BigInteger FK → `participants.id` nullable
   (`fk_transactions_entered_by_participant`).

All FKs keep the default `ON DELETE` (no action), which is what blocks deletes.

`downgrade()` reverses it: drop the two transaction columns, the index, the participants FK, then
`accounts`, then `participants`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `micromamba run -n ledger-env pytest tests/test_migrations.py -v`
Expected: all PASS, including `test_revision_chain_has_exactly_one_head`.

- [ ] **Step 5: Verify against a throwaway Neon branch**

Create a Neon branch from production; set `DATABASE_URL` in the shell to it.
Run: `micromamba run -n ledger-env alembic current` → prints the branch host and `0003_add_entered_by`.
Run: `alembic upgrade head`, then `alembic downgrade -1`, then `alembic upgrade head`.
Expected: all three succeed; `\d participants` / `\d accounts` in `psql` show the columns, FKs and index.
Also check by hand: inserting two open accounts named `Card A` and `card a` fails on
`uq_accounts_open_name_ci`; deleting an account a participant defaults to fails on the FK.

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0004_add_participants_and_accounts.py tests/test_migrations.py
git commit -m "feat: migration 0004, participants and accounts tables (#53)"
```

### Task 2: `app/services/participants.py`

**Files:**
- Create: `app/services/participants.py`
- Create: `tests/test_participants.py`
- Modify: `tests/conftest.py` (add the `directory` fixture from "Shared test fixture")

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Participant: id: int; display_name: str; telegram_user_id: str | None; default_account_id: int | None`
  - `@dataclass(frozen=True) class Account: id: int; name: str; owner_participant_id: int | None; funded_by_account_id: int | None; closed_on: date | None`, plus `def is_open_on(self, d: date) -> bool` (`closed_on is None or d <= closed_on`) and property `is_open` (`closed_on is None`).
  - `@dataclass(frozen=True) class Directory: participants: tuple[Participant, ...]; accounts: tuple[Account, ...]` with methods
    `participant(id: int | None) -> Participant | None`,
    `participant_by_telegram_id(tg_id: str) -> Participant | None`,
    `account(id: int | None) -> Account | None`,
    `open_account_named(name: str | None) -> Account | None` (matches `name.strip().casefold()`),
    `open_account_names() -> list[str]` (open accounts, in id order).
  - `class DirectoryError(Exception)`
  - `def validate_directory(directory: Directory) -> list[str]` — every problem as a sentence naming the row's id and name; empty list means valid.
  - `def load_directory(conn) -> Directory` — two `SELECT`s naming their columns, ordered by id; raises `DirectoryError("; ".join(problems))` if `validate_directory` finds any.
  - `def resolve_payment(transaction: dict, directory: Directory, sender: Participant) -> dict` — mutates and returns the dict, setting `account_id`, `payment_method` (the account's name), `account_owner` (display name from `whose_card_paid`, or `"Not recorded"`), and `transaction_type = "Transfer"` for a top-up.
  - `def whose_card_paid(account: Account, sender: Participant | None, directory: Directory) -> Participant | None` — the owner, or `sender` when the account is shared.
  - Constant `WALLET_NAME = "YouTrip"`.

- [ ] **Step 1: Write the failing tests** in `tests/test_participants.py`, using the `directory` fixture

```python
def test_open_account_named_ignores_case_and_spaces(directory):
    assert directory.open_account_named("  card a ").id == 10
    assert directory.open_account_named("CARD A").id == 10

def test_open_account_named_skips_closed_accounts(directory):
    assert directory.open_account_named("Old Card") is None

def test_open_account_names_leaves_out_closed_accounts(directory):
    assert directory.open_account_names() == ["Card A", "Card B", "Cash", "YouTrip"]

def test_account_is_open_on_its_closed_on_date_but_not_after(directory):
    old = directory.account(14)
    assert old.is_open_on(date(2026, 3, 31))
    assert not old.is_open_on(date(2026, 4, 1))

def test_shared_account_resolves_to_the_sender(directory):
    bob = directory.participant(2)
    assert whose_card_paid(directory.account(12), bob, directory) == bob

def test_single_owner_card_resolves_to_its_owner_whoever_sends(directory):
    bob = directory.participant(2)
    assert whose_card_paid(directory.account(10), bob, directory).display_name == "Alice"

def test_shared_account_with_no_sender_is_not_recorded(directory):
    assert whose_card_paid(directory.account(12), None, directory) is None

def test_topup_is_paid_from_the_wallets_funding_card_whoever_sends(directory):
    t = resolve_payment({"category": "YouTrip top-up", "currency": "SGD"}, directory, directory.participant(2))
    assert (t["account_id"], t["payment_method"], t["account_owner"], t["transaction_type"]) == (10, "Card A", "Alice", "Transfer")

def test_named_card_is_kept(directory):
    t = resolve_payment({"payment_method": "card b", "currency": "SGD"}, directory, directory.participant(1))
    assert (t["account_id"], t["payment_method"], t["account_owner"]) == (11, "Card B", "Bob")

def test_missing_card_in_sgd_uses_the_senders_default(directory):
    t = resolve_payment({"payment_method": None, "currency": "SGD"}, directory, directory.participant(2))
    assert t["account_id"] == 11

def test_missing_card_in_foreign_currency_uses_the_wallet(directory):
    t = resolve_payment({"payment_method": None, "currency": "MYR"}, directory, directory.participant(2))
    assert (t["account_id"], t["account_owner"]) == (13, "Bob")

@pytest.mark.parametrize("name", ["Made Up Card", "Old Card"])
def test_unknown_or_closed_card_falls_back_to_the_default_rule(directory, name):
    t = resolve_payment({"payment_method": name, "currency": "SGD"}, directory, directory.participant(1))
    assert (t["account_id"], t["payment_method"]) == (10, "Card A")

def test_valid_directory_has_no_problems(directory):
    assert validate_directory(directory) == []

def test_validation_lists_every_problem_and_names_each_row(directory):
    broken = replace(directory, participants=(
        replace(directory.participant(1), default_account_id=14),   # closed default
        replace(directory.participant(2), default_account_id=None), # no default
    ), accounts=directory.accounts[:3] + (replace(directory.account(13), funded_by_account_id=14),) + directory.accounts[4:])
    problems = validate_directory(broken)
    assert len(problems) == 3
    assert any("Alice" in p and "1" in p for p in problems)
    assert any("Bob" in p for p in problems)
    assert any("YouTrip" in p and "13" in p for p in problems)

def test_participant_without_telegram_id_needs_no_default(directory):
    assert directory.participant(3).default_account_id is None
    assert validate_directory(directory) == []

def test_load_directory_raises_with_the_problems():
    participants = MagicMock(all=lambda: [(1, "Alice", "111111", None)])  # no default account
    accounts = MagicMock(all=lambda: [(10, "Card A", 1, None, None)])
    conn = MagicMock()
    conn.execute.side_effect = [participants, accounts]
    with pytest.raises(DirectoryError, match="Alice"):
        load_directory(conn)
```

Row tuples follow the SELECTs' column order: participants `(id, display_name, telegram_user_id,
default_account_id)`, accounts `(id, name, owner_participant_id, funded_by_account_id, closed_on)`.

Top-up fallback rules (no test-determined choice otherwise): if there is no open `YouTrip` account
or it has no `funded_by_account_id`, the top-up uses the sender's default account, still
`Transfer`. A foreign-currency payment with no open `YouTrip` uses the sender's default.

- [ ] **Step 2: Run to verify they fail**

Run: `micromamba run -n ledger-env pytest tests/test_participants.py -v`
Expected: FAIL, `ModuleNotFoundError: app.services.participants`.

- [ ] **Step 3: Implement the module** with the signatures above. Validation rules, one problem
each: a participant with a `telegram_user_id` and no `default_account_id`; a participant whose
default account is closed or missing; an account whose `funded_by_account_id` points at a closed or
missing account.

- [ ] **Step 4: Run to verify they pass**

Run: `micromamba run -n ledger-env pytest tests/test_participants.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/participants.py tests/test_participants.py tests/conftest.py
git commit -m "feat: Directory of participants and accounts, with payment rules as pure functions (#53)"
```

### Task 3: Seed and backfill script

**Files:**
- Create: `scripts/seed_directory.py`
- Create: `directory_seed.example.json` (fictional names only)
- Modify: `.gitignore` (add `directory_seed.local.json`)
- Create: `tests/test_seed_directory.py`

**Interfaces:**
- Consumes: `Account` from Task 2.
- Produces (pure, tested):
  - `def parse_seed(raw: dict) -> SeedData` — raises `ValueError` naming the bad entry. `SeedData` is a frozen dataclass of `participants: list[dict]`, `accounts: list[dict]`, `aliases: dict[str, str]` (keys stored casefolded and stripped), `transaction_overrides: dict[str, str]`.
  - `def match_account(account_desc: str | None, txn_date: date, accounts: list[Account], aliases: dict[str, str]) -> tuple[str, int | None]` — returns `("matched", id)`, `("unmatched", None)` or `("ambiguous", None)`.
- CLI: `python -m scripts.seed_directory [--seed PATH] [--write]`, default seed path `directory_seed.local.json`.

Seed file format (the example file, fictional):

```json
{
  "participants": [
    {"display_name": "Alice", "telegram_user_id": "111111", "default_account": "Card A"},
    {"display_name": "Bob", "telegram_user_id": "222222", "default_account": "Card B"}
  ],
  "accounts": [
    {"name": "Card A", "owner": "Alice"},
    {"name": "Card B", "owner": "Bob"},
    {"name": "Cash", "owner": null},
    {"name": "YouTrip", "owner": null, "funded_by": "Card A"},
    {"name": "Old Card", "owner": "Alice", "closed_on": "2026-03-31"}
  ],
  "aliases": {"card a typo": "Card A"},
  "transaction_overrides": {"00000000-0000-0000-0000-000000000000": "Cash"}
}
```

`aliases` maps old `account_desc` text (a typo or a made-up name) to an account name.
`transaction_overrides` maps one `transaction_id` to an account name, for rows with no
`account_desc` or ones the date can't settle.

- [ ] **Step 1: Write the failing tests**

```python
def test_match_ignores_case_and_spaces():
    assert match_account(" CARD A ", date(2026, 5, 1), ACCOUNTS, {}) == ("matched", 10)

def test_match_uses_an_alias():
    assert match_account("Card A Typo", date(2026, 5, 1), ACCOUNTS, {"card a typo": "Card A"}) == ("matched", 10)

def test_reused_name_picks_the_account_open_on_the_date():
    # Old Card (14) closed 2026-03-31; an open replacement with the same name is id 15.
    accounts = ACCOUNTS + [Account(15, "Old Card", 1, None, None)]
    assert match_account("Old Card", date(2026, 3, 31), accounts, {}) == ("ambiguous", None)
    assert match_account("Old Card", date(2026, 4, 1), accounts, {}) == ("matched", 15)

def test_two_closed_accounts_covering_the_date_are_ambiguous():
    accounts = ACCOUNTS + [Account(16, "Old Card", 1, None, date(2026, 6, 30))]
    assert match_account("Old Card", date(2026, 3, 1), accounts, {}) == ("ambiguous", None)

def test_closed_account_matches_up_to_its_closed_on_date():
    assert match_account("Old Card", date(2026, 3, 31), ACCOUNTS, {}) == ("matched", 14)

def test_unknown_or_missing_name_is_unmatched():
    assert match_account("Nope", date(2026, 5, 1), ACCOUNTS, {}) == ("unmatched", None)
    assert match_account(None, date(2026, 5, 1), ACCOUNTS, {}) == ("unmatched", None)

def test_seed_rejects_a_default_that_is_not_a_listed_account():
    with pytest.raises(ValueError, match="Alice"):
        parse_seed({"participants": [{"display_name": "Alice", "telegram_user_id": "111111", "default_account": "Nope"}], "accounts": []})

def test_seed_rejects_an_owner_who_is_not_a_listed_participant():
    with pytest.raises(ValueError, match="Card A"):
        parse_seed({"participants": [], "accounts": [{"name": "Card A", "owner": "Nobody"}]})

def test_the_example_seed_file_parses():
    parse_seed(json.loads(Path("directory_seed.example.json").read_text()))
```

`ACCOUNTS` is a module-level list built from the shared fixture's accounts. Matching rule:
candidates are the accounts whose name (after alias lookup) equals the text, ignoring case and
spaces, and that are open on the transaction's date. Exactly one → matched; none → unmatched; two or
more → ambiguous (on 2026-03-31 both the closing card and its open replacement qualify, so a human
decides with an override).

- [ ] **Step 2: Run to verify they fail**

Run: `micromamba run -n ledger-env pytest tests/test_seed_directory.py -v`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement the script.**

Pure functions as above. `main()`:
1. Prints the database host from `get_engine().url.host` and whether this is a dry run.
2. Opens one connection and one transaction (`conn.begin()`); the dry run rolls back at the end,
   `--write` commits. So the dry run exercises the real inserts and prints exactly what would
   change.
3. Inserts participants missing by `display_name`, then accounts missing by
   (`lower(name)`, `closed_on`), then sets each `default_account_id` and `funded_by_account_id`
   by name. Existing rows are left alone, so a rerun is safe.
4. Backfills `account_id` for rows `WHERE account_id IS NULL`: override by `transaction_id` first,
   else `match_account` with the row's `date` (parse the TEXT `YYYY-MM-DD`).
5. Backfills `entered_by_participant_id` for rows `WHERE entered_by_participant_id IS NULL AND
   entered_by IS NOT NULL`, by `telegram_user_id`.
6. Prints a report: counts filled, then every unmatched or ambiguous row as
   `transaction_id  date  account_desc`, grouped by `account_desc`, so each distinct name is
   decided once (alias, or a closed account in the seed).

- [ ] **Step 4: Run to verify the tests pass**

Run: `micromamba run -n ledger-env pytest tests/test_seed_directory.py -v`
Expected: PASS.

- [ ] **Step 5: Dry run and write against the Neon branch from Task 1**

Copy the example to `directory_seed.local.json` (fictional values are fine on a branch).
Run: `micromamba run -n ledger-env python -m scripts.seed_directory`
Expected: prints the branch host, "DRY RUN", the report; a second dry run reports the same.
Run with `--write`, then once more with `--write`.
Expected: the second write inserts nothing and fills nothing.

- [ ] **Step 6: Commit**

```bash
git add scripts/seed_directory.py directory_seed.example.json .gitignore tests/test_seed_directory.py
git commit -m "feat: seed and backfill script for participants and accounts (#53)"
```

Open PR 1 (Tasks 1-3) after Task 4's "before merge" step.

### Task 4: Operations — apply 0004 and seed production

No code. Each step has a checkable result.

- [ ] **Step 1:** Write the real `directory_seed.local.json`. Confirm `git status` doesn't list it.
- [ ] **Step 2:** Rehearse on a fresh Neon branch from production: `alembic upgrade head`, then the
  seed dry run. Add an alias or closed account for every unmatched name, and an override for every
  row with no `account_desc`, until the dry run reports zero unmatched and zero ambiguous. Then
  `--write` on the branch.
- [ ] **Step 3:** Production: `alembic current` prints the production host and
  `0003_add_entered_by`; run `alembic upgrade head`; `alembic current` prints
  `0004_participants_accounts`.
- [ ] **Step 4:** Merge PR 1.
- [ ] **Step 5:** Production seed dry run → zero unmatched; then `--write`. In the Neon SQL editor,
  `SELECT count(*) FROM transactions WHERE account_id IS NULL` returns 0.

### Task 5: Dual-write in `add_expense` and the duplicate query

**Files:**
- Modify: `app/add_expense.py:47-110`
- Modify: `app/services/ledger_queries.py:28-62`
- Test: `tests/test_add_expense.py`

**Interfaces:**
- Produces:
  - `add_expense(date_str, description, amount_dollars, category, currency="SGD", transaction_type="Expense", account_desc=None, account_owner=None, source="Manual CLI", idempotency_key=None, entered_by=None, account_id=None, entered_by_participant_id=None) -> bool` — inserts the two new columns alongside the old ones.
  - `find_recent_duplicate(...)` returns `description`, `seconds_ago`, `entered_by_participant_id` (no longer `entered_by`).

- [ ] **Step 1: Write the failing tests**

```python
def test_add_expense_writes_both_old_and_new_columns(monkeypatch):
    conn = fake_connection(monkeypatch)  # MagicMock pattern from tests/test_rest_api.py
    add_expense("2026-11-11", "lunch", 12, "Food", account_desc="Card A", account_owner="Alice",
                entered_by="111111", account_id=10, entered_by_participant_id=1)
    params = conn.execute.call_args.args[1]
    assert (params["account_id"], params["entered_by_participant_id"]) == (10, 1)
    assert (params["account_desc"], params["account_owner"], params["entered_by"]) == ("Card A", "Alice", "111111")

def test_duplicate_query_selects_the_participant_id(monkeypatch):
    conn = fake_connection(monkeypatch, module=ledger_queries)
    find_recent_duplicate(1200, "SGD")
    assert "entered_by_participant_id" in str(conn.execute.call_args.args[0])
```

- [ ] **Step 2: Run to verify they fail**

Run: `micromamba run -n ledger-env pytest tests/test_add_expense.py -v`
Expected: the two new tests FAIL.

- [ ] **Step 3: Implement** — add the two columns to the `INSERT` column list, `VALUES` and params;
change the duplicate `SELECT`. Update both docstrings.

- [ ] **Step 4: Run the whole suite**

Run: `micromamba run -n ledger-env pytest`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/add_expense.py app/services/ledger_queries.py tests/test_add_expense.py
git commit -m "feat: write account_id and entered_by_participant_id alongside the old columns (#53)"
```


### Task 6: Extraction takes the account names as a parameter

**Files:**
- Modify: `app/services/extraction.py:14-64` (delete `_load_account_owners`, `build_allowed_accounts`), `:160-176`
- Modify: `scripts/check_extraction.py`
- Modify: `tests/test_extraction.py:140-182` (delete the `build_allowed_accounts` tests)

**Interfaces:**
- Produces: `async def extract_transactions(transcript_text: str, today: date, allowed_accounts: list[str]) -> dict | None`. The prompt joins `allowed_accounts` with `", "`, exactly where it does now. The module no longer reads any env var except `OPENAI_API_KEY`.
- `scripts/check_extraction.py` passes a module-level `FICTIONAL_ACCOUNTS = ["Card A", "Card B", "Cash", "YouTrip"]`.

- [ ] **Step 1: Write the failing test**

```python
async def test_prompt_lists_exactly_the_accounts_passed_in(monkeypatch):
    parse = AsyncMock(side_effect=RuntimeError("stop"))  # capture the call, skip the network
    monkeypatch.setattr(extraction.openai_client.beta.chat.completions, "parse", parse)
    await extraction.extract_transactions("lunch 12", date(2026, 11, 11), ["Card A", "Cash"])
    system = parse.call_args.kwargs["messages"][0]["content"]
    assert "The only valid payment methods are: Card A, Cash." in system
```

- [ ] **Step 2: Run to verify it fails**

Run: `micromamba run -n ledger-env pytest tests/test_extraction.py -v`
Expected: FAIL, unexpected argument.

- [ ] **Step 3: Implement**, and update the call in `scripts/check_extraction.py`. Keep the
  suite green until Task 7 by moving the old flattening into `bot_core.py` as a private
  `_allowed_accounts_from_env()` passed at the one call site; Task 7 deletes it with the rest of
  the env parsing.

- [ ] **Step 4: Run the suite and the import smoke check**

Run: `micromamba run -n ledger-env pytest`
Run: `micromamba run -n ledger-env python -c "import app.services.extraction, scripts.check_extraction"`
Expected: all PASS; imports succeed with no `.env`-dependent error.

- [ ] **Step 5: Run the real-model check**

Run: `micromamba run -n ledger-env python -m scripts.check_extraction`
Expected: the same pass rates as before the change (the prompt text is unchanged apart from the list's source).

- [ ] **Step 6: Commit**

```bash
git add app/services/extraction.py scripts/check_extraction.py tests/test_extraction.py
git commit -m "refactor: extraction takes the open account names as a parameter (#53)"
```

### Task 7: Bot switch-over

**Files:**
- Modify: `app/bot_core.py` (env parsing at lines 20-31, `is_authorized` 41-48, every handler's
  auth call, `apply_payment_defaults` 214-260 removed, `_duplicate_banner` 397-418,
  `process_expense_text` 435-527, the `add_expense` call 640-652)
- Modify: `app/bot_local.py` (docstring lines 8-9 mention the env vars)
- Modify: `tests/test_bot_core.py`, `tests/test_button_callback.py`, `tests/test_duplicate_warning.py`, `tests/test_dates.py`

**Interfaces:**
- Consumes: `Directory`, `DirectoryError`, `load_directory`, `resolve_payment`, `whose_card_paid` (Task 2); `add_expense(..., account_id, entered_by_participant_id)` and `find_recent_duplicate` returning `entered_by_participant_id` (Task 5); `extract_transactions(text, today, allowed_accounts)` (Task 6).
- Produces:
  - `def _load_directory() -> Directory` — opens `get_connection()` and calls `load_directory`. The one seam tests monkeypatch.
  - `async def authorize(update: Update) -> Directory | None` — replaces `is_authorized`. Load failure (any exception) → logs it, replies `"Couldn't reach the ledger, try again in a minute. 🛠️"`, returns None. Unknown Telegram ID → the existing unauthorized reply, returns None.
  - `process_expense_text(update, context, raw_text, status_msg, directory: Directory)`.
  - Pending entry keys added: `account_id`, `entered_by_participant_id` (the sender's id). `entered_by` (Telegram ID string), `payment_method` and `account_owner` stay for the dual-write.

- [ ] **Step 1: Update and add the tests**

Replace every `monkeypatch.setattr(bot_core, "ALLOWED_TG_IDS" | "ACCOUNT_OWNERS" | "PRIMARY_ACCOUNT_OWNER", ...)`
with `monkeypatch.setattr(bot_core, "_load_directory", lambda: directory)` using the shared
fixture. Delete the `apply_payment_defaults` tests in `tests/test_bot_core.py` (Task 2 covers the
rules). New tests:

```python
async def test_authorize_fails_closed_when_the_directory_cannot_load(monkeypatch):
    def boom(): raise DirectoryError("Participant 1 (Alice) has no default account")
    monkeypatch.setattr(bot_core, "_load_directory", boom)
    update = fake_update(user_id=111111)
    assert await bot_core.authorize(update) is None
    update.message.reply_text.assert_awaited_once_with("Couldn't reach the ledger, try again in a minute. 🛠️")

async def test_authorize_rejects_a_participant_without_a_telegram_id_match(monkeypatch, directory):
    monkeypatch.setattr(bot_core, "_load_directory", lambda: directory)
    assert await bot_core.authorize(fake_update(user_id=999999)) is None

async def test_text_handler_does_not_call_the_model_when_the_directory_is_down(monkeypatch):
    # _load_directory raises; extract_transactions is a mock that must not be awaited.
    ...
    extract.assert_not_awaited()

async def test_pending_entry_carries_the_ids(monkeypatch, directory):
    # Bob sends "lunch 12 cash": pending entry has account_id 12, entered_by_participant_id 2,
    # account_owner "Bob", entered_by "222222".

async def test_confirm_saves_without_loading_the_directory(monkeypatch, directory):
    # Review Focus 1: a pending card exists; _load_directory now raises; tapping Confirm still
    # calls add_expense with account_id and entered_by_participant_id from the pending entry.

def test_duplicate_banner_names_the_earlier_sender_from_the_directory(directory):
    banner = bot_core._duplicate_banner({"entered_by_participant_id": 1, "seconds_ago": 30, "description": "x"}, "SGD 12.00", sender_id=2, directory=directory)
    assert "Alice logged SGD 12.00" in banner
```

`fake_update` is whatever helper the existing callback tests already use; reuse it rather than
writing a new one.

- [ ] **Step 2: Run to verify the new tests fail**

Run: `micromamba run -n ledger-env pytest tests/test_bot_core.py tests/test_button_callback.py tests/test_duplicate_warning.py tests/test_dates.py -v`
Expected: the new tests FAIL (`authorize` / `_load_directory` don't exist).

- [ ] **Step 3: Implement.**
  - Delete the env-var parsing and `PRIMARY_ACCOUNT_OWNER`.
  - `if not await is_authorized(update): return` becomes `directory = await authorize(update)` / `if directory is None: return` in every handler. Voice and text pass `directory` on.
  - In `process_expense_text`: `sender = directory.participant_by_telegram_id(user_id)`; call `extract_transactions(raw_text, today, directory.open_account_names())` (Task 6); `resolve_payment(single_transaction, directory, sender)` replaces `apply_payment_defaults`; set `entered_by_participant_id = sender.id`.
  - `_duplicate_banner(duplicate, amount_text, sender_id: int, directory: Directory)` compares participant ids and looks the name up with `directory.participant(...)`.
  - The `add_expense` call adds `account_id=` and `entered_by_participant_id=` from the pending entry.
  - `handle_button_click` does not call `authorize` or `_load_directory`.

- [ ] **Step 4: Run the whole suite**

Run: `micromamba run -n ledger-env pytest`
Expected: all PASS. `grep -rn "ALLOWED_TG_IDS\|ACCOUNT_OWNERS\|PRIMARY_ACCOUNT_OWNER" app tests scripts` returns nothing.

- [ ] **Step 5: Commit**

```bash
git add app/bot_core.py app/bot_local.py tests/
git commit -m "feat: bot reads participants and accounts from Postgres, fails closed (#53)"
```

### Task 8: CLI and REST API name an account; docs and ADRs

**Files:**
- Modify: `app/add_expense.py:127-135` (CLI), `app/main.py`
- Modify: `tests/test_rest_api.py`
- Modify: `.env.example`, `README.md`, `CLAUDE.md`, `docs/SCHEMA.md`, `ROADMAP.md`
- Create: `docs/decisions/0032-participants-and-accounts-in-postgres.md`, `docs/decisions/0033-entered-by-participant-id.md`; update `docs/decisions/README.md`

**Interfaces:**
- Consumes: `load_directory`, `Directory.open_account_named` (Task 2); `add_expense(..., account_id=)` (Task 5).
- Produces: CLI `--account NAME` (required). REST `Transaction.account: str` (required); POST resolves it, unknown name → HTTP 422 with `"Unknown or closed account: <name>"`; the `account_owner` field and its `"Shared"` default are removed from the model. GET keeps its response shape until Task 11.

- [ ] **Step 1: Write the failing tests** in `tests/test_rest_api.py`

```python
def test_post_resolves_the_account_name(monkeypatch, directory):
    conn = _fake_db(monkeypatch)
    monkeypatch.setattr(main, "load_directory", lambda conn: directory)
    response = _post(12, account="card a")
    assert response.status_code == 200
    assert conn.execute.call_args.args[1]["account_id"] == 10

def test_post_rejects_an_unknown_account(monkeypatch, directory):
    conn = _fake_db(monkeypatch)
    monkeypatch.setattr(main, "load_directory", lambda conn: directory)
    response = _post(12, account="Old Card")
    assert response.status_code == 422
    assert "Old Card" in response.text
```

Update `_post` to take `account="Card A"`, and give the two existing tests the `directory` patch.

- [ ] **Step 2: Run to verify they fail**

Run: `micromamba run -n ledger-env pytest tests/test_rest_api.py -v`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement the CLI and REST API changes.** The CLI prints
  `Unknown or closed account: <name>` and exits non-zero on an unknown name.

- [ ] **Step 4: Update the docs** (README only describes what has shipped, per `CLAUDE.md`):
  - `.env.example`: remove the three vars and their comments.
  - `CLAUDE.md`: replace the three env-var bullets; rewrite ingestion steps 0 and 5 around
    `Directory` / `resolve_payment`; add `participants.py` and the fail-closed rule to Architecture;
    `entered_by` → `entered_by_participant_id` under Storage; add `scripts/seed_directory.py` under `scripts/`.
  - `docs/SCHEMA.md`: the two new tables and the two new columns; replace the real-name examples on
    line 19 (`'Sujay', 'Wife'`) with fictional ones while editing that line.
  - `README.md`: wherever it describes accounts coming from env vars.
  - ADR-0032: the tables, closed accounts, `funded_by_account_id`, `default_account_id`, fail-closed
    allowlist, no cache. ADR-0033: supersedes ADR-0026, following the superseding convention in
    `docs/decisions/README.md`; ADR-0026's body stays as written.
  - `ROADMAP.md`: Current status and the Phase 1 #53 line; Phase 3 entry gains the groups/trips
    design note from the spec's "Out of scope".

- [ ] **Step 5: Run the suite and the doc check**

Run: `micromamba run -n ledger-env pytest`
Run: `@agent-doc-checker run the check`
Expected: all PASS; the doc checker reports no README claim that the code contradicts.

- [ ] **Step 6: Commit**

```bash
git add app/add_expense.py app/main.py tests/test_rest_api.py .env.example README.md CLAUDE.md docs/ ROADMAP.md
git commit -m "feat: CLI and REST API name an account; docs and ADRs for participants and accounts (#53)"
```

Open PR 2 (Tasks 5-8). Merge only after Task 4 Step 5.

### Task 9: Operations — switch-over

- [ ] **Step 1:** Merge PR 2 and wait for the Cloud Run deploy.
- [ ] **Step 2:** From each person's Telegram, send a shared-account payment (e.g. "coffee 4 cash")
  and a single-owner card payment, and confirm both. The cards show the expected owner (spec
  verification 3).
- [ ] **Step 3:** Insert a test account in the Neon SQL editor; the next message's extraction can
  pick it (send "test 1 <name>", then Cancel). Then close it with `UPDATE accounts SET closed_on =
  current_date WHERE id = <id>` (spec verification 2).
- [ ] **Step 4:** Rerun the seed script against production with `--write`. It reports zero filled
  or only the rows saved between Task 4 and the deploy, and zero unmatched.
- [ ] **Step 5:** Remove `ALLOWED_TG_IDS`, `ACCOUNT_OWNERS`, `PRIMARY_ACCOUNT_OWNER` from the Cloud
  Run service (and their Secret Manager secrets) and from local `.env`. Send one more message; it
  still works.

### Task 10: Stop writing the old columns

**Files:**
- Modify: `app/add_expense.py` (drop the `account_desc`, `account_owner`, `entered_by` params and
  columns), `app/bot_core.py` (stop passing them; the pending entry keeps `payment_method` /
  `account_owner` only for the card text)
- Modify: `app/main.py` (GET reads `accounts.name` via a join instead of `account_owner`, returning it as `account`)
- Test: `tests/test_add_expense.py`, `tests/test_rest_api.py`

- [ ] **Step 1: Change the tests:** the Task 5 test asserts the three old keys are **absent** from
  the params and the `INSERT` text; a REST GET test asserts the SQL joins `accounts`.
- [ ] **Step 2: Run to verify they fail.** `micromamba run -n ledger-env pytest tests/test_add_expense.py tests/test_rest_api.py -v`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the suite.** `micromamba run -n ledger-env pytest` → all PASS;
  `grep -rn "account_desc\|account_owner\|entered_by\b" app` returns only comments and the pending
  card's display keys.
- [ ] **Step 5: Commit** — `git commit -m "feat: stop writing account_desc, account_owner and entered_by (#53)"`, open PR 3, merge, confirm one real save works.

### Task 11: Migration 0005 (contraction)

**Files:**
- Create: `alembic/versions/0005_drop_text_account_columns.py`
- Test: `tests/test_migrations.py`

- [ ] **Step 1: Write the failing test**

```python
def test_contraction_revises_participants_accounts():
    directory = _load(VERSIONS / "0004_add_participants_and_accounts.py")
    contraction = _load(VERSIONS / "0005_drop_text_account_columns.py")
    assert contraction.down_revision == directory.revision
```

- [ ] **Step 2: Run to verify it fails.** `micromamba run -n ledger-env pytest tests/test_migrations.py -v`
- [ ] **Step 3: Write the migration.** `revision = "0005_drop_text_account_columns"`.
  `upgrade()`: first a guard query — if any `transactions.account_id IS NULL`, raise with the count
  and "run scripts/seed_directory.py first", so it fails before changing anything. Then
  `alter_column("transactions", "account_id", nullable=False)` and drop `account_desc`,
  `account_owner`, `entered_by`.
  `downgrade()`: re-add the three as nullable Text, then refill with one `UPDATE ... FROM` each:
  `account_desc = accounts.name`; `entered_by = participants.telegram_user_id` via
  `entered_by_participant_id`; `account_owner` = the owner's `display_name`, or the sender's when
  the account is shared; then make `account_id` nullable again. The docstring says the old
  first-match guesses on shared accounts are not recovered, and to take a `pg_dump` before upgrading.
- [ ] **Step 4: Run the suite.** `micromamba run -n ledger-env pytest` → all PASS.
- [ ] **Step 5: Verify on a fresh Neon branch from production:** `alembic upgrade head`,
  `downgrade -1` (spot-check that 5 rows have their three columns refilled correctly), `upgrade head`.
  Also on a branch with one `account_id` set to NULL by hand: `upgrade head` fails with the guard
  message and the columns still exist.
- [ ] **Step 6: Commit, open PR 4.** `git commit -m "feat: migration 0005, require account_id and drop the text columns (#53)"`
- [ ] **Step 7: Operations:** run the backup workflow by hand (`gh workflow run backup.yml`) and
  wait for it to succeed; merge PR 4; `alembic current` shows the production host, then
  `alembic upgrade head`; send one entry and confirm it. Close #53's sub-issues and #53 once spec
  verification items 1-5 all hold.
