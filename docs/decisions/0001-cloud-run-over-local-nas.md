# ADR-0001: Deploy the ingestion layer to Cloud Run, not a local NAS

**Status:** Accepted  
**Date:** 2026-07  
**Code:** `Dockerfile`, `app/bot_webhook.py`

## Context

The bot has to be reachable whenever an expense happens, including while travelling. A NAS at home is the obvious alternative and keeps everything private.

## Decision

Deploy the Telegram bot to Google Cloud Run. The sensitive ledger/reconciliation layer stays decoupled.

## Alternatives considered

Local NAS hosting: maximum privacy, but single points of failure the household actually has — home ISP stability, power outages, and strict DNS/firewall setups on foreign networks.

## Consequences

High availability and near-zero operational overhead, at the cost of household financial data transiting a cloud provider. Cloud Run sleeps idle containers, which is what forces the webhook transport in [ADR-0003](0003-split-bot-transport-from-logic.md) and rules out long polling in production.
