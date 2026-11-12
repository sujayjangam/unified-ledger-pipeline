# AGENTS.md

Read `CLAUDE.md` in this repo root before doing anything else — it is the single source of truth
for this project's conventions, architecture, environment, and commands, written for any AI coding
agent working here, not just Claude Code. Everything in it applies to you.

## Working alongside Claude Code

Claude Code runs this project and hands work to two workers, Codex and agy. The full rules are in
"Workers" in `CLAUDE.md`; the short version for a worker:

- You were most likely started from a brief in `.work/briefs/`. Do what it says, stay inside the
  files it names, and make your final message the result it asks for. Claude reads only that
  message and `git diff --stat`.
- Claude reviews and runs the gate (`pytest`, `ruff check .`, the CI import smoke check) on
  anything you write, and Claude commits. Keep diffs clear, state any assumption, and don't widen
  the scope beyond the brief.
- Codex is also the second set of eyes on Claude's own work (`codex exec review --uncommitted`, or
  `--base main` for a finished branch).
- Only this repo. Never read `.env`, `.env.local`, `directory_seed.local.json` or
  `claude-plan-*.md`; never run `alembic`, `scripts/seed_directory.py`, the bot or anything that
  opens a database connection.

## A few things worth repeating for an agent without this project's conversation history

- Don't touch `.env`, `.env.local`, or anything under `Test PDF_CSV/` (real financial statement
  samples) — both are gitignored on purpose.
- Don't start implementation work unless the user asks for it in the current session, even if
  `ROADMAP.md` lists it as next.
- Never run `alembic upgrade head` without confirming the target host first — `alembic/env.py`
  reads `.env` (production) by default, never `.env.local`. See `docs/LOCAL_TESTING.md`.
- Never commit, push, open PRs or create GitHub issues — Claude Code does those, and only when the
  user asks.
