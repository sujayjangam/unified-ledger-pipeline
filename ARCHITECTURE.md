# Unified Ledger: Architecture & Design Decisions

This document outlines the core architectural decisions made for the Unified Ledger MVP, prioritizing a reliable data ingestion pipeline and separation of concerns.

## Architectural Decisions

### 1. Ingestion Layer: Cloud Run vs. Local NAS
**Context:** The system requires an "always-on" ingestion layer (Telegram Bot) to capture unstructured voice expenses while traveling.
**Trade-off:** Hosting the bot on a local NAS ensures maximum privacy but introduces single points of failure (home ISP stability, power outages, and strict DNS firewalls). 
**Decision:** We deployed the Telegram bot to a serverless cloud environment (Google Cloud Run). This ensures high availability and zero operational overhead during the MVP testing phase. The sensitive "Ledger/Reconciliation" layer remains decoupled and local. This demonstrates a modern, hybrid-cloud approach to data engineering.

### 2. Transcription Engine: OpenAI API vs. Local Whisper
**Context:** We need to convert unstructured audio into text.
**Trade-off:** Running OpenAI's Whisper model locally provides maximum privacy but requires heavy containerization (PyTorch, FFmpeg) and high RAM availability on the host machine. 
**Decision:** For the MVP, we utilize the OpenAI API. It requires minimal compute footprint (ideal for Cloud Run), offers superior speed, and allows us to ship the operational pipeline immediately. The core logic is modular, meaning the API call can be seamlessly swapped for a local LLM inference engine in Phase 2.

---

## Codebase Structure: Separation of Concerns

The Telegram bot is intentionally decoupled into three distinct files to separate the "Business Logic" from the "Transport Layer." This allows for seamless local testing and environment-agnostic deployment.

### `bot_core.py` (The Brain & Factory)
This file houses the core business logic, including the authentication gatekeeper and message handlers. 
* **Design Pattern:** It utilizes a Factory Pattern (`get_application()`). It builds and configures the bot engine but does *not* start the network connection. 
* **Advantage:** By isolating the logic from the network loop, the codebase is highly testable. We can run automated tests on the parsing logic without triggering live API calls to Telegram.
* **Security:** Implements an `is_authorized` middleware check to drop payloads from unauthorized Telegram IDs before processing begins.

### `bot_polling.py` (The Local Runner)
* **Purpose:** Used strictly for local development and testing.
* **Mechanism:** Imports the configured engine from `bot_core` and executes `app.run_polling()`. This creates a blocking, infinite loop that constantly queries Telegram for new messages. It bypasses the need to expose local ports to the internet via ngrok.

### `bot_webhook.py` (The Production Serverless Runner)
* **Purpose:** Used for production deployment on Google Cloud Run.
* **Mechanism:** Wraps the bot engine in a **FastAPI** web server. Because Cloud Run puts inactive containers to sleep, polling will fail. Instead, Telegram "pushes" payloads to the `/webhook` endpoint, waking the container up.
* **Lifespan Management:** Uses FastAPI's `asynccontextmanager` to safely initialize the bot connection during a "Cold Start" and gracefully shut down memory usage when the container spins down.