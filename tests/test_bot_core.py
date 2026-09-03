import app.bot_core as bot_core

# ACCOUNT_OWNERS/ALLOWED_IDS/PRIMARY_ACCOUNT_OWNER are all parsed from env vars once, at
# module-import time (see bot_core.py's top-level try/except block) - they're never re-read
# inside a function call. So to fake their contents for a test, we have to monkeypatch the
# module-level attribute directly; setting the underlying env var after import would have
# no effect.


# --- apply_payment_defaults ---

# Test fixtures below use fictional names/card labels ("Alice"/"Bob", "Card A"/"Card B")
# rather than this household's real ACCOUNT_OWNERS values - this is a public repo, and the
# matching logic itself doesn't care whose name or which card it is.

def test_youtrip_topup_forces_transfer_and_payment_method(monkeypatch):
    # The YouTrip top-up branch always funds from PRIMARY_ACCOUNT_OWNER's account,
    # regardless of who sent the message (spender_name below is deliberately someone else).
    monkeypatch.setattr(bot_core, "ACCOUNT_OWNERS", {"Alice": ["Card A", "Cash"], "Bob": ["Card B"]})
    monkeypatch.setattr(bot_core, "PRIMARY_ACCOUNT_OWNER", "Alice")
    txn = {"category": "YouTrip top-up", "currency": "SGD"}
    result = bot_core.apply_payment_defaults(txn, "Bob")
    assert result["payment_method"] == "Card A"
    assert result["transaction_type"] == "Transfer"


def test_missing_payment_method_defaults_to_youtrip_for_non_sgd(monkeypatch):
    monkeypatch.setattr(bot_core, "ACCOUNT_OWNERS", {"Alice": ["Card A"]})
    txn = {"category": "Food", "currency": "MYR"}
    result = bot_core.apply_payment_defaults(txn, "Alice")
    assert result["payment_method"] == "YouTrip"


def test_missing_payment_method_defaults_to_senders_default_account_for_sgd(monkeypatch):
    monkeypatch.setattr(bot_core, "ACCOUNT_OWNERS", {"Bob": ["Card B", "Cash"]})
    txn = {"category": "Food", "currency": "SGD"}
    result = bot_core.apply_payment_defaults(txn, "Bob")
    assert result["payment_method"] == "Card B"


def test_cash_is_always_attributed_to_the_sender(monkeypatch):
    monkeypatch.setattr(
        bot_core, "ACCOUNT_OWNERS", {"Alice": ["Card A"], "Bob": ["Card B"]}
    )
    txn = {"category": "Food", "currency": "SGD", "payment_method": "Cash"}
    result = bot_core.apply_payment_defaults(txn, "Bob")
    assert result["account_owner"] == "Bob"


def test_reverse_match_of_account_owner_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(
        bot_core, "ACCOUNT_OWNERS", {"Alice": ["Card A"], "Bob": ["Card B"]}
    )
    txn = {"category": "Food", "currency": "SGD", "payment_method": "card b"}
    result = bot_core.apply_payment_defaults(txn, "Bob")
    assert result["account_owner"] == "Bob"


def test_unmatched_payment_method_falls_back_to_unknown_owner(monkeypatch):
    monkeypatch.setattr(bot_core, "ACCOUNT_OWNERS", {"Alice": ["Card A"]})
    txn = {"category": "Food", "currency": "SGD", "payment_method": "Some Random Card"}
    result = bot_core.apply_payment_defaults(txn, "Alice")
    assert result["account_owner"] == "Unknown"
