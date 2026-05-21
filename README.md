# Discord Mod Bot

Standalone Discord command bot for operator/mod commands. It is separate from
`app.options.discord_bot` and only proxies Discord commands to the local webhook
server.

## Configure

Add a separate Discord bot token to `.env`:

```env
DISCORD_MOD_BOT_TOKEN="your-discord-mod-bot-token"
MOD_BOT_WEBHOOK_BASE_URL="http://127.0.0.1:5555"
MOD_BOT_COMMAND_PREFIX="!"
# MOD_BOT_CHANNEL_IDS="123456789012345678,234567890123456789"
# MOD_BOT_AUTH_TOKEN="same-token-as-webhook.auth_token-if-enabled"
```

The Discord application must have the Message Content intent enabled because
this bot uses prefix commands like `!positions`.

## Install

The project is managed with [uv](https://docs.astral.sh/uv/). From the repo root:

```sh
uv sync
```

That creates `.venv`, installs runtime + dev dependencies, and installs this
project itself so the `discord-mod-bot` console script is available.

## Run

Start the trading/options webhook server first so `/positions` is available on
port `5555`, then run either:

```sh
uv run discord-mod-bot
# or, equivalently:
uv run python -m discord_mod_bot
```

## Test

```sh
uv run pytest
```

## Configuration (`config.yaml`)

`src/discord_mod_bot/config.yaml` controls which commands are enabled, who
can run them, and the Discord webhook URLs the bot posts to. Override the
path with `MOD_BOT_CONFIG_PATH`.

```yaml
command_prefix: !
commands:
  positions: { enabled: true, allowed-by: admin }
  health:    { enabled: true, allowed-by: members }
  report:    { enabled: true, allowed-by: admin }
  close:     { enabled: true, allowed-by: admin }
webhooks:
  report:
    enabled: true
    name: Bikini Bottom News
    url: webhooks/<id>/<token>     # path-only or full https URL both work
```

`allowed-by: admin` requires the Discord Administrator guild permission;
`members` lets anyone in an allowed channel run the command. New commands
should also register a `CommandSpec` in `src/discord_mod_bot/bot.py:_SPECS`.

## Commands

- `!health` -- `GET /health` from the trading webhook server.
- `!positions` -- `GET /positions`, rendered as a Discord embed.
- `!report [period] [--here]` -- generate a performance report (period
  defaults to `today`; also accepts `week` and `month`) and post it to the
  `webhooks.report` Discord webhook. Pass `--here` to also preview it in
  the originating channel.

Arguments are parsed by `discord_mod_bot.commands.parse_args`, which
understands a positional subcommand, bare `--flag`s and `--key=value`
options -- enough scaffolding for future commands without changing the
plumbing.

