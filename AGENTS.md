# AGENTS.md

Read `CLAUDE.md` in this repo root before doing anything else. Its rules apply to you, and the
"Workers" section there is the full version of what follows.

## If you were started as a worker (Codex, agy)

- You were most likely started from a brief in `.work/briefs/`. Do what it says, stay inside the
  files it names, and make your final message the result it asks for. Claude reads only that
  message and `git diff --stat`.
- Claude reviews and runs the gate (`pytest`, `ruff check .`, the CI import smoke check) on
  anything you write, and Claude commits. Keep diffs clear, state any assumption, and don't widen
  the scope beyond the brief.
- Codex is also the second set of eyes on Claude's own work (`codex exec review --uncommitted`, or
  `--base main` for a finished branch).

## Fences

- Only this repo. Never read `.env`, `.env.local`, `directory_seed.local.json`, `claude-plan-*.md`
  or anything under `Test PDF_CSV/` (real financial statements).
- Never run `alembic`, `scripts/seed_directory.py`, the bot or anything else that opens a database
  connection: `alembic/env.py` reads `.env`, which is production.
- Never commit, push, deploy, open PRs or create GitHub issues — Claude Code does those, and only
  when the user asks.
