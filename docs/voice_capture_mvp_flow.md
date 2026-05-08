# Voice Expense Capture MVP Flow (LED-009)

## 1. Interaction Journey (Happy Path)
1. **Guide Display**: Before recording, the bot displays a short "Guide" (e.g., "Mention: Amount, Description, Payment Method, and any Splits").
2. **Submission**: User sends a voice note via Telegram.
3. **Reception**: Bot immediately responds: "Extracting expense... ⏳"
4. **Processing**: Backend transcribes audio and extracts entities:
   - Amount
   - Description (Category/Vendor)
   - Payment Method (Card/Cash)
   - Splits (Names or "Everyone")
5. **Confirmation**: Bot responds with structured summary:
   - "Found: $25.00 for Lunch at Warung XYZ. Paid by: Card. Split: Shared. Confirm?"
6. **Save**: User clicks [Confirm] button.
7. **Success**: Bot replies: "Data uploaded to Ledger! ✅"

## 2. Failure Paths & Error Handling
* **Inaudible Audio**: If the AI cannot detect speech, the bot asks for a new voice note.
* **Missing Amount**: If the amount is not found, the bot offers two buttons: [Manual Entry] or [New Voice Note].
* **Entity Ambiguity**: If names don't match the pre-filled list (Sunil, Leonard, Priscy), the bot will ask for clarification.

## 3. Scope Boundaries
* **In-Scope**: Voice transcription, simple entity extraction, manual confirmation, local DB storage.
* **Out-of-Scope**: Receipt image parsing (OCR), real-time currency conversion (FX rates), complex recurring expense logic.

## 4. Known Constants (Look-up Tables)
To improve accuracy, the bot will prioritize matching these entities:
* **Users**: Sunil, Varsha, Sujay, Laura, Leonard, Esther, Danny, Priscy, Sathi, Krissy, Tryphy
* **Payment Methods**: OCBC Infinity Card, DBS Cashback Card, Paynow, Cash, OCBC Nets card, DBS Nets card, GrabPay, ApplePay, default = card