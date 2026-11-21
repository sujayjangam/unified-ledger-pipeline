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

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text

# The multi-currency wallet. A top-up moves money into it, and a foreign-currency payment that
# names no card is assumed to be on it. Matched by name, like any other account.
WALLET_NAME = "YouTrip"
# Only the outside-payer rule may choose this shared account, never the model.
OUTSIDE_PAYER_ACCOUNT_NAME = "Paid by someone else"
# Setup failures replace the card; payer warnings are display-only lines on it.
NO_OUTSIDE_ACCOUNT_MESSAGE = "⚠️ No outside-payer account set up — add Paid by someone else in the accounts table. Nothing was saved."
PAYER_UNCLEAR_WARNING = "⚠️ Couldn't tell who paid"
PAYER_READ_AS_WARNING = "⚠️ Read as: {payer} paid. Check."
# Toasts for a "Paid by" tap that can't be applied. The card and the entry stay as they were.
NO_PAYER_ACCOUNT_TOAST = "⚠️ {payer} has no default account to put this on. Nothing changed."
NO_OUTSIDE_ACCOUNT_TOAST = "⚠️ No 'Paid by someone else' account is set up. Nothing changed."
OWNER_WINS_WARNING = "⚠️ The message says {payer} paid, but {account} is {owner}'s card, so {owner} is recorded as paying."
OUTSIDE_ON_OWNED_WARNING = "⚠️ {payer} paid, so this is on {outside_account}, not {account}."

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


class OutsideAccountNotSetUp(Exception):
    """An outside payer was named but their shared account is missing or closed."""


class PayerHasNoAccount(Exception):
    """A household payer was tapped, but no account of theirs can take the entry."""


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
    is_household: bool = False


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

    def household(self) -> tuple[Participant, ...]:
        """Payer candidates when no trip supplies a wider list."""
        return tuple(p for p in self.participants if p.is_household)

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
        """Every ordinary account name, for the extraction prompt's valid payment methods. Open
        accounts first (id order), then closed ones; a name already listed isn't repeated, so a
        replacement card and its predecessor appear once."""
        names, seen = [], set()
        for a in sorted(self.accounts, key=lambda a: (not a.is_open, a.id)):
            if _key(a.name) == _key(OUTSIDE_PAYER_ACCOUNT_NAME):
                continue  # Python alone chooses this account after reading an outside payer.
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

    # Two open accounts with the same name, spaces and case ignored. The database's unique index
    # compares lower(name) without trimming, so it lets " Card A" sit beside "Card A" - but the bot
    # treats those as one name, and would silently pick whichever it found first.
    open_by_name = {}
    for a in directory.accounts:
        if a.is_open:
            open_by_name.setdefault(_key(a.name), []).append(a)
    for same in open_by_name.values():
        if len(same) > 1:
            ids = ", ".join(str(a.id) for a in same)
            problems.append(f"Accounts {ids} are all open and named {same[0].name.strip()!r} - close or rename all but one")
    return problems


def load_directory(conn) -> Directory:
    """Reads both tables and checks them. The only function in this module that touches Postgres.

    Raises DirectoryError naming every broken row. The bot treats that exactly like the database
    being down - it refuses the message - because bad data here would otherwise save an entry
    against the wrong person or card without anyone noticing.
    """
    participants = conn.execute(text(
        "SELECT id, display_name, telegram_user_id, default_account_id, is_household FROM participants ORDER BY id"
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


def _names_account(message: str, name: str) -> bool:
    """True when the message contains the account's whole name as words, case ignored. Whole
    words, so "cash" is not found inside "DBS Cashback"."""
    pattern = r"(?<!\w)" + r"\s+".join(re.escape(word) for word in name.split()) + r"(?!\w)"
    return re.search(pattern, message, re.IGNORECASE) is not None


def _open_account_in(message: str, directory: Directory) -> Account | None:
    """The open account the message names, or None. If several match, the longest name wins: it
    is the more specific one (a message naming "Card A Plus" also contains "Card A")."""
    named = [a for a in directory.accounts if a.is_open
             and _key(a.name) != _key(OUTSIDE_PAYER_ACCOUNT_NAME) and _names_account(message, a.name)]
    return max(named, key=lambda a: len(a.name), default=None)


@dataclass(frozen=True)
class PayerReading:
    """A unique text payer, or a signal that the card should ask the user to check."""

    payer: Participant | None
    unclear: bool


# These words describe payment or grammar, rather than an unknown person's name.
_PAYER_EXCLUSIONS = frozenset("""
    i me we us you he she they it who that which just already also all fully partly
    was were been be is are has have had not never being and then bill prepaid
    card cash credit debit paynow paylah nets grab visa mastercard amex bank transfer
    cheque check apple google someone somebody
""".split())


def match_person(name: str, candidates) -> Participant | None:
    """The one candidate a name as said stands for, or None. Used for split names and for the
    model's payer_name; never guesses.

    The same rule read_payer uses: a candidate's whole display name, or one word of a
    multi-word display name that no other candidate shares ("Lee" for "Ann Lee" while she is
    the only Lee). Case and extra spaces are ignored, and family wording is stripped first, so
    "Carol's family" and "Carol's" both look up "carol". None when nobody matches, or when
    the name could be more than one person.
    """
    name = " ".join(name.casefold().split())
    name = re.sub(r"\s+famil(?:y|ies)$", "", name)
    name = re.sub(r"['’]s$", "", name).strip()
    candidates = tuple(candidates)
    word_counts = {}
    for person in candidates:
        for word in set(person.display_name.casefold().split()):
            word_counts[word] = word_counts.get(word, 0) + 1
    matches = []
    for person in candidates:
        words = person.display_name.casefold().split()
        if name == " ".join(words) or (len(words) > 1 and name in words and word_counts[name] == 1):
            matches.append(person)
    return matches[0] if len(matches) == 1 else None


# The model's payer_name is believed only when the message has one of these words. Measured
# 2026-11-16, 20 runs each: with a name in the message and no such word, the model still named a
# payer - "Carol's birthday cake 55" in 3 runs, "dinner with Carol 70" in 1 - and a wording fix
# didn't stop it. Whether a word is there is something Python answers exactly (as in ADR-0031).
_PAYING_WORD = re.compile(
    r"(?<!\w)(?:paid|pay|pays|paying|cover|covers|covered|treat|treats|treated|shout|shouts|shouted|"
    r"footed|settled|sponsored|belanja)(?!\w)", re.IGNORECASE)


def mentions_paying(message: str) -> bool:
    """True when the message has a word about someone paying ("paid", "covered", "treat")."""
    return _PAYING_WORD.search(message) is not None


def second_reader_payer(payer_name: str | None, candidates, sender, message: str) -> Participant | None:
    """The person the model says paid, if the message has a paying word and the name is one
    known candidate other than the sender. None otherwise. The caller uses it only when
    read_payer found nobody: Python's own reading always wins (ADR-0040)."""
    if not payer_name or not mentions_paying(message):
        return None
    person = match_person(payer_name, candidates)
    return person if person and person.id != sender.id else None


def read_payer(message: str, candidates, sender: Participant, account_names=()) -> PayerReading:
    """Read 'paid by <name>', 'paid for by <name>', '<name> paid' and '<name> pays'.

    <name> is a candidate's whole display name, or one word of it that no other candidate's
    name shares. 'I paid', 'I pay' and 'paid by me' always mean the sender.

    Python reads the raw text, never the model: 'split with Carol' must never become
    'Carol paid'. The caller supplies trip members or household people and directory
    account names, whose words suppress warnings for payment methods such as Card A.
    """
    # What counts as saying a person: their whole display name, or any one word of it that no
    # other candidate's name contains. Nobody says "Ann Lee paid" every time; "Lee paid" is
    # enough while she is the only Lee. A word two candidates share ("Ann" in "Ann Lee" and "Ann
    # Kim") is left out, so it can't pick either of them - it falls through to the unclear warning.
    word_counts = {}
    for p in candidates:
        for w in {w.casefold() for w in p.display_name.split()}:
            word_counts[w] = word_counts.get(w, 0) + 1
    spoken = [(p.display_name.split(), p) for p in candidates]
    spoken += [([w], p) for p in candidates if len(p.display_name.split()) > 1
               for w in p.display_name.split() if word_counts[w.casefold()] == 1]
    # One alternation consumes the longest form first, so a shorter one cannot also match the
    # same payer mention. Separate mentions still count as separate people.
    spoken.sort(key=lambda entry: len(" ".join(entry[0])), reverse=True)
    names = [r"\s+".join(re.escape(w) for w in words) for words, _ in spoken]
    found = {}
    if names:
        choices = "|".join(f"(?P<p{i}>{name})" for i, name in enumerate(names))
        for pattern in (rf"(?<!\w)paid\s+(?:for\s+)?by\s+(?:{choices})(?!\w)",
                        rf"(?<!\w)(?:{choices})\s+(?:paid|pays)(?!\w)"):
            for match in re.finditer(pattern, message, re.IGNORECASE):
                person = spoken[int(match.lastgroup[1:])][1]
                found[person.id] = person
    if re.search(r"(?<!\w)(?:I\s+(?:paid|pay)|paid\s+by\s+me)(?!\w)", message, re.IGNORECASE):
        found[sender.id] = sender
    if found:
        return PayerReading(next(iter(found.values())) if len(found) == 1 else None, len(found) > 1)

    excluded = _PAYER_EXCLUSIONS | {w.casefold() for name in account_names for w in re.findall(r"\w+", name)}
    # Check both directions separately so an incidental 'Lunch paid' cannot consume
    # 'paid by Carroll' and hide the unknown name after it.
    # Amounts are not names: the '40 paid' in 'Lunch 40 paid by cash' must not warn.
    word = r"[^\W\d_]+"
    words = re.findall(rf"(?<!\w)paid\s+(?:for\s+)?by\s+({word})(?!\w)", message, re.IGNORECASE)
    # In "Dinner paid by cash" the word before "paid" is the thing bought, not a person: when
    # "paid by" follows, the payer (if any) is the word after it, checked just above.
    words += re.findall(rf"(?<!\w)({word})\s+(?:paid|pays)(?!\w)(?!\s+(?:for\s+)?by(?!\w))",
                        message, re.IGNORECASE)
    return PayerReading(None, any(word.casefold() not in excluded for word in words))


def resolve_payment(transaction: dict, directory: Directory, sender: Participant,
                    text_payer: Participant | None = None) -> dict:
    """Decides which account paid for an extracted transaction, and fills in what follows from it.
    Mutates and returns the dict, like the apply_payment_defaults() it replaces.

    Sets account_id, paid_by_participant_id, payment_method, paid_by, account_owner,
    closed_account, payer_warning and base_account_id. Names and warnings are display only;
    base_account_id is only for a later "Paid by" tap (change_payer). In order:
      1. A YouTrip top-up is paid from the wallet's funding card, whoever sent it, and is a
         Transfer. No open wallet or no funding card -> WalletNotSetUp: never guess.
      2. An outside text payer uses the open outside-payer account, whatever the model picked.
         Missing setup raises OutsideAccountNotSetUp. An owned model pick produces a warning.
      3. Otherwise keep today's named-card and closed-card correction rules. With no match,
         foreign currency uses the wallet first, then the household payer's default or the
         sender's default. An owned account decides the payer (warning on disagreement);
         an unowned account takes the text payer or sender. Warnings never block saving.
    """
    warning = None
    # The account the message itself named (after the closed-card correction), before any payer
    # rule moved it. Kept on the entry for change_payer. None when no card was named.
    base = None
    if transaction.get("category") == "YouTrip top-up":
        wallet = directory.open_account_named(WALLET_NAME)
        funder = directory.account(wallet.funded_by_account_id) if wallet else None
        if funder is None or not funder.is_open:
            raise WalletNotSetUp()
        account = funder
        payer = whose_card_paid(account, sender, directory)
        transaction["transaction_type"] = "Transfer"
    else:
        account = directory.account_named(transaction.get("payment_method"))
        if account and _key(account.name) == _key(OUTSIDE_PAYER_ACCOUNT_NAME):
            account = None
        message = transaction.get("description") or ""
        if account is not None and not account.is_open and not _names_account(message, account.name):
            # The model picked a closed card the message never names in full, while the message
            # does name an open one: "Zebra test exp $25 cash" once went on a closed "Zebra Test
            # Card" instead of Cash. The open card wins. With no open card named, the closed card
            # stays, with its warning and "Save anyway": a late refund said loosely ("my old
            # visa", often from a voice transcript) must not slip onto the default card unseen.
            account = _open_account_in(message, directory) or account
        base = account
        if text_payer is not None and not text_payer.is_household:
            outside = directory.open_account_named(OUTSIDE_PAYER_ACCOUNT_NAME)
            if outside is None:
                raise OutsideAccountNotSetUp()
            if account and account.owner_participant_id is not None:
                warning = OUTSIDE_ON_OWNED_WARNING.format(
                    payer=text_payer.display_name, outside_account=outside.name, account=account.name)
            account, payer = outside, text_payer
        else:
            if account is None and transaction.get("currency", "SGD") != "SGD":
                account = directory.open_account_named(WALLET_NAME)
            if account is None:
                account = directory.account(text_payer.default_account_id) if text_payer else None
                account = account or directory.account(sender.default_account_id)
            payer = whose_card_paid(account, text_payer or sender, directory)
            if text_payer and payer.id != text_payer.id:
                warning = OWNER_WINS_WARNING.format(
                    payer=text_payer.display_name, account=account.name, owner=payer.display_name)

    transaction["base_account_id"] = base.id if base else None
    return _set_payment(transaction, account, payer, warning)


def _set_payment(transaction: dict, account: Account, payer: Participant, warning: str | None) -> dict:
    """Writes the chosen account and payer onto the entry: the two ids that get saved, and the
    names and flags the card shows. One place, so resolve_payment (the message) and change_payer
    (a "Paid by" tap) can never fill these fields differently."""
    transaction["account_id"] = account.id
    transaction["payment_method"] = account.name
    transaction["account_owner"] = payer.display_name
    transaction["paid_by_participant_id"] = payer.id
    transaction["paid_by"] = payer.display_name
    transaction["payer_warning"] = warning
    transaction["closed_account"] = not account.is_open
    return transaction


def change_account(transaction: dict, directory: Directory, account: Account) -> dict:
    """Return a copy with an offered account deliberately chosen on the card.

    Owned cards decide the payer. Shared accounts keep a household payer, otherwise
    fall back to the sender. The handler validates the account against its menu first.
    """
    payer = directory.participant(transaction.get("paid_by_participant_id"))
    if account.owner_participant_id is not None:
        payer = directory.participant(account.owner_participant_id)
    elif payer is None or not payer.is_household:
        payer = directory.participant(transaction["entered_by_participant_id"])
    # Future payer taps must start from this deliberate choice, not undo it by using
    # the account originally read from the message. Split ticks remain untouched.
    changed = {**transaction, "base_account_id": account.id}
    return _set_payment(changed, account, payer, None)


def change_payer(transaction: dict, directory: Directory, payer: Participant) -> dict:
    """The entry as it would be with `payer` tapped on the card's "Paid by" menu. Returns a
    changed copy; the entry passed in is left alone, so the caller can redraw the card first and
    keep the old entry if that fails.

    The same rules as resolve_payment, with the tapped person in place of a name in the message:
      - An outside payer goes on the open "Paid by someone else" account
        (none -> OutsideAccountNotSetUp).
      - A household payer keeps the account the message named (base_account_id) when it is
        unowned (Cash, the wallet) or their own. Someone else's card can't stay: an owned account
        always decides the payer, and here the tap decides. Then, as if no card had been named:
        the wallet for a foreign currency, otherwise the payer's default account. A default that
        is missing, or is someone else's card -> PayerHasNoAccount: never guess.

    It starts from base_account_id, not the account the card shows now, so taps don't pile up:
    tapping Bob and then Alice again gives back exactly the card Alice started with.

    A tap is a deliberate choice, so the result carries no payer warning.
    """
    if not payer.is_household:
        account = directory.open_account_named(OUTSIDE_PAYER_ACCOUNT_NAME)
        if account is None:
            raise OutsideAccountNotSetUp()
    else:
        def usable(a: Account | None) -> Account | None:
            return a if a is not None and a.owner_participant_id in (None, payer.id) else None

        account = usable(directory.account(transaction.get("base_account_id")))
        if account is None and transaction.get("currency", "SGD") != "SGD":
            # usable() here too: a wallet that has an owner is that person's, like any card.
            account = usable(directory.open_account_named(WALLET_NAME))
        if account is None:
            account = usable(directory.account(payer.default_account_id))
        if account is None:
            raise PayerHasNoAccount()
    return _set_payment(dict(transaction), account, payer, None)
