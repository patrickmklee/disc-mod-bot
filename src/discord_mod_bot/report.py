"""Performance report builder for !report.

Pure functions -- no I/O, no discord.py dependency. Takes the JSON
returned from `/trades` plus an optional `/positions` payload and
produces a Discord embed (plain dict) plus the period window used.

Period semantics
----------------
- `today`: trades whose exit_time falls in today's session in
  America/New_York. Trading day is "calendar day in ET" -- aligns with
  how the underlying daily_pnl row is keyed (UTC date), which is close
  enough for the report header. We compute the window in ET so a 6pm
  ET command on the same day still shows the correct date.
- `week`: trades whose exit_time falls in the current calendar week
  starting Monday 00:00 ET, through `now`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

try:
	from zoneinfo import ZoneInfo
	_ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover -- zoneinfo is stdlib on 3.12 but tzdata may be missing
	_ET = timezone(timedelta(hours=-5))  # fallback EST, no DST -- best effort

COLOR_GAIN = 0x00C805
COLOR_LOSS = 0xFF5000
COLOR_NEUTRAL = 0x59636E

EMBED_FIELD_LIMIT = 25
EMBED_FIELD_VALUE_LIMIT = 1024
MAX_TICKER_ROWS = 10
MAX_TRADE_ROWS = 8


@dataclass(frozen=True)
class ReportWindow:
	period: str            # "today" | "week" | "month" ...
	label: str             # human-readable, used in embed title
	start_et: datetime
	end_et: datetime

	@property
	def start_iso_utc(self) -> str:
		"""ISO timestamp suitable for the /trades ?since= parameter."""
		return self.start_et.astimezone(timezone.utc).isoformat()


def resolve_window(period: str, *, now: datetime | None = None) -> ReportWindow | None:
	"""Resolve a period name to a concrete time window.

	Returns None for unknown periods so the caller can render a usage
	message rather than silently producing an empty report.
	"""
	period_norm = (period or "").strip().lower() or "today"
	now_et = (now or datetime.now(timezone.utc)).astimezone(_ET)

	if period_norm in ("today", "day", "daily"):
		start = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
		label = start.strftime("%Y-%m-%d")
		return ReportWindow("today", label, start, now_et)

	if period_norm in ("week", "weekly", "wk"):
		monday = now_et - timedelta(days=now_et.weekday())
		start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
		label = f"week of {start.strftime('%Y-%m-%d')}"
		return ReportWindow("week", label, start, now_et)

	if period_norm in ("month", "monthly", "mo"):
		start = now_et.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
		label = start.strftime("%Y-%m (MTD)")
		return ReportWindow("month", label, start, now_et)

	return None


def filter_trades_to_window(
	trades: list[dict[str, Any]],
	window: ReportWindow,
) -> list[dict[str, Any]]:
	"""Keep trades whose exit_time falls within the window (inclusive)."""
	start_utc = window.start_et.astimezone(timezone.utc)
	end_utc = window.end_et.astimezone(timezone.utc)
	out: list[dict[str, Any]] = []
	for trade in trades:
		exit_dt = _parse_iso(trade.get("exit_time"))
		if exit_dt is None:
			continue
		if start_utc <= exit_dt <= end_utc:
			out.append(trade)
	return out


def filter_trades_by_field(
	trades: list[dict[str, Any]],
	field_name: str,
	expected: str,
) -> list[dict[str, Any]]:
	"""Keep trades whose `field_name` matches `expected` (case-insensitive).

	Empty/None `expected` returns the input unchanged so callers can chain
	filters unconditionally. Trades missing the field are dropped when the
	filter is active -- a requested filter that can't be evaluated is a
	stronger signal than silently keeping everything.
	"""
	target = (expected or "").strip().lower()
	if not target:
		return trades
	out: list[dict[str, Any]] = []
	for trade in trades:
		value = trade.get(field_name)
		if value is None:
			continue
		if str(value).strip().lower() == target:
			out.append(trade)
	return out


def filter_trades_by_symbol(
	trades: list[dict[str, Any]],
	symbol: str,
) -> list[dict[str, Any]]:
	"""Keep trades whose `ticker` matches `symbol` (case-insensitive)."""
	return filter_trades_by_field(trades, "ticker", symbol)


def filter_trades_by_strategy(
	trades: list[dict[str, Any]],
	strategy: str,
) -> list[dict[str, Any]]:
	"""Keep trades whose `strategy` matches `strategy` (case-insensitive)."""
	return filter_trades_by_field(trades, "strategy", strategy)


def filter_trades_by_channel(
	trades: list[dict[str, Any]],
	channel: str,
) -> list[dict[str, Any]]:
	"""Keep trades whose `channel` (signal source) matches (case-insensitive)."""
	return filter_trades_by_field(trades, "channel", channel)


def resolve_custom_window(
	since: str,
	until: str | None = None,
	*,
	now: datetime | None = None,
) -> ReportWindow | None:
	"""Build a window from explicit `--from` / `--to` dates.

	Both accept `YYYY-MM-DD` (interpreted as ET wall-clock; `since` is
	midnight ET, `until` is end-of-day ET) or a full ISO datetime
	(naive datetimes are treated as ET). When `until` is omitted, the
	window extends to `now`.

	Returns None on any parse failure or when end precedes start.
	"""
	start_et = _parse_window_bound(since, end_of_day=False)
	if start_et is None:
		return None

	if until and until.strip():
		end_et = _parse_window_bound(until, end_of_day=True)
		if end_et is None:
			return None
	else:
		end_et = (now or datetime.now(timezone.utc)).astimezone(_ET)

	if end_et < start_et:
		return None

	if start_et.date() == end_et.date():
		label = start_et.strftime("%Y-%m-%d")
	else:
		label = f"{start_et.strftime('%Y-%m-%d')} -> {end_et.strftime('%Y-%m-%d')}"
	return ReportWindow("custom", label, start_et, end_et)


def _parse_window_bound(value: str, *, end_of_day: bool) -> datetime | None:
	"""Parse `YYYY-MM-DD` or ISO datetime as an ET-localised datetime.

	Bare dates are anchored at midnight ET (or 23:59:59.999999 ET when
	`end_of_day` is True). Naive datetimes are localised to ET; aware
	datetimes are converted to ET.
	"""
	text = (value or "").strip()
	if not text:
		return None
	if len(text) == 10 and text[4] == "-" and text[7] == "-":
		try:
			day = datetime.fromisoformat(text)
		except ValueError:
			return None
		day = day.replace(tzinfo=_ET)
		if end_of_day:
			day = day.replace(hour=23, minute=59, second=59, microsecond=999999)
		return day
	normalised = text.replace(" ", "T", 1) if text[10:11] == " " else text
	try:
		dt = datetime.fromisoformat(normalised)
	except ValueError:
		return None
	if dt.tzinfo is None:
		dt = dt.replace(tzinfo=_ET)
	return dt.astimezone(_ET)


def build_report_embed(
	*,
	window: ReportWindow,
	trades: list[dict[str, Any]],
	positions: dict[str, Any] | None = None,
	source_url: str = "",
	filters_label: str = "",
) -> dict[str, Any]:
	"""Render the report as a Discord embed (dict form).

	`filters_label`, when non-empty, is appended to the description (and
	to the empty-trades message) so the reader sees which filters were
	applied -- e.g. `filtered by symbol=AMD, strategy=momentum`.
	"""
	totals = _aggregate(trades)
	filters_suffix = f" -- {filters_label}" if filters_label else ""

	if totals["count"] == 0:
		color = COLOR_NEUTRAL
		description = (
			f"No closed trades for **{window.label}**{filters_suffix} "
			f"(window: {window.start_et.strftime('%Y-%m-%d %H:%M ET')} -> "
			f"{window.end_et.strftime('%Y-%m-%d %H:%M ET')})."
		)
	else:
		if totals["net_pnl"] > 0:
			color = COLOR_GAIN
		elif totals["net_pnl"] < 0:
			color = COLOR_LOSS
		else:
			color = COLOR_NEUTRAL
		description = (
			f"**{window.label}**{filters_suffix} | "
			f"{totals['count']} trade(s) | "
			f"{_format_pnl(totals['net_pnl'])} net | "
			f"WR {totals['win_rate_pct']:.1f}%"
		)

	embed: dict[str, Any] = {
		"title": f"{_period_title(window.period)} Performance Report",
		"description": description,
		"color": color,
		"timestamp": datetime.now(timezone.utc),
		"fields": [],
	}
	if source_url:
		embed["url"] = source_url.rstrip("/") + "/trades"

	# Account snapshot from /positions (optional)
	if positions:
		_append(embed, "Account Value", _money(positions.get("balance")), inline=True)
		_append(embed, "Daily P&L (today)", _format_pnl(positions.get("daily_pnl")), inline=True)
		_append(embed, "Open Positions", str(positions.get("open_positions") or 0), inline=True)

	# Aggregate stats for the requested window
	if totals["count"] > 0:
		_append(embed, "Trades", str(totals["count"]), inline=True)
		_append(
			embed,
			"Win / Loss",
			f"{totals['wins']} / {totals['losses']}"
			+ (f" / {totals['breakevens']} BE" if totals["breakevens"] else ""),
			inline=True,
		)
		_append(embed, "Win Rate", f"{totals['win_rate_pct']:.1f}%", inline=True)
		_append(embed, "Gross P&L", _format_pnl(totals["gross_pnl"]), inline=True)
		_append(embed, "Net P&L", _format_pnl(totals["net_pnl"]), inline=True)
		_append(embed, "Avg Trade", _format_pnl(totals["avg_pnl"]), inline=True)

		_append(
			embed,
			"Best Winner",
			_one_line_trade(totals["best"]) if totals["best"] else "-",
			inline=False,
		)
		_append(
			embed,
			"Worst Loser",
			_one_line_trade(totals["worst"]) if totals["worst"] else "-",
			inline=False,
		)

		# Per-ticker breakdown (top by absolute |net_pnl|)
		per_ticker = totals["per_ticker"]
		if per_ticker:
			rows = sorted(per_ticker.values(), key=lambda r: abs(r["net_pnl"]), reverse=True)
			lines = []
			for row in rows[:MAX_TICKER_ROWS]:
				lines.append(
					f"`{row['ticker']:<6}` {row['count']:>2}t | "
					f"W/L {row['wins']}/{row['losses']} | "
					f"{_format_pnl(row['net_pnl'])}"
				)
			_append(embed, "By Ticker", "\n".join(lines), inline=False)

		# Recent trades (newest first)
		recent = sorted(
			trades,
			key=lambda t: t.get("exit_time") or "",
			reverse=True,
		)[:MAX_TRADE_ROWS]
		if recent:
			lines = [_one_line_trade(t) for t in recent]
			_append(embed, "Recent Trades", "\n".join(lines), inline=False)

	embed["footer"] = {"text": _footer(window, totals["count"], source_url)}
	return embed


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------

def _aggregate(trades: list[dict[str, Any]]) -> dict[str, Any]:
	wins = losses = breakevens = 0
	gross = 0.0
	net = 0.0
	best: dict[str, Any] | None = None
	worst: dict[str, Any] | None = None
	per_ticker: dict[str, dict[str, Any]] = {}

	for trade in trades:
		pnl = _coerce_float(trade.get("pnl")) or 0.0
		gross += pnl
		net += pnl  # we don't have a separate net column; pnl is already net of fees in trades table
		if pnl > 0:
			wins += 1
		elif pnl < 0:
			losses += 1
		else:
			breakevens += 1

		if best is None or pnl > (_coerce_float(best.get("pnl")) or 0.0):
			best = trade
		if worst is None or pnl < (_coerce_float(worst.get("pnl")) or 0.0):
			worst = trade

		ticker = str(trade.get("ticker") or "?").upper()
		bucket = per_ticker.setdefault(
			ticker,
			{"ticker": ticker, "count": 0, "wins": 0, "losses": 0, "net_pnl": 0.0},
		)
		bucket["count"] += 1
		bucket["net_pnl"] += pnl
		if pnl > 0:
			bucket["wins"] += 1
		elif pnl < 0:
			bucket["losses"] += 1

	count = len(trades)
	win_rate = (wins / count * 100) if count else 0.0
	avg = (net / count) if count else 0.0
	return {
		"count": count,
		"wins": wins,
		"losses": losses,
		"breakevens": breakevens,
		"gross_pnl": gross,
		"net_pnl": net,
		"avg_pnl": avg,
		"win_rate_pct": win_rate,
		"best": best,
		"worst": worst,
		"per_ticker": per_ticker,
	}


def _one_line_trade(trade: dict[str, Any] | None) -> str:
	if not trade:
		return "-"
	ticker = str(trade.get("ticker") or "?").upper()
	direction = str(trade.get("direction") or "?").upper()[:1]
	strike = _format_strike(trade.get("strike"))
	pnl = _coerce_float(trade.get("pnl"))
	reason = str(trade.get("exit_reason") or "").replace("_", " ")
	exit_time = (str(trade.get("exit_time") or "")[:16]).replace("T", " ")
	return (
		f"`{exit_time}` {ticker} {direction}{strike} "
		f"{_format_pnl(pnl)}"
		+ (f" ({reason})" if reason else "")
	)


def _format_strike(value: Any) -> str:
	num = _coerce_float(value)
	if num is None:
		return ""
	if num.is_integer():
		return str(int(num))
	return f"{num:g}"


def _format_pnl(value: Any) -> str:
	num = _coerce_float(value)
	if num is None:
		return "-"
	sign = "+" if num > 0 else ("-" if num < 0 else "")
	return f"{sign}${abs(num):,.2f}"


def _money(value: Any) -> str:
	num = _coerce_float(value)
	if num is None:
		return "-"
	sign = "-" if num < 0 else ""
	return f"{sign}${abs(num):,.2f}"


def _coerce_float(value: Any) -> float | None:
	if value in (None, ""):
		return None
	try:
		return float(value)
	except (TypeError, ValueError):
		return None


def _parse_iso(value: Any) -> datetime | None:
	if not value:
		return None
	text = str(value).strip()
	if not text:
		return None
	# Trades table stores either ISO with 'T' or ' ' separator, possibly
	# without timezone. Treat naive timestamps as UTC.
	text = text.replace(" ", "T", 1) if text[10:11] == " " else text
	try:
		dt = datetime.fromisoformat(text)
	except ValueError:
		return None
	if dt.tzinfo is None:
		dt = dt.replace(tzinfo=timezone.utc)
	return dt.astimezone(timezone.utc)


def _append(embed: dict[str, Any], name: str, value: str, *, inline: bool) -> None:
	if len(embed["fields"]) >= EMBED_FIELD_LIMIT:
		return
	text = value or "-"
	if len(text) > EMBED_FIELD_VALUE_LIMIT:
		text = text[: EMBED_FIELD_VALUE_LIMIT - 3] + "..."
	embed["fields"].append({"name": name, "value": text, "inline": inline})


def _period_title(period: str) -> str:
	return {
		"today": "Daily",
		"week": "Weekly",
		"month": "Monthly",
	}.get(period, period.title() or "Performance")


def _footer(window: ReportWindow, trade_count: int, source_url: str) -> str:
	source = source_url or "trading webhook"
	return (
		f"{trade_count} trade(s) in {window.label} | "
		f"source: {source.rstrip('/')}"
	)
