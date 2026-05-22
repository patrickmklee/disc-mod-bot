# Slash Command Migration — Phase 1 + Phase 2

**Design choice.** Use `commands.hybrid_command` for all three commands. One
decorator registers both `!report` and `/report`; existing pure-function code
in `report.py` is untouched; tests for `_aggregate`, `filter_trades_*`,
`resolve_window`, `_embed_to_webhook_dict` stay green. The prefix kwarg
syntax changes from `--symbol=AMD` to `symbol:AMD` (one README note —
discord.py's prefix kwarg style).

## Phase 1 — hybrid migration

### `src/discord_mod_bot/bot.py`

1. **Drop the custom argument plumbing.** Remove the `_register_commands`
   `parse_args(raw_args, _SPECS[…])` pattern and the `CommandArgs` parameter
   on `_cmd_*` handlers. Each `_cmd_*` takes typed kwargs directly. `_SPECS`
   survives only as a place to document admin gating per command (or moves
   into the decorators).

2. **Rewrite the three handlers as hybrid commands** in `_register_commands`:

   ```python
   @self.bot.hybrid_command(description="Trading webhook server health check.")
   async def health(ctx: commands.Context):
       if not await self._gate(ctx, "health"): return
       await self._cmd_health(ctx, ephemeral=True)

   @self.bot.hybrid_command(description="Show open positions from the trading server.")
   async def positions(ctx: commands.Context):
       if not await self._gate(ctx, "positions"): return
       await self._cmd_positions(ctx)

   @self.bot.hybrid_command(description="Performance report posted in this channel.")
   @app_commands.default_permissions(administrator=True)
   @app_commands.describe(
       period="Time window preset (use 'custom' with from_date/to_date)",
       from_date="Custom range start, YYYY-MM-DD (overrides period)",
       to_date="Custom range end, YYYY-MM-DD (defaults to now)",
       symbol="Filter to a single ticker",
       strategy="Filter to a strategy name",
       channel="Filter to a signal-source channel",
   )
   async def report(
       ctx: commands.Context,
       period: Literal["today", "week", "month", "custom"] = "today",
       from_date: Optional[str] = None,
       to_date: Optional[str] = None,
       symbol: Optional[str] = None,
       strategy: Optional[str] = None,
       channel: Optional[str] = None,
   ):
       if not await self._gate(ctx, "report"): return
       await self._cmd_report(ctx, period=period, from_date=from_date, ...)
   ```

3. **`_cmd_report`** changes its body trivially: branch on
   `period == "custom"` (require `from_date`, call `resolve_custom_window`),
   else `resolve_window(period)`. The three filter calls stay identical —
   they already take strings.

4. **`_gate`** stays. `_channel_allowed` stays — call it on
   `ctx.channel.id`. `ctx.send(..., ephemeral=True)` works in both surfaces;
   on prefix it's silently downgraded to a normal send (we'll only use
   ephemeral for `/health` and gate-rejection messages).

5. **`setup_hook` for syncing.** Subclass-free — assign on the bot:

   ```python
   async def _setup_hook():
       guild_id = os.environ.get("MOD_BOT_SYNC_GUILD_ID", "").strip()
       if guild_id.isdigit():
           guild = discord.Object(id=int(guild_id))
           self.bot.tree.copy_global_to(guild=guild)
           await self.bot.tree.sync(guild=guild)
           LOG.info("Synced app commands to dev guild %s", guild_id)
       else:
           LOG.warning(
               "MOD_BOT_SYNC_GUILD_ID unset; skipping sync. Set it for "
               "dev, or run a one-off global sync manually."
           )
   self.bot.setup_hook = _setup_hook
   ```

   Global sync is opt-in (rare, propagates over an hour). Dev iteration
   is instant.

### `src/discord_mod_bot/commands.py` + `tests/test_*` for parser

Delete `commands.py` and the four `parse_args` tests in
`test_discord_mod_bot_report.py` (they're at the bottom). The hybrid
command system replaces every job that module did.

### `README.md`

Two edits:
- Replace `!report --symbol=AMD` examples with `/report symbol:AMD` (and
  note prefix flag syntax is `key:value`, no double-dash, during the
  transition window).
- Add a one-line dev section: `Set MOD_BOT_SYNC_GUILD_ID in .env to get
  instant slash registration on your dev guild.`

## Phase 2 — UX polish on top

### Autocomplete (`src/discord_mod_bot/autocomplete.py`, ~80 lines)

A single `AutocompleteCache` instance held by `DiscordModBot`:

```python
class AutocompleteCache:
    def __init__(self, client: WebhookClient, ttl_seconds: float = 60.0): ...
    async def symbols(self) -> list[str]:    # from /positions
    async def strategies(self) -> list[str]: # distinct field across recent /trades
    async def channels(self) -> list[str]:   # distinct field across recent /trades
```

Each method: check cache freshness, otherwise
`await asyncio.to_thread(self.client.get_json, …)`, extract distinct values,
return. Fail-open (return `[]` on error; autocomplete is best-effort and
Discord has a 3s budget).

Three autocomplete callbacks on `report`:

```python
@report.autocomplete("symbol")
async def _sym_ac(interaction, current: str):
    return _matching_choices(await self._ac_cache.symbols(), current)
# Same shape for strategy, channel.
```

`_matching_choices` is a 5-line helper: case-insensitive `startswith`
first, then `in`, capped at 25 (Discord's limit).

### Ephemeral defaults

- `/health` → ephemeral response.
- Gate rejection messages (`!{name} is disabled`, `admin-only`) → ephemeral.
- `/positions`, `/report` → public (they're for sharing).

### Choices, not just Literal

Optional polish: replace `Literal[…]` on `period` with `Choice[str]` so the
dropdown shows nicer labels ("Today" instead of "today"). 4-line change in
the decorator, no logic impact.

## Test plan

- Pure-function tests in `test_discord_mod_bot_report.py` — all keep passing
  (no API surface changes to `report.py`).
- Embed-shape test `_embed_to_webhook_dict` — keep passing.
- New tests: `AutocompleteCache` with a fake `WebhookClient` (TTL behavior,
  distinct-extraction, empty-on-error).
- Delete the 4 `parse_args` tests at the bottom of
  `test_discord_mod_bot_report.py`.

End state: ~40 passing tests (44 − 4 deleted + ~4 new for the cache).

## What's *not* in this phase

- No persistent dashboard / `discord.ui.View` / `tasks.loop` — that's Phase 3.
- No global sync command and no UI for "which guilds are registered" —
  env-var-driven is enough.
- No removal of `intents.message_content = True` — prefix surface is still
  live during migration; drop the intent in Phase 4 after sunsetting `!`.
- No new error-handling abstractions — existing `on_command_error` covers
  prefix, and hybrid commands route slash errors through it too.

## Effort

- **Phase 1** (~½ day): bot.py rewrite + delete `commands.py` + sync wiring
  + README.
- **Phase 2** (~½ day): `autocomplete.py` + 3 autocomplete decorators +
  `@app_commands.describe` strings + ephemeral toggles + `Choice` labels.
