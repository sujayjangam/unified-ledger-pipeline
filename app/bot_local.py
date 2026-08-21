"""Local test runner: long-polls a *separate* test bot, never the production one.

Layered environment. `.env.local` (gitignored) is loaded first with `override=True`, then
`app.bot_core` loads `.env` the way it always does. `load_dotenv()` defaults to
`override=False`, so that second call leaves anything `.env.local` already set alone and
only fills in the gaps. `.env.local` therefore holds *only* what must differ for testing -
the test bot's token, and ideally a Neon branch `DATABASE_URL`. `OPENAI_API_KEY`,
`ALLOWED_TG_IDS` and `ACCOUNT_OWNERS` keep coming from `.env`, so there is exactly one copy
of each secret on disk.

Import order matters: `bot_core` reads `TELEGRAM_BOT_TOKEN` at module import time
(`bot_core.py:33`), so the override has to happen before that import - which is why
`from app.bot_core import get_application` sits below this module's setup code instead of
at the top of the file.

Why a separate token at all: `run_polling()` clears whatever webhook is registered against
the token it runs with (`telegram/ext/_updater.py` passes `webhook_url=""` into
`_bootstrap`), so polling the production token would silently take the deployed Cloud Run
bot offline until the next redeploy. See
`docs/decisions/0019-separate-bot-token-for-local-testing.md` and `docs/LOCAL_TESTING.md`.

Run from the repo root: `python -m app.bot_local`
"""
import os
import sys

from dotenv import dotenv_values, load_dotenv

LOCAL_ENV, PROD_ENV = ".env.local", ".env"

if not load_dotenv(LOCAL_ENV, override=True):
    sys.exit(
        f"❌ {LOCAL_ENV} not found. Run this from the repo root; see docs/LOCAL_TESTING.md "
        "for the keys it needs."
    )

local_cfg = dotenv_values(LOCAL_ENV)
prod_cfg = dotenv_values(PROD_ENV)

# Guard 1: never poll the production bot - doing so deletes its registered webhook.
local_token = local_cfg.get("TELEGRAM_BOT_TOKEN")
if not local_token or local_token == prod_cfg.get("TELEGRAM_BOT_TOKEN"):
    sys.exit(
        f"❌ {LOCAL_ENV} must set TELEGRAM_BOT_TOKEN to a different token than {PROD_ENV}.\n"
        "   Polling the production token takes the deployed bot offline."
    )

# Guard 2: falling through to the production DATABASE_URL is possible but has to be
# deliberate - there is no delete path for a row saved by mistake yet (issue #22).
if not local_cfg.get("DATABASE_URL"):
    if os.getenv("ALLOW_PROD_DB") != "1":
        sys.exit(
            f"❌ {LOCAL_ENV} does not set DATABASE_URL, so test entries would land in the\n"
            "   production ledger, which has no delete path yet (issue #22).\n"
            "   Point it at a Neon branch, or opt in explicitly for this run:\n"
            "     $env:ALLOW_PROD_DB = '1'; python -m app.bot_local"
        )
    print("⚠️  ALLOW_PROD_DB=1 - test entries WILL be written to the production ledger.")

from app.bot_core import get_application  # noqa: E402 - must follow the override above


async def announce(app):
    """Name the bot we actually connected as, so a token mix-up is obvious immediately."""
    me = await app.bot.get_me()
    print(f"✅ Polling as @{me.username}. The production webhook is untouched.")


if __name__ == "__main__":
    app = get_application()
    app.post_init = announce
    print("Bot logic initialized. Waiting for connection via POLLING (local test bot)...")
    app.run_polling()
