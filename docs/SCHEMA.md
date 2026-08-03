# Ledger Canonical Schema (v1.3 - Household Unified)

## Core Design Decisions
1. **Multi-Entity Tracking**: Records ownership (account_owner) vs. beneficiary (benefit_of).
2. **Flexible Splitting**: 'split_ratio' handles shared (0.5) or personal (1.0) allocation.
3. **Multi-Currency Support**: Tracks raw transaction data vs. base SGD amount.
4. **Integer Cents**: All money stored as integers for mathematical precision.

## Fields
- **transaction_id**: UUID (Primary Key)
- **date**: ISO 8601 string (YYYY-MM-DD)
- **description**: Raw bank text
- **amount**: Positive integer (cents)
- **currency**: ISO 3-letter code
- **base_amount**: Final value in SGD (Integer Cents)
- **account_owner**: Card owner (e.g., 'Sujay', 'Wife')
- **benefit_of**: Beneficiary (e.g., 'Sujay', 'Wife', 'Shared')
- **split_ratio**: Decimal (e.g., 0.5)
- **category**: Budget group
- **transaction_type**: 'income' or 'expense'
- **source**: Data origin
- **reconciliation_status**: 'settled' or 'unsettled'
- **account_desc**: Payment method / card the transaction was charged to (e.g. 'YouTrip',
  'Cash'). Nullable — only the Telegram bot populates it today.
- **idempotency_key**: UUID, nullable, UNIQUE. Set by the Telegram bot (one per confirm prompt,
  generated when the transaction is presented for confirmation, reused on every save attempt for
  that same prompt) so a double-tap or webhook redelivery can't insert the same transaction twice.
  Manual/API inserts leave this NULL — the UNIQUE constraint allows multiple NULLs.
