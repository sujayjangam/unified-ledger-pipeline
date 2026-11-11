"""The people and payment accounts the bot knows about, loaded from Postgres (#53).

This replaces three env vars (ALLOWED_TG_IDS, ACCOUNT_OWNERS, PRIMARY_ACCOUNT_OWNER). They were
baked into each Cloud Run revision, so adding a card was a redeploy, and a shared payment method
had no owner model of its own - it was "shared" only because its name appeared under two people,
and the first-match lookup then gave every such payment to the same person.

How the module is split, and why:
  - Participant, Account and Directory are plain frozen dataclasses: one snapshot of both tables.
  - load_directory(conn) is the ONLY function here that touches the database.
  - Every rule (who may use the bot, which card paid, whose card that is) is a pure function that
    takes a Directory. So the tests build a fictional Directory in memory and never need Postgres
    - the same reason apply_payment_defaults() was once pulled out of the bot handler.

The bot loads a fresh Directory for every incoming message (no cache, decided in #53), so a row
inserted in the Neon SQL editor is picked up on the very next message.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text

# The multi-currency wallet. A top-up moves money into it, and a foreign-currency payment that
# names no card is assumed to be on it. Matched by name, like any other account.
WALLET_NAME = "YouTrip"

# Shown on the confirmation card when the chosen card has been closed. Not an error: refunds and
# late charges really do land on a card after it is cancelled, so the user decides.
CLOSED_CARD_WARNING = "⚠️ This card is already closed. Do you want to proceed with saving?"

# Shown instead of a card when a top-up can't be attributed. That is a set-up problem in the
# accounts table, not something wrong with the message, so nothing is guessed and nothing is saved.
NO_WALLET_MESSAGE = "⚠️ No YouTrip wallet set up — add it in the accounts table. Nothing was saved."


class DirectoryError(Exception):
    """The directory couldn't be used: its rows break a rule the bot relies on."""


class WalletNotSetUp(Exception):
    """A top-up arrived but no open YouTrip account with a funding card exists."""


def _key(name: str | None) -> str | None:
    """How names are compared everywhere: spaces trimmed, case ignored. Mirrors the open-name
    unique index in migration 0004, which is on lower(name)."""
    return name.strip().casefold() if name else None


@dataclass(frozen=True)
class Participant:
    id: int
    display_name: str
    # None means tracked (e.g. for splitting a trip) but not allowed to use the bot.
    telegram_user_id: str | None
    default_account_id: int | None


@dataclass(frozen=True)
class Account:
    id: int
    name: str
    # None means shared: a payment on it belongs to whoever sent the message.
    owner_participant_id: int | None
    # Only set on a wallet: the card that tops it up.
    funded_by_account_id: int | None
    # The last day the card was used. None means open.
    closed_on: date | None

    @property
    def is_open(self) -> bool:
        return self.closed_on is None

    def is_open_on(self, d: date) -> bool:
        """Open on a given day. closed_on is the last day it was used, so that day still counts."""
        return self.closed_on is None or d <= self.closed_on


@dataclass(frozen=True)
class Directory:
    participants: tuple[Participant, ...]
    accounts: tuple[Account, ...]

    def participant(self, id: int | None) -> Participant | None:
        return next((p for p in self.participants if p.id == id), None)

    def participant_by_telegram_id(self, tg_id: str) -> Participant | None:
        # The allowlist: only a participant with this Telegram ID may use the bot.
        return next((p for p in self.participants if p.telegram_user_id == tg_id), None)

    def account(self, id: int | None) -> Account | None:
        return next((a for a in self.accounts if a.id == id), None)

    def open_account_named(self, name: str | None) -> Account | None:
        """The open account with this name, or None. Used where a closed card must be refused
        (the CLI and REST API). The database guarantees at most one open match."""
        key = _key(name)
        return next((a for a in self.accounts if a.is_open and _key(a.name) == key), None)

    def account_named(self, name: str | None) -> Account | None:
        """The account with this name, closed ones included, so a late refund on a cancelled card
        can still be logged. An open account wins over a closed one with the same name (a
        replacement card); among closed ones, the most recently closed."""
        if not name:
            return None
        open_match = self.open_account_named(name)
        if open_match:
            return open_match
        closed = [a for a in self.accounts if _key(a.name) == _key(name)]
        return max(closed, key=lambda a: a.closed_on, default=None)

    def account_names(self) -> list[str]:
        """Every account name, for the extraction prompt's list of valid payment methods. Open
        accounts first (id order), then closed ones; a name already listed isn't repeated, so a
        replacement card and its predecessor appear once."""
        names, seen = [], set()
        for a in sorted(self.accounts, key=lambda a: (not a.is_open, a.id)):
            if _key(a.name) not in seen:
                seen.add(_key(a.name))
                names.append(a.name)
        return names


def validate_directory(directory: Directory) -> list[str]:
    """Every rule the bot relies on that the database itself can't enforce, as one sentence per
    broken row. An empty list means the directory is usable.

    All problems are returned, not just the first: the bot refuses everyone while any exist (see
    load_directory), so fixing them in one visit to the SQL editor matters.
    """
    problems = []
    for p in directory.participants:
        if p.telegram_user_id is None:
            continue  # not a bot user, so the bot never needs their default
        default = directory.account(p.default_account_id)
        if p.default_account_id is None:
            problems.append(f"Participant {p.id} ({p.display_name}) uses the bot but has no default account")
        elif default is None or not default.is_open:
            problems.append(
                f"Participant {p.id} ({p.display_name}) has default account {p.default_account_id}, which is closed or missing"
            )
    for a in directory.accounts:
        if a.funded_by_account_id is None:
            continue
        funder = directory.account(a.funded_by_account_id)
        if funder is None or not funder.is_open:
            problems.append(
                f"Account {a.id} ({a.name}) is funded by account {a.funded_by_account_id}, which is closed or missing"
            )
    return problems


def load_directory(conn) -> Directory:
    """Reads both tables and checks them. The only function in this module that touches Postgres.

    Raises DirectoryError naming every broken row. The bot treats that exactly like the database
    being down - it refuses the message - because bad data here would otherwise save an entry
    against the wrong person or card without anyone noticing.
    """
    participants = conn.execute(text(
        "SELECT id, display_name, telegram_user_id, default_account_id FROM participants ORDER BY id"
    )).all()
    accounts = conn.execute(text(
        "SELECT id, name, owner_participant_id, funded_by_account_id, closed_on FROM accounts ORDER BY id"
    )).all()

    directory = Directory(
        participants=tuple(Participant(*row) for row in participants),
        accounts=tuple(Account(*row) for row in accounts),
    )
    problems = validate_directory(directory)
    if problems:
        raise DirectoryError("; ".join(problems))
    return directory


def whose_card_paid(account: Account, sender: Participant | None, directory: Directory) -> Participant | None:
    """The person whose money this was: the account's owner, or - for a shared account such as
    Cash - whoever sent the message. None when a shared account has no recorded sender."""
    if account.owner_participant_id is not None:
        return directory.participant(account.owner_participant_id)
    return sender


def resolve_payment(transaction: dict, directory: Directory, sender: Participant) -> dict:
    """Decides which account paid for an extracted transaction, and fills in what follows from it.
    Mutates and returns the dict, like the apply_payment_defaults() it replaces.

    Sets account_id, payment_method (the account's real name), account_owner (a display name, for
    the card) and closed_account. In order:
      1. A YouTrip top-up is paid from the wallet's funding card, whoever sent it, and is a
         Transfer. No open wallet or no funding card -> WalletNotSetUp: never guess.
      2. A named card is matched, closed cards included (closed_account is then True and the card
         asks the user to confirm).
      3. No card, or a name that matches nothing (the model invented one): a foreign-currency
         payment goes on the wallet, anything else on the sender's default card. The card shows
         which account was chosen, so the user sees it before confirming.
    """
    if transaction.get("category") == "YouTrip top-up":
        wallet = directory.open_account_named(WALLET_NAME)
        funder = directory.account(wallet.funded_by_account_id) if wallet else None
        if funder is None or not funder.is_open:
            raise WalletNotSetUp()
        account = funder
        transaction["transaction_type"] = "Transfer"
    else:
        account = directory.account_named(transaction.get("payment_method"))
        if account is None and transaction.get("currency", "SGD") != "SGD":
            account = directory.open_account_named(WALLET_NAME)
        if account is None:
            account = directory.account(sender.default_account_id)

    owner = whose_card_paid(account, sender, directory)
    transaction["account_id"] = account.id
    transaction["payment_method"] = account.name
    transaction["account_owner"] = owner.display_name if owner else "Not recorded"
    transaction["closed_account"] = not account.is_open
    return transaction
