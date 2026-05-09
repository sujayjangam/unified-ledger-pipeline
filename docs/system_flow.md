# Ledger System Flow

This document outlines the end-to-end data lifecycle of the Unified Ledger system.

## 1. Data Entry (Input) 📥
Transactions enter the system through two main channels:
* **CLI (Manual)**: Via `app/add_expense.py` for quick terminal-based entries.
* **REST API**: Via a FastAPI service (`app/main.py`) which allows external tools or mobile apps to send transaction data.

## 2. Validation and Processing ⚙️
Before storage, every transaction passes through the "Processing Layer":
* **Validation (Pydantic)**: The API uses Pydantic models to enforce data types (e.g., ensuring 'amount' is a number) and required fields.
* **Precision Handling**: Amounts are converted from decimal dollars (float) to integer cents to avoid floating-point rounding errors during future calculations.
* **Metadata Tagging**: Each transaction is assigned a unique UUID and a source tag (e.g., 'API' or 'Manual').

## 3. Storage 🗄️
The processed data is stored in a local **SQLite database** (`data/ledger.db`). 
* **Table**: `transactions`
* **Primary Key**: `transaction_id` (UUID)

## 4. Future Intelligence (ML Pipeline) 🤖
The system is designed to facilitate automated categorization and reconciliation in later phases:
* **Feature Extraction**: The `description` field is prioritized as the primary feature for NLP-based categorization.
* **Matching Logic**: The system will eventually compare manually entered "API" transactions against bank statement imports to reconcile accounts.

## 5. System Components 🏗️
* **Database**: SQLite (Relational storage)
* **API Framework**: FastAPI (Asynchronous Python)
* **Data Validation**: Pydantic
* **Interface**: Swagger UI / CLI