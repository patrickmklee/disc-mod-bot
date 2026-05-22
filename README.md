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
# MOD_BOT_SYNC_GUILD_ID="123456789012345678"   # dev guild for instant slash registration
```

The Discord application must have the Message Content intent enabled because
this bot uses prefix commands like `!positions`. Set `MOD_BOT_SYNC_GUILD_ID`
to your dev guild ID to have slash commands appear instantly there; without
it, the bot leaves the command tree alone (global sync is opt-in).

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
register inside `_register_commands` in `src/discord_mod_bot/bot.py`.

## Commands

All commands are registered as **hybrid commands** — the same handler
serves both prefix (`!report`) and slash (`/report`) invocations. Slash
arguments take their values via the native Discord UI (dropdowns, typed
fields, autocomplete). For prefix invocations, kwargs use `key:value`
syntax (no double-dash).

- `/health` (or `!health`) — `GET /health`. Slash response is ephemeral
  (visible only to the invoker).
- `/positions` (or `!positions`) — `GET /positions`, rendered as an embed.
- `/report` (or `!report`) — performance report posted in the same channel.
  Slash UI: a `period` dropdown, optional `from_date` / `to_date`
  (YYYY-MM-DD), and `symbol` / `strategy` / `channel` filters with live
  autocomplete from the trading server. Examples:

  - `/report` — today (default)
  - `/report period:week`
  - `/report period:custom from_date:2026-05-15 to_date:2026-05-18`
  - `/report period:week symbol:AMD`
  - `/report period:month strategy:momentum`

  Filters compose (window first, then symbol / strategy / channel). Trades
  missing the filtered field are dropped, so empty results signal that the
  filter didn't apply rather than that nothing matched.

