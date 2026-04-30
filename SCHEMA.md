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
- **transaction_currency**: ISO 3-letter code
- **base_amount**: Final value in SGD (Integer Cents)
- **account_owner**: Card owner (e.g., 'Sujay', 'Wife')
- **benefit_of**: Beneficiary (e.g., 'Sujay', 'Wife', 'Shared')
- **split_ratio**: Decimal (e.g., 0.5)
- **category**: Budget group
- **transaction_type**: 'income' or 'expense'
- **source**: Data origin
- **reconciliation_status**: 'settled' or 'unsettled'
