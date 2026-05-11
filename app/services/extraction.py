import os
from datetime import datetime
from typing import Optional
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
    description: Optional[str] = Field(default=None, description="A clean description (e.g., 'Taxi', 'Lunch'). Null if unclear.")
    category: ExpenseCategory = Field(description="Classify into one of the exact ExpenseCategory enums.")
    date: str = Field(description="YYYY-MM-DD format. Infer based on today's date.")
    
    # NEW: A flag for your future UI to know if it needs to ask the user for clarification
    needs_review: bool = Field(description="Set to true ONLY if amount is null or the transcript is highly confusing.")

# 3. The Extraction Function
async def extract_transaction(transcript_text: str) -> dict | None:
    """Takes raw text and safely extracts a structured JSON object, handling missing data."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    try:
        response = await openai_client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": f"""You are a strict financial data extraction tool. Today is {today_str}. 
                 If a user forgets to mention a price or details are vague, leave those fields null and set needs_review to true. 
                 Never guess an amount. 
                 If the expense does not clearly fit into one of the provided categories, select 'Other' and set needs_review to true."""
                },
                {"role": "user", "content": transcript_text}
            ],
            response_format=TransactionSchema,
        )
        
        return response.choices[0].message.parsed.model_dump()
        
    except Exception as e:
        print(f"❌ Extraction Error: {e}")
        return None