# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this bot is

A standalone Discord bot for operator/mod commands. It does **not** trade or
parse market data — it forwards Discord commands to a separate trading
webhook server (expected at `MOD_BOT_WEBHOOK_BASE_URL`, default
`http://127.0.0.1:5555`) and renders the responses as Discord embeds. The
bot must be run alongside that trading server; commands like `!positions`
and `!report` are no-ops if it's unreachable.

## Common commands

```sh
uv sync                                          # install (creates .venv, installs project editably)
uv run discord-mod-bot                           # run the bot (also: uv run python -m discord_mod_bot)
uv run pytest                                    # all tests
uv run pytest tests/test_discord_mod_bot_report.py -k resolve_window   # one test / pattern
uv run --no-sync discord-mod-bot                 # iterate without re-resolving deps each run
```

Environment knobs (set in `.env`):

- `DISCORD_MOD_BOT_TOKEN` — required.
- `MOD_BOT_WEBHOOK_BASE_URL` — trading server base, default `http://127.0.0.1:5555`.
- `MOD_BOT_COMMAND_PREFIX` — default `!`.
- `MOD_BOT_CHANNEL_IDS` — comma-separated allowlist; empty = all channels.
- `MOD_BOT_AUTH_TOKEN` — sent as `Authorization` to the trading server if set.
- `MOD_BOT_CONFIG_PATH` — override location of `config.yaml`.
- `MOD_BOT_LOG_LEVEL` — default `INFO`.

## Architecture

**Two-layer config.** `.env` (loaded in `bot.py:ModBotConfig.from_env`) holds
secrets and per-host wiring. `config.yaml` (loaded in `config_loader.py`,
shipped inside the package at `src/discord_mod_bot/config.yaml`) holds
declarative per-command gates and webhook URLs. They're intentionally
separate so the YAML can be committed while secrets stay out of git.

**Pure-function split.** `report.py`, `alert_grades.py`, `autocomplete.py`,
and `config_loader.py` have no `discord.py` dependency — they're imported and
unit-tested directly. `bot.py` is the only file that touches `discord.py`
or the trading webhook. When adding logic, push it into the pure modules so
it stays testable; reserve `bot.py` for wiring.

**Command flow.** `bot.py:_register_commands` registers each
`@bot.hybrid_command` (slash + prefix from one decorator) which (1) calls
`_gate(ctx, name)` — channel allowlist + YAML `enabled` + `allowed-by:
admin` check — then (2) delegates to a thin `_cmd_*(ctx, **kwargs)` handler
that tests can call directly. Arguments are typed function parameters;
`app_commands.describe` and `.autocomplete` decorate the command.

**Adding a new command** requires two coordinated edits: register the
command + handler in `_register_commands`, and add a matching entry under
`commands:` in `config.yaml` (otherwise it silently defaults to enabled /
members-only). New slash commands only appear if `MOD_BOT_SYNC_GUILD_ID`
is set.

**Alert-grading report.** `/grade [date]` and
`python -m discord_mod_bot.post_alert_grades [date] [--dry-run]` render the
ledger that pytrade-bot writes to `MOD_BOT_ALERT_GRADES_DIR` (default
`~/pytrade-signal-grades`). That ledger is the contract between the repos:
`discord.json` is already post-shaped and every number belongs to
pytrade-bot — `alert_grades.py` reads it verbatim and never recomputes or
reformats grades. Missing fields are pytrade-bot's to add. Records on disk
are verdict v1; `alert_card` renders whichever verdict keys a record
carries so v2 needs no change here. `MOD_BOT_ALERT_GRADES_ENV` picks the
destination channel and production is opt-in — anything but the literal
`production` posts to the dev channel.

**Webhook proxy model.** `WebhookClient` (in `bot.py`) does the outbound
GETs against the trading server. `DiscordWebhookPoster` and the
`webhooks.report` block in `config.yaml` are **currently unused** — they
were used when `!report` POSTed to a Discord webhook, but the command now
replies in-channel. Leave them in place unless cleaning up deliberately.

## Code style notes

- **Bias toward concise, elegant code.** Don't introduce abstractions for
  hypothetical future needs, don't add fallbacks for cases that can't happen,
  don't write helper layers unless a duplication actually exists. Three
  similar lines beats a premature helper.
- **Smoke-testing the running bot.** `uv run discord-mod-bot` blocks on the
  Discord connect; for a quick "does it import and reach `main()`?" check
  use `timeout 4 uv run discord-mod-bot` and look at stderr — anything past
  the import-error wall is success.
- Source files use **tab indentation** (existing convention — match it when
  editing).
- Imports are **absolute** throughout (`from discord_mod_bot.bot import …`,
  never relative). The src-layout requires this; tests import the same way.
- Tests are **pytest functions with bare `assert`** (not `unittest.TestCase`).
  Time-sensitive tests in `test_discord_mod_bot_report.py` pass `now=` to
  `resolve_window` / `resolve_custom_window` so they don't drift; preserve
  that pattern.

## In-flight work

`docs/handoffs/2026-09-04-alert-grades-report.md` is the plan for the
alert-grading report. Phase 1 (config, loader, sender, poster, `/grade`) is
done; phase 2 (`views.py` — select menu, ephemeral alert cards, the three
buttons) and phase 3 (Components V2, blocked on the design) are not.
`docs/slash-migration.md` is the completed hybrid-command migration, kept
for context.
