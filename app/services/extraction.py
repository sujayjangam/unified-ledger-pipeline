import os
import json
from typing import Optional, List
from enum import Enum
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
from typing import Literal
from app.services.utils import get_sgt_now

# Initialize the OpenAI client
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def _load_account_owners() -> dict:
    """Reads and parses the ACCOUNT_OWNERS env var, degrading to {} rather than raising.

    This module deliberately does NOT call load_dotenv() - it relies on bot_core.py having
    called it before importing this file (see the `# noqa: E402` comments there). But two
    paths import this module with no .env loaded at all: the CI import smoke check
    (.github/workflows/ci.yml runs `python -c "import <module>"` per file) and
    tests/test_extraction.py, which never imports bot_core. Both must keep working, so a
    missing or malformed value returns {} instead of blowing up the import.

    Mirrors the same guard bot_core.py:20-25 already applies to this variable.
    """
    try:
        return json.loads(os.getenv("ACCOUNT_OWNERS", "{}"))
    except json.JSONDecodeError:
        return {}


def build_allowed_accounts(account_owners: dict) -> List[str]:
    """Flattens ACCOUNT_OWNERS into the list of payment methods the extractor may choose from.

    ACCOUNT_OWNERS is the single source of truth for which cards/wallets exist in this
    household. It replaced a separate ALLOWED_ACCOUNTS env var that fed this prompt: that
    var was never actually set, so the prompt silently ran on a hardcoded fallback list
    which omitted one household member's card entirely and named exactly one bank account.
    Any transfer-shaped message therefore had only one plausible card to pick, and it was
    always the same person's - which is how transactions ended up attributed to the wrong
    owner. Deriving the list here means the two can no longer drift apart.

    Kept as a pure function (dict in, list out - no env, no network) so it can be unit
    tested directly, the same reason apply_payment_defaults() was pulled out of the bot's
    handler in bot_core.py.
    """
    accounts: List[str] = []
    # Track lowercased names separately: the reverse-lookup in bot_core.py matches payment
    # methods case-insensitively, so treating 'PayNow' and 'paynow' as one entry here keeps
    # the prompt consistent with how the extracted value is later resolved to an owner.
    seen = set()

    for owner_accounts in account_owners.values():
        for account in owner_accounts:
            if account.lower() not in seen:
                seen.add(account.lower())
                accounts.append(account)

    # Cash is a real payment method the bot handles (bot_core.py attributes it to whoever
    # sent the message) but it belongs to nobody, so it never appears in ACCOUNT_OWNERS.
    # Append it explicitly or the model loses the ability to extract "paid with cash".
    if "cash" not in seen:
        accounts.append("Cash")

    return accounts

# 1. Define Strict Categories using an Enum (Prevents typos like "Foods" or "Transit")
class ExpenseCategory(str, Enum):
    FOOD = "Food"
    TRANSPORT = "Transport"
    ACCOMMODATION = "Accommodation"
    ENTERTAINMENT = "Entertainment"
    UTILITIES = "Utilities"
    YOUTRIP_TOPUP = "YouTrip top-up"
    OTHER = "Other"

# 2. Define the Upgraded Data Blueprint
class TransactionSchema(BaseModel):
    # Optional[] means the LLM is allowed to return 'null' if the user forgot to state the price
    amount: Optional[float] = Field(default=None, description="""The exact numerical value. Extract ONLY the number. 
                                    Example: for '50 ringgit', extract 50.0. If not explicitly stated in the text, it must be NULL""")
    
    currency: str = Field(default="SGD", description="""3-letter ISO currency code. 
                          You MUST map spoken words to standard codes: 'dollars' -> SGD, 'ringgit' -> MYR, 'rupiah' -> IDR, 'baht' -> THB, 'usd' -> USD, 'rupees' -> INR, 'aud' -> AUD.
                          Default to SGD if not stated or if user mentions 'dollars' Only use USD and AUD at their explicit mentions.""")
    
    # no schema for description, because I want to store the raw transcript for better historical data rather than a summary

    transaction_type: Literal['Expense', 'Transfer'] = Field(description="""Classify as 'Transfer' if the user is topping up a wallet (e.g., YouTrip),
                                                              moving money between accounts, or paying a credit card bill. Otherwise, classify as 'Expense'.""")
    # Capture the payment method or account. The list of valid values is NOT baked in here:
    # a Field description is evaluated once, when this class is defined at import time, and
    # this module gets imported in paths where no .env has been loaded yet. The list is built
    # per call and injected via the system prompt in extract_transactions() instead.
    payment_method: Optional[str] = Field(default=None, description="""The card, account, or wallet used.
                                          Must be one of the payment methods listed in the system prompt.
                                          Null if not mentioned - never guess one. If the words 'pay' and 'now'
                                          are mentioned consecutively, then the payment method is most likely
                                          'PayNow'.""")
    
    category: ExpenseCategory = Field(description="Classify into one of the exact ExpenseCategory enums.")
    
    date: str = Field(description="YYYY-MM-DD format. Infer based on today's date.")
    
    # A flag for your future UI to know if it needs to ask the user for clarification
    needs_review: bool = Field(description="Set to true ONLY if amount is null or the transcript is highly confusing.")

# creating TransactionList to make life easier in future ticket to handle multiple transactions in 1 voice note
class TransactionList(BaseModel):
    transactions: List[TransactionSchema] = Field(description="A list of extracted expenses. If the user mentions multiple distinct expenses, create a separate object for each.")

# 3. The Extraction Function
async def extract_transactions(transcript_text: str) -> dict | None:
    """Takes raw text and safely extracts a structured JSON list of transactions."""
    today_str = get_sgt_now().strftime("%Y-%m-%d")

    # Built per call rather than at import time, so the list always reflects the ACCOUNT_OWNERS
    # currently in the environment and this module stays safe to import without a loaded .env.
    allowed_accounts = ", ".join(build_allowed_accounts(_load_account_owners()))

    try:
        response = await openai_client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
                # CHANGED: Added a strict 'No Translation' rule directly to the system prompt engine
                {"role": "system", "content": f"""You are a strict financial data extraction tool. Today is {today_str}.
                                                Extract notes in the EXACT original language. DO NOT translate into Malay,
                                                Indonesian, or any other language. If a user forgets to mention a price or
                                                details are vague, leave those fields null and set needs_review to true.
                                                Never guess an amount.
                                                The only valid payment methods are: {allowed_accounts}.
                                                Use a value from that list and nothing else. If the user did not say how
                                                they paid, leave payment_method null - do not guess, and do not pick the
                                                only card that happens to look plausible."""},
                {"role": "user", "content": transcript_text}
            ],
            response_format=TransactionList,
        )
        
        # without mode='json' enums like 'Transport' was being passed as 'ExpenseCategory.Transport' instead
        return response.choices[0].message.parsed.model_dump(mode='json')
        
    except Exception as e:
        print(f"❌ Extraction Error: {e}")
        return None