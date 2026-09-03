import app.bot_core as bot_core

# ACCOUNT_OWNERS/ALLOWED_IDS are parsed from env vars once, at module-import time (see
# bot_core.py's top-level try/except block) - they're never re-read inside a function call.
# So to fake their contents for a test, we have to monkeypatch the module-level attribute
# directly; setting the underlying env var after import would have no effect.


# --- apply_payment_defaults ---

def test_youtrip_topup_forces_transfer_and_payment_method(monkeypatch):
    monkeypatch.setattr(bot_core, "ACCOUNT_OWNERS", {"Sujay": ["OCBC Infinity", "Cash"]})
    txn = {"category": "YouTrip top-up", "currency": "SGD"}
    result = bot_core.apply_payment_defaults(txn, "Sujay")
    assert result["payment_method"] == "OCBC Infinity"
    assert result["transaction_type"] == "Transfer"


def test_missing_payment_method_defaults_to_youtrip_for_non_sgd(monkeypatch):
    monkeypatch.setattr(bot_core, "ACCOUNT_OWNERS", {"Sujay": ["OCBC Infinity"]})
    txn = {"category": "Food", "currency": "MYR"}
    result = bot_core.apply_payment_defaults(txn, "Sujay")
    assert result["payment_method"] == "YouTrip"


def test_missing_payment_method_defaults_to_senders_default_account_for_sgd(monkeypatch):
    monkeypatch.setattr(bot_core, "ACCOUNT_OWNERS", {"Priya": ["DBS Card", "Cash"]})
    txn = {"category": "Food", "currency": "SGD"}
    result = bot_core.apply_payment_defaults(txn, "Priya")
    assert result["payment_method"] == "DBS Card"


def test_cash_is_always_attributed_to_the_sender(monkeypatch):
    monkeypatch.setattr(
        bot_core, "ACCOUNT_OWNERS", {"Sujay": ["OCBC Infinity"], "Priya": ["DBS Card"]}
    )
    txn = {"category": "Food", "currency": "SGD", "payment_method": "Cash"}
    result = bot_core.apply_payment_defaults(txn, "Priya")
    assert result["account_owner"] == "Priya"


def test_reverse_match_of_account_owner_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(
        bot_core, "ACCOUNT_OWNERS", {"Sujay": ["OCBC Infinity"], "Priya": ["DBS Card"]}
    )
    txn = {"category": "Food", "currency": "SGD", "payment_method": "dbs card"}
    result = bot_core.apply_payment_defaults(txn, "Priya")
    assert result["account_owner"] == "Priya"


def test_unmatched_payment_method_falls_back_to_unknown_owner(monkeypatch):
    monkeypatch.setattr(bot_core, "ACCOUNT_OWNERS", {"Sujay": ["OCBC Infinity"]})
    txn = {"category": "Food", "currency": "SGD", "payment_method": "Some Random Card"}
    result = bot_core.apply_payment_defaults(txn, "Sujay")
    assert result["account_owner"] == "Unknown"
