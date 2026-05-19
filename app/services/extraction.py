import os
from datetime import datetime
from typing import Optional, List
from enum import Enum
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
from typing import Optional, Literal
from app.services.utils import get_sgt_now

# Initialize the OpenAI client
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ALLOWED_ACCOUNTS = os.getenv("ALLOWED_ACCOUNTS", "YouTrip, OCBC Infinity, Cash")

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
    # NEW: Capture the payment method or account
    payment_method: Optional[str] = Field(default=None, description=f"""The card, account, or wallet used (Must be one of: 
                                          {ALLOWED_ACCOUNTS}). Null if not mentioned. If topping up YouTrip, the payment method 
                                          is ALWAYS 'OCBC Infinity'. If the words 'pay' and 'now' are mentioned consecutively, then 
                                          the payment method is most likely 'PayNow'.""")
    
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
    
    try:
        response = await openai_client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
                # CHANGED: Added a strict 'No Translation' rule directly to the system prompt engine
                {"role": "system", "content": f"""You are a strict financial data extraction tool. Today is {today_str}. 
                                                Extract notes in the EXACT original language. DO NOT translate into Malay, 
                                                Indonesian, or any other language. If a user forgets to mention a price or 
                                                details are vague, leave those fields null and set needs_review to true. 
                                                Never guess an amount."""},
                {"role": "user", "content": transcript_text}
            ],
            response_format=TransactionList,
        )
        
        # without mode='json' enums like 'Transport' was being passed as 'ExpenseCategory.Transport' instead
        return response.choices[0].message.parsed.model_dump(mode='json')
        
    except Exception as e:
        print(f"❌ Extraction Error: {e}")
        return None