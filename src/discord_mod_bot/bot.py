"""Standalone Discord mod bot that proxies operator commands to webhooks.

Run with:
    uv run discord-mod-bot

Commands are registered as discord.py hybrid commands -- the same handler
serves both prefix (`!report`) and slash (`/report`) invocations during
the migration window.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from urllib.parse import urljoin

import requests
from dotenv import load_dotenv

from discord_mod_bot import autocomplete as ac
from discord_mod_bot import report as report_builder
from discord_mod_bot.config_loader import YamlConfig, load_yaml_config


LOG = logging.getLogger("discord_mod_bot")
EMBED_FIELD_LIMIT = 25
EMBED_FIELD_NAME_LIMIT = 256
EMBED_FIELD_VALUE_LIMIT = 1024
EMBED_DESCRIPTION_LIMIT = 4096
POSITION_FIELD_NAME_LIMIT = 80
POSITION_FIELD_VALUE_LIMIT = 220
MAX_POSITION_FIELDS = 18

COLOR_GAIN = 0x00C805
COLOR_LOSS = 0xFF5000
COLOR_NEUTRAL = 0x59636E


@dataclass(frozen=True)
class ModBotConfig:
    token: str
    webhook_base_url: str = "http://127.0.0.1:5555"
    command_prefix: str = "!"
    request_timeout_seconds: float = 10.0
    auth_token: str = ""
    channel_ids: tuple[int, ...] = ()
    yaml: YamlConfig = field(default_factory=YamlConfig)

    @classmethod
    def from_env(cls) -> "ModBotConfig":
        load_dotenv()

        token = (
            os.environ.get("DISCORD_MOD_BOT_TOKEN", "").strip()
            or os.environ.get("MOD_DISCORD_BOT_TOKEN", "").strip()
        )
        if not token:
            raise SystemExit(
                "Set DISCORD_MOD_BOT_TOKEN in .env or the environment before "
                "running python -m discord_mod_bot."
            )

        yaml_cfg = load_yaml_config()
        # CLI/env wins over YAML for the prefix so an operator can override
        # without editing the checked-in config file.
        env_prefix = os.environ.get("MOD_BOT_COMMAND_PREFIX", "").strip()
        prefix = env_prefix or yaml_cfg.command_prefix or "!"

        return cls(
            token=token,
            webhook_base_url=os.environ.get(
                "MOD_BOT_WEBHOOK_BASE_URL",
                "http://127.0.0.1:5555",
            ).strip().rstrip("/"),
            command_prefix=prefix,
            request_timeout_seconds=_float_env("MOD_BOT_REQUEST_TIMEOUT", 10.0),
            auth_token=os.environ.get("MOD_BOT_AUTH_TOKEN", "").strip(),
            channel_ids=_parse_channel_ids(os.environ.get("MOD_BOT_CHANNEL_IDS", "")),
            yaml=yaml_cfg,
        )


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        LOG.warning("Invalid %s=%r; using %.1f", name, raw, default)
        return default


def _parse_channel_ids(raw: str) -> tuple[int, ...]:
    ids: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            LOG.warning("Ignoring invalid Discord channel id %r", part)
    return tuple(ids)


def _is_admin(ctx) -> bool:
    """Return True if the command author has admin privileges.

    DMs have no guild_permissions, so admin-gated commands are blocked in
    DMs. This is intentional -- the report / close webhooks shouldn't be
    triggerable from a private channel.
    """
    perms = getattr(getattr(ctx, "author", None), "guild_permissions", None)
    if perms is None:
        return False
    return bool(getattr(perms, "administrator", False))


class WebhookClient:
    """Read-side client against the local trading webhook server."""

    def __init__(self, base_url: str, *, timeout: float = 10.0, auth_token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.auth_token = auth_token

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        headers = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        response = requests.get(url, headers=headers, params=params, timeout=self.timeout)
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(f"{url} did not return JSON") from exc


class DiscordWebhookPoster:
    """POST embeds to Discord webhook URLs (the `webhooks.*` config block).

    Separate from `WebhookClient` because the targets are Discord-hosted
    webhooks, not the local trading server, and they accept a different
    JSON shape (`embeds`, `username`, etc.).
    """

    def __init__(self, *, timeout: float = 10.0):
        self.timeout = timeout

    def post_embed(
        self,
        url: str,
        embed: dict[str, Any],
        *,
        username: str = "",
    ) -> None:
        if not url:
            raise RuntimeError("webhook URL is empty (check config.yaml -> webhooks)")
        body: dict[str, Any] = {"embeds": [_embed_to_webhook_dict(embed)]}
        if username:
            body["username"] = username[:80]
        response = requests.post(url, json=body, timeout=self.timeout)
        response.raise_for_status()


def _embed_to_webhook_dict(embed: dict[str, Any]) -> dict[str, Any]:
    """Convert our internal embed dict to the Discord webhook shape.

    Discord webhook embeds use the same field names as bot embeds with one
    difference: timestamp must be a string, not a datetime.
    """
    out: dict[str, Any] = {}
    for key in ("title", "description", "color", "url"):
        if embed.get(key) is not None:
            out[key] = embed[key]
    ts = embed.get("timestamp")
    if isinstance(ts, datetime):
        out["timestamp"] = ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    elif isinstance(ts, str) and ts:
        out["timestamp"] = ts
    fields = embed.get("fields") or []
    if fields:
        out["fields"] = [
            {"name": f.get("name", "-"), "value": f.get("value", "-"),
             "inline": bool(f.get("inline", False))}
            for f in fields
        ]
    footer = embed.get("footer") or {}
    if footer.get("text"):
        out["footer"] = {"text": footer["text"]}
    return out


class DiscordModBot:
    def __init__(self, config: ModBotConfig):
        discord, commands, app_commands = _load_discord()
        self.config = config
        self.discord = discord
        self.commands = commands
        self.app_commands = app_commands
        self.webhooks = WebhookClient(
            config.webhook_base_url,
            timeout=config.request_timeout_seconds,
            auth_token=config.auth_token,
        )
        self.discord_webhooks = DiscordWebhookPoster(
            timeout=config.request_timeout_seconds,
        )
        self.autocomplete = ac.AutocompleteCache(self.webhooks.get_json)

        intents = discord.Intents.default()
        intents.message_content = True
        self.bot = commands.Bot(command_prefix=config.command_prefix, intents=intents)
        self.bot.setup_hook = self._setup_hook
        self._register_events()
        self._register_commands()

    async def _setup_hook(self) -> None:
        """Sync app commands. Per-guild if MOD_BOT_SYNC_GUILD_ID is set
        (instant), otherwise skip (global sync is opt-in to avoid surprise
        registrations during dev).
        """
        guild_id = os.environ.get("MOD_BOT_SYNC_GUILD_ID", "").strip()
        if guild_id.isdigit():
            guild = self.discord.Object(id=int(guild_id))
            self.bot.tree.copy_global_to(guild=guild)
            synced = await self.bot.tree.sync(guild=guild)
            LOG.info("Synced %d app commands to guild %s", len(synced), guild_id)
        else:
            LOG.info(
                "MOD_BOT_SYNC_GUILD_ID unset; skipping app-command sync. "
                "Set it to register slash commands instantly on a dev guild."
            )

    def run(self) -> None:
        self.bot.run(self.config.token, log_handler=None)

    def _channel_allowed(self, channel_id: int) -> bool:
        return not self.config.channel_ids or channel_id in self.config.channel_ids

    async def _gate(self, ctx, name: str) -> bool:
        """Apply YAML enabled + allowed-by + channel allowlist gates.

        Returns True if the command should proceed. Rejection messages are
        sent ephemerally on the slash surface (silently ignored on prefix);
        channel-allowlist rejection drops silently as before.
        """
        if not self._channel_allowed(ctx.channel.id):
            return False
        cmd_cfg = self.config.yaml.command(name)
        if not cmd_cfg.enabled:
            await ctx.send(f"`{name}` is disabled in config.yaml.", ephemeral=True)
            return False
        if cmd_cfg.allowed_by.lower() == "admin" and not _is_admin(ctx):
            await ctx.send(f"`{name}` is admin-only.", ephemeral=True)
            return False
        return True

    def _register_events(self) -> None:
        @self.bot.event
        async def on_ready():
            user = self.bot.user
            LOG.info("Discord mod bot connected as %s (%s)", user, getattr(user, "id", "?"))
            if self.config.channel_ids:
                LOG.info("Commands enabled in channels: %s", self.config.channel_ids)
            else:
                LOG.info("Commands enabled in all channels")

        @self.bot.event
        async def on_command_error(ctx, error):
            if isinstance(error, self.commands.CommandNotFound):
                return
            LOG.exception("Command failed", exc_info=error)
            await ctx.send(f"Command failed: {_safe_error(error)}")

    def _register_commands(self) -> None:
        # Hybrid commands register both prefix (!foo) and slash (/foo)
        # surfaces from a single decorator. Handlers stay thin -- they gate
        # and delegate -- so tests can call _cmd_* directly without a bot
        # client.
        app_commands = self.app_commands
        Choice = app_commands.Choice

        @self.bot.hybrid_command(
            name="health",
            description="Trading webhook server health check.",
        )
        async def health(ctx):
            if not await self._gate(ctx, "health"):
                return
            await self._cmd_health(ctx)

        @self.bot.hybrid_command(
            name="positions",
            description="Show open positions from the trading server.",
        )
        async def positions(ctx):
            if not await self._gate(ctx, "positions"):
                return
            await self._cmd_positions(ctx)

        @self.bot.hybrid_command(
            name="report",
            description="Performance report posted in this channel.",
        )
        @app_commands.default_permissions(administrator=True)
        @app_commands.describe(
            period="Time window preset (use 'custom' with from_date/to_date).",
            from_date="Custom range start (YYYY-MM-DD); overrides period.",
            to_date="Custom range end (YYYY-MM-DD); defaults to now.",
            symbol="Filter to a single ticker.",
            strategy="Filter to a strategy name.",
            channel="Filter to a signal-source channel.",
        )
        async def report(
            ctx,
            period: Literal["today", "week", "month", "custom"] = "today",
            from_date: Optional[str] = None,
            to_date: Optional[str] = None,
            symbol: Optional[str] = None,
            strategy: Optional[str] = None,
            channel: Optional[str] = None,
        ):
            if not await self._gate(ctx, "report"):
                return
            await self._cmd_report(
                ctx,
                period=period,
                from_date=from_date,
                to_date=to_date,
                symbol=symbol,
                strategy=strategy,
                channel=channel,
            )

        async def _ac(values_coro, current):
            return [Choice(name=v, value=v) for v in ac.match(await values_coro, current)]

        @report.autocomplete("symbol")
        async def _symbol_ac(interaction, current: str):
            return await _ac(self.autocomplete.symbols(), current)

        @report.autocomplete("strategy")
        async def _strategy_ac(interaction, current: str):
            return await _ac(self.autocomplete.strategies(), current)

        @report.autocomplete("channel")
        async def _channel_ac(interaction, current: str):
            return await _ac(self.autocomplete.channels(), current)

    # ------------------------------------------------------------------
    # Handlers (one per command, kept thin so tests can call them directly)
    # ------------------------------------------------------------------

    async def _cmd_positions(self, ctx) -> None:
        """Fetch open position status from the local webhook server."""
        async with ctx.typing():
            try:
                payload = await asyncio.to_thread(self.webhooks.get_json, "/positions")
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else "unknown"
                await ctx.send(f"`/positions` returned HTTP {status}: {_safe_error(exc)}")
                return
            except Exception as exc:
                await ctx.send(f"Could not reach `/positions`: {_safe_error(exc)}")
                return

        for embed_payload in build_positions_embeds(
            payload,
            source_url=self.config.webhook_base_url,
        ):
            await ctx.send(embed=self._discord_embed(embed_payload))

    async def _cmd_health(self, ctx) -> None:
        """Webhook server health check. Slash response is ephemeral."""
        async with ctx.typing():
            try:
                payload = await asyncio.to_thread(self.webhooks.get_json, "/health")
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else "unknown"
                await ctx.send(f"`/health` returned HTTP {status}: {_safe_error(exc)}", ephemeral=True)
                return
            except Exception as exc:
                await ctx.send(f"Could not reach `/health`: {_safe_error(exc)}", ephemeral=True)
                return
            LOG.info(payload)
            await ctx.send(f"{payload}", ephemeral=True)

    async def _cmd_report(
        self,
        ctx,
        *,
        period: str = "today",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        symbol: Optional[str] = None,
        strategy: Optional[str] = None,
        channel: Optional[str] = None,
    ) -> None:
        """Generate a performance report and reply with it in the channel."""
        if period == "custom" or from_date:
            if not from_date:
                await ctx.send(
                    "`period=custom` needs `from_date` (YYYY-MM-DD).",
                    ephemeral=True,
                )
                return
            window = report_builder.resolve_custom_window(from_date, to_date)
            if window is None:
                await ctx.send(
                    "Could not parse `from_date` / `to_date`. Use YYYY-MM-DD.",
                    ephemeral=True,
                )
                return
        else:
            window = report_builder.resolve_window(period)
            if window is None:
                await ctx.send(f"Unknown period `{period}`.", ephemeral=True)
                return

        async with ctx.typing():
            try:
                trades_payload = await asyncio.to_thread(
                    self.webhooks.get_json,
                    "/trades",
                    {"limit": 1000, "since": window.start_iso_utc},
                )
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else "unknown"
                await ctx.send(f"`/trades` returned HTTP {status}: {_safe_error(exc)}")
                return
            except Exception as exc:
                await ctx.send(f"Could not reach `/trades`: {_safe_error(exc)}")
                return

            try:
                positions_payload = await asyncio.to_thread(
                    self.webhooks.get_json, "/positions"
                )
            except Exception as exc:
                # Snapshot is best-effort; report still works without it.
                LOG.info("Skipping positions snapshot in report: %s", _safe_error(exc))
                positions_payload = None

            raw_trades = trades_payload.get("trades") if isinstance(trades_payload, dict) else []
            if not isinstance(raw_trades, list):
                raw_trades = []
            # Server-side filter is best-effort (it filters on exit_time, but
            # the column may be empty for orphan rows); re-filter locally to
            # be safe.
            scoped = report_builder.filter_trades_to_window(raw_trades, window)
            if symbol:
                scoped = report_builder.filter_trades_by_symbol(scoped, symbol)
            if strategy:
                scoped = report_builder.filter_trades_by_strategy(scoped, strategy)
            if channel:
                scoped = report_builder.filter_trades_by_channel(scoped, channel)

            parts: list[str] = []
            if symbol:
                parts.append(f"symbol={symbol.upper()}")
            if strategy:
                parts.append(f"strategy={strategy}")
            if channel:
                parts.append(f"channel={channel}")
            filters_label = ("filtered by " + ", ".join(parts)) if parts else ""

            embed = report_builder.build_report_embed(
                window=window,
                trades=scoped,
                positions=positions_payload if isinstance(positions_payload, dict) else None,
                source_url=self.config.webhook_base_url,
                filters_label=filters_label,
            )

        await ctx.send(embed=self._discord_embed(embed))

    def _discord_embed(self, payload: dict[str, Any]):
        embed = self.discord.Embed(
            title=payload.get("title"),
            description=payload.get("description"),
            color=payload.get("color"),
            url=payload.get("url"),
            timestamp=payload.get("timestamp"),
        )
        for field in payload.get("fields", []):
            embed.add_field(
                name=field.get("name", "-"),
                value=field.get("value", "-"),
                inline=bool(field.get("inline", False)),
            )
        footer = payload.get("footer") or {}
        if footer.get("text"):
            embed.set_footer(text=footer["text"])
        return embed


def build_positions_embeds(payload: Any, *, source_url: str = "") -> list[dict[str, Any]]:
    """Return Discord embed payloads for a /positions JSON payload.

    The returned dictionaries are intentionally plain data so formatting can be
    unit-tested without importing discord.py.
    """
    if not isinstance(payload, dict):
        return [_base_embed(
            title="Positions unavailable",
            description="The `/positions` webhook returned an unexpected payload.",
            color=COLOR_LOSS,
            source_url=source_url,
        )]

    positions = _normalise_positions(payload.get("positions"))
    open_positions = payload.get("open_positions", len(positions))
    daily_pnl = _coerce_float(payload.get("daily_pnl"))
    killed = bool(payload.get("killed", False))
    running = bool(payload.get("running", False))

    if killed:
        color = COLOR_LOSS
    elif daily_pnl is not None and daily_pnl < 0:
        color = COLOR_LOSS
    elif positions or (daily_pnl is not None and daily_pnl > 0):
        color = COLOR_GAIN
    else:
        color = COLOR_NEUTRAL

    status_text = "Killed" if killed else ("Running" if running else "Stopped")
    description = (
        f"`{_format_count(open_positions)}` open positions | "
        f"Trading `{status_text}`"
    )

    embed = _base_embed(
        title="Portfolio Positions",
        description=description,
        color=color,
        source_url=source_url,
    )

    _add_field(embed, "Account Value", _format_money(payload.get("balance")), inline=True)
    _add_field(embed, "Daily P&L", _format_pnl(daily_pnl), inline=True)
    _add_field(embed, "Session", _format_session(payload), inline=True)

    if positions:
        for pos in positions[:MAX_POSITION_FIELDS]:
            _add_field(
                embed,
                _position_title(pos),
                _position_body(pos),
                inline=False,
                name_limit=POSITION_FIELD_NAME_LIMIT,
                value_limit=POSITION_FIELD_VALUE_LIMIT,
            )
        hidden = len(positions) - MAX_POSITION_FIELDS
        if hidden > 0:
            _add_field(
                embed,
                "More positions",
                f"{hidden} additional position(s) are open. "
                "Use the webhook directly for the full JSON payload.",
                inline=False,
            )
    else:
        _add_field(
            embed,
            "Open Positions",
            "No open positions right now.",
            inline=False,
        )

    embed["footer"] = {"text": _footer_text(source_url, len(positions))}
    return [embed]


def _base_embed(
    *,
    title: str,
    description: str,
    color: int,
    source_url: str = "",
) -> dict[str, Any]:
    embed: dict[str, Any] = {
        "title": _truncate(title, 256),
        "description": _truncate(description, EMBED_DESCRIPTION_LIMIT),
        "color": color,
        "timestamp": datetime.now(timezone.utc),
        "fields": [],
    }
    if source_url.startswith(("http://", "https://")):
        embed["url"] = source_url.rstrip("/") + "/positions"
    return embed


def _add_field(
    embed: dict[str, Any],
    name: str,
    value: str,
    *,
    inline: bool = False,
    name_limit: int = EMBED_FIELD_NAME_LIMIT,
    value_limit: int = EMBED_FIELD_VALUE_LIMIT,
) -> None:
    if len(embed["fields"]) >= EMBED_FIELD_LIMIT:
        return
    embed["fields"].append({
        "name": _truncate(name, min(name_limit, EMBED_FIELD_NAME_LIMIT)),
        "value": _truncate(value or "-", min(value_limit, EMBED_FIELD_VALUE_LIMIT)),
        "inline": inline,
    })


def _normalise_positions(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        values = raw.values()
    elif isinstance(raw, list):
        values = raw
    else:
        values = []
    return [p for p in values if isinstance(p, dict)]


def _position_title(pos: dict[str, Any]) -> str:
    ticker = str(pos.get("ticker") or pos.get("symbol") or "?").upper()
    direction = str(pos.get("direction") or pos.get("side") or "?").upper()
    strike = _format_number(pos.get("strike"))
    pos_id = str(pos.get("pos_id") or pos.get("id") or "").strip()
    suffix = f" | {pos_id}" if pos_id else ""
    return f"{ticker} {direction} {strike}{suffix}"


def _position_body(pos: dict[str, Any]) -> str:
    contracts = _format_count(pos.get("contracts", pos.get("quantity", "-")))
    status = str(pos.get("status") or "open").replace("_", " ").title()
    phase = str(pos.get("phase") or "").replace("_", " ").title()

    entry = _coerce_float(pos.get("entry_premium") or pos.get("entry_price"))
    target = _coerce_float(pos.get("target"))
    underlying_entry = _coerce_float(pos.get("underlying_at_entry"))
    pnl = _coerce_float(pos.get("pnl", pos.get("unrealized_pnl")))

    lines = [
        f"Contracts `{contracts}` | Status `{status}`",
        f"Entry `{_format_money(entry)}` -> Target `{_format_money(target)}`"
        f"{_format_target_move(underlying_entry, target)}",
    ]

    peak = _coerce_float(pos.get("peak_premium"))
    current_stop = _coerce_float(pos.get("current_stop"))
    if peak is not None or current_stop is not None:
        lines.append(
            f"Peak `{_format_money(peak)}` | Stop `{_format_money(current_stop)}`"
        )

    if phase:
        lines.append(f"Phase `{phase}`")

    underlying = _coerce_float(pos.get("underlying_price"))
    if underlying is not None or underlying_entry is not None:
        lines.append(
            "Underlying "
            f"`{_format_money(underlying)}`"
            f" from `{_format_money(underlying_entry)}`"
        )

    if pnl is not None:
        lines.append(f"P&L `{_format_pnl(pnl)}`")

    return "\n".join(lines)


def _format_session(payload: dict[str, Any]) -> str:
    pieces = []
    if "total_trades" in payload:
        pieces.append(f"Trades `{_format_count(payload.get('total_trades'))}`")
    wins = payload.get("daily_wins")
    losses = payload.get("daily_losses")
    if wins is not None or losses is not None:
        pieces.append(f"W/L `{_format_count(wins)}/{_format_count(losses)}`")
    if "vix" in payload:
        pieces.append(f"VIX `{_format_number(payload.get('vix'))}`")
    return "\n".join(pieces) or "-"


def _format_target_move(basis: float | None, target: float | None) -> str:
    if basis is None or target is None or basis == 0:
        return ""
    pct = ((target - basis) / abs(basis)) * 100
    return f" ({_format_signed_percent(pct)})"


def _format_pnl(value: float | None) -> str:
    if value is None:
        return "-"
    label = "Gain" if value > 0 else ("Loss" if value < 0 else "Flat")
    return f"{label} {_format_money(value, signed=True)}"


def _format_money(value: Any, *, signed: bool = False) -> str:
    num = _coerce_float(value)
    if num is None:
        return "-"
    sign = "-" if num < 0 else ("+" if signed and num > 0 else "")
    return f"{sign}${abs(num):,.2f}"


def _format_number(value: Any) -> str:
    num = _coerce_float(value)
    if num is None:
        text = str(value or "-").strip()
        return text or "-"
    if num.is_integer():
        return str(int(num))
    return f"{num:,.2f}".rstrip("0").rstrip(".")


def _format_signed_percent(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def _format_count(value: Any) -> str:
    try:
        return str(int(value))
    except (TypeError, ValueError):
        text = str(value or "-").strip()
        return text or "-"


def _footer_text(source_url: str, shown_positions: int) -> str:
    source = source_url or "configured webhook"
    return f"Updated from {source.rstrip('/')}/positions | {shown_positions} position(s)"


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truncate(value: str, limit: int) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def _safe_error(error: BaseException) -> str:
    text = str(error).strip()
    if not text:
        text = error.__class__.__name__
    return text[:300]


def _load_discord():
    try:
        import discord
        from discord import app_commands
        from discord.ext import commands
    except ImportError as exc:  # pragma: no cover - exercised at runtime
        raise SystemExit(
            "discord.py is required to run the mod bot. Install with: uv sync"
        ) from exc
    return discord, commands, app_commands


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("MOD_BOT_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = ModBotConfig.from_env()
    DiscordModBot(config).run()
