# ADR-0025: Authenticate webhook deliveries with Telegram's secret token

**Status:** Accepted
**Date:** 2026-09-10
**Issues:** [#60](https://github.com/sujayjangam/unified-ledger-pipeline/issues/60) (adds sender IDs to rows)
**Code:** `app/bot_webhook.py::telegram_webhook`, `::lifespan`, `::_is_from_telegram`

## Context

In production the bot receives updates by webhook: Telegram POSTs each update to `/webhook` on
Cloud Run. Who may use the bot is decided by `is_authorized()`, which checks the sender's Telegram
user ID *inside* the update against `ALLOWED_TG_IDS`.

That check can only be trusted if Telegram wrote the update. The webhook had no way to tell a
delivery from Telegram apart from any other HTTP request.

Telegram provides a mechanism for exactly this. `setWebhook` accepts a `secret_token` (1-256
characters of `A-Z a-z 0-9 _ -`), and Telegram sends it back in an
`X-Telegram-Bot-Api-Secret-Token` header on every delivery. The Bot API docs describe it as
"useful to ensure that the request comes from a webhook set by you."

[#60](https://github.com/sujayjangam/unified-ledger-pipeline/issues/60) is about to store sender
IDs on every row, and in backups. That makes it worth confirming that the ID inside an update can
be trusted before storing more copies of it.

## Decision

- **Where the token lives.** `WEBHOOK_SECRET_TOKEN` is held in Secret Manager, like the other
  secrets, and never in the repo.
- **Registration.** It is registered through `setWebhook(secret_token=...)` on every startup, so
  Telegram always holds the token this process checks against.
- **Checking each request.** `/webhook` compares the header with `hmac.compare_digest` and returns
  403 on any mismatch. The check runs *first*, before the body is parsed and before the
  `update_id` enters the dedupe cache. A rejected request therefore can't reach the bot, and
  can't make a real delivery with the same `update_id` look like a duplicate.
- **Failing closed.** If `WEBHOOK_URL` is set and the token is missing or malformed, the server
  refuses to start. With no token configured, every request is rejected.

## Alternatives considered

**Keep relying on the URL staying unknown.** Rejected. The URL has never been published, but a
URL isn't a secret. It shows up in logs, dashboards and any tool that lists the service. Hoping it
stays hidden is not a control.

**Put a secret in the URL path**, e.g. `/webhook/<random>`. Rejected. It would work, but the
secret would then appear wherever the URL does, including request logs. The header is Telegram's
own mechanism, and it keeps the secret out of the URL.

**Allow only Telegram's published IP ranges.** Rejected. Those ranges have to be tracked and kept
current as Telegram changes them. That is ongoing maintenance, for a guarantee the token already
gives.

## Consequences

**Bought:**

- The sender ID inside an update can be trusted, because only Telegram can produce a request that
  passes the check. `is_authorized()` means what it looks like it means.
- `handle_button_click` still doesn't call `is_authorized()` (noted in `ROADMAP.md`). That now
  matters much less, because a button press can only arrive through Telegram.

**Cost:**

- **One more secret to manage.**
- **A rollout order.** The secret has to be attached to the Cloud Run service *before* the code
  that requires it deploys.
- **Local webhook testing needs the token.** Running the webhook server locally
  (`uvicorn app.bot_webhook:app_fastapi`) rejects every request unless `WEBHOOK_SECRET_TOKEN` is
  set. The usual local runner, `app.bot_local`, uses polling and is unaffected.
