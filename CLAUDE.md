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

**Pure-function split.** `report.py`, `commands.py`, and `config_loader.py`
are I/O-free and have no `discord.py` dependency — they're imported and
unit-tested directly. `bot.py` is the only file that touches `discord.py`
or the trading webhook. When adding logic, push it into the pure modules so
it stays testable; reserve `bot.py` for wiring.

**Command flow.** `bot.py:_register_commands` registers each `@bot.command`
which (1) calls `_gate(ctx, name)` — channel allowlist + YAML `enabled` +
`allowed-by: admin` check — then (2) calls the corresponding
`_cmd_*(ctx, args)` handler. Args come through `commands.parse_args`, which
splits raw discord-args into `subcommand / positional / flags / options`.
Per-command metadata lives in `_SPECS` (declared near the top of `bot.py`).

**Adding a new command** requires three coordinated edits: add a
`CommandSpec` to `_SPECS`, register a handler in `_register_commands`, and
add a matching entry under `commands:` in `config.yaml` (otherwise it
silently defaults to enabled / members-only).

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

`docs/slash-migration.md` contains a concrete plan to migrate the three
prefix commands to discord.py hybrid commands (slash + prefix) with
autocomplete and ephemeral responses. Read it before touching `bot.py`
command registration — the current handler shape will change.
