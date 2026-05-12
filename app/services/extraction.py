import os
from datetime import datetime
from typing import Optional, List
from enum import Enum
from pydantic import BaseModel, Field
from openai import AsyncOpenAI

# Initialize the OpenAI client
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# 1. Define Strict Categories using an Enum (Prevents typos like "Foods" or "Transit")
class ExpenseCategory(str, Enum):
    FOOD = "Food"
    TRANSPORT = "Transport"
    ACCOMMODATION = "Accommodation"
    ENTERTAINMENT = "Entertainment"
    UTILITIES = "Utilities"
    OTHER = "Other"

# 2. Define the Upgraded Data Blueprint
class TransactionSchema(BaseModel):
    # Optional[] means the LLM is allowed to return 'null' if the user forgot to state the price
    amount: Optional[float] = Field(default=None, description="The numerical amount. If not explicitly stated in the text, this MUST be null.")
    currency: str = Field(default="SGD", description="3-letter currency code. Default to SGD if not stated.")
    # remove this completely because AI trying to summarize our transcript is creating weird errors
    # description: Optional[str] = Field(default=None, description="""A detailed 1-line description preserving the original context.
    #                                                                 If the voice note is too short, then just extract what is available. 
    #                                                                 Do not add your own context. (e.g., 'Cab to the airport', 'Ate at lazy mondays burgers'). 
    #                                                                 Do not over-summarize. Null if unclear.)""")
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
    today_str = datetime.now().strftime("%Y-%m-%d")
    
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