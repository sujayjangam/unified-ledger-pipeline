import os
from typing import Optional, List
from enum import Enum
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
from typing import Literal
from datetime import date

# Initialize the OpenAI client
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# 1. Define Strict Categories using an Enum (Prevents typos like "Foods" or "Transit")
class ExpenseCategory(str, Enum):
    FOOD = "Food"
    TRANSPORT = "Transport"
    ACCOMMODATION = "Accommodation"
    ENTERTAINMENT = "Entertainment"
    UTILITIES = "Utilities"
    YOUTRIP_TOPUP = "YouTrip top-up"
    OTHER = "Other"

Weekday = Literal['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

class DateReference(BaseModel):
    """What the user *said* about the date - not the date itself (#15, ADR-0030).

    The model used to return a finished YYYY-MM-DD, which meant it did the calendar maths
    ("last Tuesday" -> which day is today -> count back). That maths can't be unit tested
    without calling OpenAI, and a wrong date is permanent while saved rows can't be edited.
    So the model now only reads the words, and app/services/dates.py turns this reference
    into a date with plain Python that the test suite covers.

    Exactly one shape is filled in, chosen by `kind`; the other fields stay null.
    """
    kind: Literal['unstated', 'today', 'days_ago', 'weekday', 'calendar'] = Field(description="""
        'unstated' if the message says nothing about when. 'today' if it says today/just now.
        'days_ago' for yesterday ('yday', 'ytd'), day before yesterday, 3 days ago.
        'weekday' for a named day ('Tuesday', 'last tues', 'Tuesday last week').
        'calendar' for a day and month ('3rd of July', '12 Sep', '3/7').""")
    # 'ytd' is spelled out here and again in the system prompt. With neither, the model read
    # "Grab 12.80 ytd" as 2 days ago in 2 of 5 runs; with only this one, 3 of 10; with both,
    # 0 of 10.
    days_ago: Optional[int] = Field(default=None, description="""Only for kind 'days_ago': 1 for yesterday
        ('yesterday', 'yday', 'ytd' are all 1), 2 for the day before yesterday, and so on.""")
    weekday: Optional[Weekday] = Field(default=None, description="Only for kind 'weekday': the day named.")
    # There is deliberately no field here for "Tuesday last week" versus "last Tuesday". The
    # model could not tell them apart: with a last_week field it marked "last tues" as last
    # week in 5 of 5 runs, however the description was worded, and asked to copy the week
    # words instead it invented "last week" for messages that didn't contain it. The
    # difference is one literal phrase, so app/services/dates.py checks the message text for
    # it instead (mentions_last_week) - deterministic, and covered by tests.
    day: Optional[int] = Field(default=None, description="Only for kind 'calendar': day of the month, 1-31.")
    month: Optional[int] = Field(default=None, description="""Only for kind 'calendar': month number, 1-12.
        Numeric dates are DAY FIRST: '3/7' is day 3, month 7.""")
    year: Optional[int] = Field(default=None, description="""Only for kind 'calendar': the year, ONLY if the
        user said one. Never fill it in yourself.""")

class SplitPerson(BaseModel):
    """One person or family named as sharing, before Python matches the directory."""

    name: str = Field(description="The name as said, without possessive or family wording: Carol's family -> Carol.")
    is_family: bool = Field(description="True for '<name>'s family', 'the <name>s', or '<name> family'; false for one person.")
    count: Optional[int] = Field(default=None, description="""Count said for this person or each
        family member: 'Carol and Dave 4' means 4 each. Null if not said. Never money or a percentage.""")


class SplitReference(BaseModel):
    """The model reads who shares; Python matches people and works out cents (ADR-0039).

    Like DateReference, this records words rather than a calculated answer. A share must
    never come from the model: Python makes the total exact and the result testable.
    """

    kind: Literal['none', 'everyone', 'named'] = Field(description="""
        'none' unless the message itself asks to split or share the cost: split, shared, share,
        between, divide, go halves/dutch. Buying something for someone, eating with someone,
        or saying who paid is NOT a split. 'everyone' for 'split between all', 'everyone',
        'all of us', 'the whole group'. 'named' when sharing people or families are named.""")
    include_sender: bool = Field(description="""True when the sender shares: 'split with Carol',
        'me and Carol', 'between me, Carol and Dave', 'shared with...'. False for 'split between
        Carol and Dave', where the sender is not named. True for everyone; false for none.""")
    people: List[SplitPerson] = Field(description="""Each person or family named as sharing, name
        copied as said without possessive/family wording. The sender is include_sender, not an
        entry, except when a count is said for them: 'me 3 and Carol 2' -> a 'me' entry with
        count 3. Empty for none and everyone.
        Each entry copies a count if said for that person/family, else null.""")
    # The same shape as people, so "everyone except Carol's family" can leave out a whole family.
    except_people: List[SplitPerson] = Field(description="""People or families named after 'except',
        'but not' or 'excluding': 'everyone except Dave' -> Dave. Usually with everyone. Empty otherwise.
        Each entry copies a count if said for that person/family, else null: 'everyone except
        Carol's family who only stayed 4' -> Carol, family, count 4.""")
    default_count: Optional[int] = Field(default=None, description="""Count said for every sharer
        without their own: '5 nights, everyone except Carol family who stayed 4' -> 5.
        Null if not said. Never calculate a count or share.""")
    count_unit: Optional[str] = Field(default=None, description="The unit as said, lower case and plural, e.g. nights; otherwise null.")
    uneven: bool = Field(description="""True only for shares that are NOT equal: by nights, by a
        count, a percentage, or a fixed amount for someone ('Carol's share is 50'). False otherwise.""")


# 2. Define the Upgraded Data Blueprint
class TransactionSchema(BaseModel):
    # Optional[] means the LLM is allowed to return 'null' if the user forgot to state the price
    # The examples lead with a bare number on purpose (#77). This description used to say only
    # when to return null, and the model read terse typed messages like "Lunch 15 yday" as
    # "price not stated" - measured null in 13 of 13 runs. Re-check any wording change here
    # with scripts/check_extraction.py; the test suite can't call the model.
    amount: Optional[float] = Field(default=None, description="""The price paid, as a number only.
                                    In a short typed message the price is usually a bare number with no
                                    currency sign and no decimals - that number IS the amount:
                                    'Lunch 15 yday' -> 15, 'coffee 5' -> 5, 'grab 12.80' -> 12.8,
                                    '50 ringgit' -> 50. A number that is part of the date ('3/7',
                                    '12 Sep', '3 days ago') is not the amount. Null ONLY when the
                                    message contains no price at all ('hello', 'lunch yesterday').""")
    
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
    
    # Not a date: a description of what was said, resolved into a date by
    # app/services/dates.py (see DateReference above).
    date_reference: DateReference = Field(description="What the user said about when this happened.")
    
    # These two sit after the older fields on purpose: the model fills fields in this order,
    # and the amount and date readings were tuned with nothing in front of them.
    split_reference: SplitReference = Field(description="Who the message asks to share the cost; never calculate shares.")
    # A second reading only: Python's payer patterns still win (ADR-0040).
    payer_name: Optional[str] = Field(default=None, description="""The person the message says paid, copied as said:
        'Carol covered dinner' -> Carol, 'Dave's treat' -> Dave, 'paid by Erin' -> Erin.
        Null if no payer is stated or the sender paid ('I paid'). Someone the cost is split or
        shared with is NOT the payer: 'dinner 80 split with Carol' -> null.
        Someone a thing was bought FOR is NOT the payer either: 'bought Carol a coffee 6' -> null,
        'gift for Dave 40' -> null. A name alone is never enough: there must be words saying that
        person paid. A card or payment method is not a person.""")
    # "bought Carol a coffee 6" came back with Carol as the payer in 2 of 10 runs before the
    # "bought FOR" sentence was added (2026-11-16). Re-check with scripts/check_extraction.py.

    # A flag for your future UI to know if it needs to ask the user for clarification
    needs_review: bool = Field(description="Set to true ONLY if amount is null or the transcript is highly confusing.")

# creating TransactionList to make life easier in future ticket to handle multiple transactions in 1 voice note
class TransactionList(BaseModel):
    # The share sentence was added after "Alice paid 135 split with Bob. Bob's split is 90" came
    # back as two expenses on the test bot (2026-11-18), so the one-expense guard refused it.
    # It is worded for the day several expenses per message are allowed: a share still belongs
    # to the expense it is part of, never becomes its own entry.
    transactions: List[TransactionSchema] = Field(description="""A list of extracted expenses.
        If the user mentions multiple distinct expenses, create a separate object for each.
        A sentence that gives someone's share of a cost already mentioned ('Bob's split is 90',
        'Carol's share is 50', 'Dave pays 30%') is part of that expense, never a new one.""")

# 3. The Extraction Function
async def extract_transactions(transcript_text: str, today: date, allowed_accounts: List[str]) -> dict | None:
    """Takes raw text and safely extracts a structured JSON list of transactions.

    'allowed_accounts' is the list of payment methods the model may choose from - names only.

    'today' is passed in rather than read from the clock here, so the prompt and the date
    resolver that runs afterwards (app/services/dates.py) share one value - otherwise a
    message sent just before midnight could be read against one day and resolved against
    the next.
    """
    # The weekday is included because "last Tuesday" can't be read without knowing what day
    # today is - with only the date, the model had to work that out itself.
    today_str = today.strftime("%A %Y-%m-%d")

    # The valid payment methods come from the caller - the bot passes the account names it just
    # loaded from the database (#53) - so a card added in the accounts table is offered to the
    # model on the very next message, and this module reads no household data of its own.
    allowed_accounts = ", ".join(allowed_accounts)

    try:
        response = await openai_client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
                # CHANGED: Added a strict 'No Translation' rule directly to the system prompt engine
                {"role": "system", "content": f"""You are a strict financial data extraction tool. Today is {today_str}.
                                                Extract notes in the EXACT original language. DO NOT translate into Malay,
                                                Indonesian, or any other language. Messages are often short and typed:
                                                a bare number in them is the price ('Lunch 15 yday' is 15). If the
                                                message contains no price at all, leave amount null and set
                                                needs_review to true. Never guess an amount that isn't there.
                                                The only valid payment methods are: {allowed_accounts}.
                                                Use a value from that list and nothing else. If the user did not say how
                                                they paid, leave payment_method null - do not guess, and do not pick the
                                                only card that happens to look plausible.
                                                For the date, record only what the user said in
                                                date_reference - never calculate a date yourself.
                                                Typed shorthand: 'yday' and 'ytd' both mean yesterday.
                                                Record who shares only in split_reference, only when the message asks
                                                to split or share the cost; never work out a share yourself.
                                                Read counts per person/family, default_count and count_unit.
                                                'Carol and Dave 4' is two people, each count 4, not families;
                                                'Carol's family 4' is a family with count 4.
                                                Fixed amounts/percentages set uneven
                                                true but are never counts.
                                                A share of a cost is part of that cost, not another
                                                expense: 'I paid 135 split with Bob. Bob's split is 90'
                                                is ONE expense of 135.
                                                Record who paid in payer_name, null unless the message says someone
                                                other than the sender paid; sharing with someone does not mean they paid.
                                                Numeric dates are day first: 3/7 is 3 July."""},
                {"role": "user", "content": transcript_text}
            ],
            response_format=TransactionList,
        )
        
        # without mode='json' enums like 'Transport' was being passed as 'ExpenseCategory.Transport' instead
        return response.choices[0].message.parsed.model_dump(mode='json')
        
    except Exception as e:
        print(f"❌ Extraction Error: {e}")
        return None
