# Local testing

How to exercise the bot end to end on your own machine without touching production.

## Why this is not just `python -m app.bot_polling`

`run_polling()` **deletes whatever webhook is registered against the token it runs with** —
`telegram/ext/_updater.py` passes `webhook_url=""` into `_bootstrap`, which unconditionally calls
`delete_webhook` when no URL is given. Running the polling loop against the production token
therefore takes the deployed Cloud Run bot offline until the next redeploy, silently: nothing
errors, messages simply stop arriving.

So local testing uses a **second bot, with its own token**, created in BotFather. Its webhook is
never set, so polling it is free of side effects.
See [ADR-0019](decisions/0019-separate-bot-token-for-local-testing.md).

## One-time setup

### 1. Confirm the ignore rule is in place

`.gitignore` covers `.env` **and** `.env.*` — the second rule matters, because `.env` is a literal
filename to git, not a prefix, so it would not have covered `.env.local`. Verify before creating
anything:

```bash
git check-ignore -v .env.local
```

That must print a matching rule. If it prints nothing, stop and fix `.gitignore` first.

### 2. Create the test bot

In Telegram, message [@BotFather](https://t.me/BotFather) → `/newbot` → give it a name and a
username ending in `bot`. It replies with a token.

**The token stays between BotFather and your disk.** Don't paste it into a chat, a commit, an
issue, a screenshot, or a terminal command (shell history is a file too).

### 3. Create `.env.local`

In the repo root, create `.env.local` containing **only** the values that must differ from
production:

```dotenv
# Local testing only. Gitignored. Never commit or share.
TELEGRAM_BOT_TOKEN=<the token BotFather just gave you>
DATABASE_URL=postgresql+psycopg://<neon branch connection string>
```

Everything else — `OPENAI_API_KEY`, `ALLOWED_TG_IDS`, `ACCOUNT_OWNERS`, `ALLOWED_ACCOUNTS` — is
deliberately **absent**, and keeps coming from `.env`. `app/bot_local.py` loads `.env.local` first
with `override=True`; `bot_core`'s own `load_dotenv()` defaults to `override=False`, so it fills in
the gaps without disturbing the overrides. One copy of each secret on disk, not two.

`ALLOWED_TG_IDS` carries over unchanged because a Telegram user ID identifies *you*, not the bot
you're messaging — the same ID authorises you on both bots.

### 4. Point `DATABASE_URL` at a Neon branch (recommended)

In the Neon console: **Branches → New branch** from `main`, then copy its connection string and
rewrite the scheme to `postgresql+psycopg://` (a plain `postgresql://` URL makes SQLAlchemy reach
for the uninstalled `psycopg2` — see [ADR-0006](decisions/0006-psycopg3-url-scheme.md)). A branch
is a copy-on-write snapshot, so test rows never reach the real ledger and the branch can be deleted
afterwards.

If you skip this, `app/bot_local.py` refuses to start, because there is no delete path for a row
saved by mistake yet ([#22](https://github.com/sujayjangam/unified-ledger-pipeline/issues/22)). To
override for a single run, opt in explicitly:

```powershell
$env:ALLOW_PROD_DB = '1'; python -m app.bot_local
```

## Running

```bash
micromamba activate ledger-env    # from the repo root
python -m app.bot_local
```

On startup it prints the username of the bot it actually connected as. Check that line — it is the
cheapest possible confirmation that you're not driving production.

Two guards run before the bot is even built:

1. `.env.local` must set `TELEGRAM_BOT_TOKEN`, and it must differ from the one in `.env`.
2. `.env.local` must set `DATABASE_URL`, unless `ALLOW_PROD_DB=1` is set for that run.

Stop the bot with `Ctrl+C`. There is nothing to undo afterwards — the production webhook was never
touched.

## If a token is ever exposed

Message BotFather → `/revoke` → pick the bot. The old token dies immediately and you get a new one.
Do this for the *production* bot too if its token ever lands somewhere it shouldn't, then update the
Cloud Run secret and redeploy.
