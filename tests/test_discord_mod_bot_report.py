"""Unit tests for the mod-bot performance report builder.

These exercise pure functions in `discord_mod_bot.report` (no discord.py /
no I/O), plus the embed-shape converter in `discord_mod_bot.bot`. Time-
sensitive tests pass an explicit `now=` so they don't drift with the
wall clock.
"""

from datetime import datetime, timedelta, timezone

from discord_mod_bot.bot import _embed_to_webhook_dict, _SPECS
from discord_mod_bot.commands import parse_args
from discord_mod_bot.report import (
	COLOR_GAIN,
	COLOR_LOSS,
	COLOR_NEUTRAL,
	MAX_TICKER_ROWS,
	MAX_TRADE_ROWS,
	build_report_embed,
	filter_trades_to_window,
	resolve_window,
)


# Mid-week anchor: Wednesday 2026-05-20 19:30 ET == 2026-05-20 23:30 UTC.
# Picked so "today", "week" and "month" windows are all non-trivial and
# the week boundary (Monday 2026-05-18) sits in the same month.
NOW_UTC = datetime(2026, 5, 20, 23, 30, tzinfo=timezone.utc)


def _et_iso(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> str:
	"""Return a UTC ISO string for the given ET wall-clock time.

	May is EDT (UTC-4), so 12:00 ET == 16:00 UTC. We don't depend on
	zoneinfo here so the test is stable even when tzdata is absent.
	`timedelta` handles day rollover when the ET-hour is in the evening.
	"""
	dt = datetime(year, month, day, 0, minute, tzinfo=timezone.utc) + timedelta(hours=hour + 4)
	return dt.isoformat()


# ---------------------------------------------------------------------------
# resolve_window
# ---------------------------------------------------------------------------

def test_resolve_window_today_starts_at_midnight_et():
	window = resolve_window("today", now=NOW_UTC)

	assert window is not None
	assert window.period == "today"
	assert window.label == "2026-05-20"
	# Midnight ET = 04:00 UTC during EDT
	assert window.start_iso_utc == "2026-05-20T04:00:00+00:00"
	assert window.end_et.astimezone(timezone.utc) == NOW_UTC


def test_resolve_window_today_handles_aliases():
	for alias in ("today", "TODAY", "  day  ", "daily"):
		window = resolve_window(alias, now=NOW_UTC)
		assert window is not None
		assert window.period == "today"


def test_resolve_window_week_anchors_on_monday_et():
	# NOW_UTC is Wednesday 2026-05-20 ET; Monday is 2026-05-18.
	window = resolve_window("week", now=NOW_UTC)

	assert window is not None
	assert window.period == "week"
	assert window.label == "week of 2026-05-18"
	assert window.start_iso_utc == "2026-05-18T04:00:00+00:00"


def test_resolve_window_week_on_monday_starts_today():
	# Monday at 14:00 UTC == 10:00 ET, well into the trading day.
	monday = datetime(2026, 5, 18, 14, tzinfo=timezone.utc)
	window = resolve_window("week", now=monday)

	assert window is not None
	assert window.label == "week of 2026-05-18"
	assert window.start_iso_utc == "2026-05-18T04:00:00+00:00"


def test_resolve_window_month_anchors_first_of_month_et():
	window = resolve_window("month", now=NOW_UTC)

	assert window is not None
	assert window.period == "month"
	assert window.label == "2026-05 (MTD)"
	assert window.start_iso_utc == "2026-05-01T04:00:00+00:00"


def test_resolve_window_unknown_period_returns_none():
	assert resolve_window("quarter", now=NOW_UTC) is None
	assert resolve_window("ytd", now=NOW_UTC) is None
	assert resolve_window("garbage", now=NOW_UTC) is None


def test_resolve_window_empty_period_defaults_to_today():
	# parse_args canonicalises empty input to the spec default, but the
	# builder itself should also tolerate it.
	window = resolve_window("", now=NOW_UTC)
	assert window is not None
	assert window.period == "today"


# ---------------------------------------------------------------------------
# filter_trades_to_window
# ---------------------------------------------------------------------------

def test_filter_trades_to_window_keeps_in_window_drops_outside():
	window = resolve_window("today", now=NOW_UTC)
	trades = [
		{"id": 1, "exit_time": _et_iso(2026, 5, 20, 9, 30)},   # today 9:30 ET
		{"id": 2, "exit_time": _et_iso(2026, 5, 20, 18, 0)},   # today 18:00 ET
		{"id": 3, "exit_time": _et_iso(2026, 5, 19, 15, 0)},   # yesterday
		{"id": 4, "exit_time": _et_iso(2026, 5, 21, 9, 30)},   # tomorrow
	]

	kept = filter_trades_to_window(trades, window)

	assert sorted(t["id"] for t in kept) == [1, 2]


def test_filter_trades_to_window_skips_rows_without_exit_time():
	window = resolve_window("today", now=NOW_UTC)
	trades = [
		{"id": 1, "exit_time": None},
		{"id": 2, "exit_time": ""},
		{"id": 3},  # key missing entirely
		{"id": 4, "exit_time": "not-a-timestamp"},
		{"id": 5, "exit_time": _et_iso(2026, 5, 20, 12)},
	]

	kept = filter_trades_to_window(trades, window)

	assert [t["id"] for t in kept] == [5]


def test_filter_trades_to_window_treats_naive_timestamps_as_utc():
	window = resolve_window("today", now=NOW_UTC)
	# 12:00 ET == 16:00 UTC; naive form should still land in today's window.
	trades = [{"id": 1, "exit_time": "2026-05-20 16:00:00"}]

	kept = filter_trades_to_window(trades, window)

	assert [t["id"] for t in kept] == [1]


def test_filter_trades_to_window_week_keeps_full_week():
	window = resolve_window("week", now=NOW_UTC)
	trades = [
		{"id": 1, "exit_time": _et_iso(2026, 5, 18, 10)},   # Monday inside
		{"id": 2, "exit_time": _et_iso(2026, 5, 20, 15)},   # Wednesday inside
		{"id": 3, "exit_time": _et_iso(2026, 5, 17, 12)},   # last Sunday -- outside
		# Sunday 22:30 ET == Monday 02:30 UTC, just before the
		# Monday-00:00-ET (== 04:00 UTC) window boundary.
		{"id": 4, "exit_time": _et_iso(2026, 5, 17, 22, 30)},
	]

	kept = filter_trades_to_window(trades, window)

	assert sorted(t["id"] for t in kept) == [1, 2]


# ---------------------------------------------------------------------------
# build_report_embed -- empty / no-data branches
# ---------------------------------------------------------------------------

def test_build_report_embed_empty_trades_is_neutral():
	window = resolve_window("today", now=NOW_UTC)

	embed = build_report_embed(window=window, trades=[])

	assert embed["title"] == "Daily Performance Report"
	assert embed["color"] == COLOR_NEUTRAL
	assert "No closed trades" in embed["description"]
	assert "**2026-05-20**" in embed["description"]
	assert embed["fields"] == []  # no stats block, no positions, no per-ticker
	assert embed["footer"]["text"].startswith("0 trade(s) in 2026-05-20")


def test_build_report_embed_includes_positions_snapshot_even_when_empty():
	window = resolve_window("today", now=NOW_UTC)

	embed = build_report_embed(
		window=window,
		trades=[],
		positions={"balance": 5000.0, "daily_pnl": 0.0, "open_positions": 2},
	)

	by_name = {f["name"]: f["value"] for f in embed["fields"]}
	assert by_name["Account Value"] == "$5,000.00"
	assert by_name["Daily P&L (today)"] == "$0.00"
	assert by_name["Open Positions"] == "2"


# ---------------------------------------------------------------------------
# build_report_embed -- populated cases
# ---------------------------------------------------------------------------

def _winning_trade(**kw):
	base = {
		"ticker": "AMD",
		"direction": "call",
		"strike": 175.0,
		"pnl": 100.0,
		"exit_reason": "target_hit",
		"exit_time": _et_iso(2026, 5, 20, 10, 30),
	}
	base.update(kw)
	return base


def test_build_report_embed_gain_color_and_aggregates():
	window = resolve_window("today", now=NOW_UTC)
	trades = [
		_winning_trade(ticker="AMD", pnl=200.0),
		_winning_trade(ticker="AMD", pnl=-50.0, exit_reason="velocity_trail"),
		_winning_trade(ticker="NVDA", pnl=75.0),
	]

	embed = build_report_embed(window=window, trades=trades)
	by_name = {f["name"]: f["value"] for f in embed["fields"]}

	assert embed["color"] == COLOR_GAIN
	assert by_name["Trades"] == "3"
	assert by_name["Win / Loss"] == "2 / 1"
	# 2 wins / 3 trades = 66.7%
	assert by_name["Win Rate"] == "66.7%"
	assert by_name["Net P&L"] == "+$225.00"
	# Avg trade = 225 / 3 = 75
	assert by_name["Avg Trade"] == "+$75.00"


def test_build_report_embed_loss_color():
	window = resolve_window("today", now=NOW_UTC)
	trades = [
		_winning_trade(pnl=-100.0, exit_reason="velocity_trail"),
		_winning_trade(pnl=20.0),
	]

	embed = build_report_embed(window=window, trades=trades)

	assert embed["color"] == COLOR_LOSS
	by_name = {f["name"]: f["value"] for f in embed["fields"]}
	assert by_name["Net P&L"] == "-$80.00"


def test_build_report_embed_breakeven_color_is_neutral():
	window = resolve_window("today", now=NOW_UTC)
	trades = [
		_winning_trade(pnl=50.0),
		_winning_trade(pnl=-50.0, exit_reason="velocity_trail"),
		_winning_trade(pnl=0.0, exit_reason="breakeven_stop"),
	]

	embed = build_report_embed(window=window, trades=trades)
	by_name = {f["name"]: f["value"] for f in embed["fields"]}

	assert embed["color"] == COLOR_NEUTRAL
	assert by_name["Win / Loss"] == "1 / 1 / 1 BE"
	assert by_name["Net P&L"] == "$0.00"


def test_build_report_embed_best_and_worst_picked_correctly():
	window = resolve_window("today", now=NOW_UTC)
	trades = [
		_winning_trade(ticker="AMD", pnl=10.0),
		_winning_trade(ticker="NVDA", pnl=500.0),
		_winning_trade(ticker="MSFT", pnl=-300.0, exit_reason="velocity_trail"),
		_winning_trade(ticker="TSLA", pnl=-20.0, exit_reason="velocity_trail"),
	]

	embed = build_report_embed(window=window, trades=trades)
	by_name = {f["name"]: f["value"] for f in embed["fields"]}

	assert "NVDA" in by_name["Best Winner"]
	assert "+$500.00" in by_name["Best Winner"]
	assert "MSFT" in by_name["Worst Loser"]
	assert "-$300.00" in by_name["Worst Loser"]


def test_build_report_embed_per_ticker_rollup_sorted_by_abs_pnl():
	window = resolve_window("today", now=NOW_UTC)
	trades = [
		_winning_trade(ticker="AMD", pnl=10.0),
		_winning_trade(ticker="AMD", pnl=15.0),
		_winning_trade(ticker="NVDA", pnl=-200.0, exit_reason="velocity_trail"),
		_winning_trade(ticker="MSFT", pnl=50.0),
		_winning_trade(ticker="MSFT", pnl=-10.0, exit_reason="velocity_trail"),
	]

	embed = build_report_embed(window=window, trades=trades)
	by_name = {f["name"]: f["value"] for f in embed["fields"]}

	lines = by_name["By Ticker"].splitlines()
	# Highest |net_pnl| first: NVDA(-200), MSFT(+40), AMD(+25)
	assert "NVDA" in lines[0]
	assert "MSFT" in lines[1]
	assert "AMD" in lines[2]
	# AMD bucket: 2 trades, both wins
	assert "2t" in lines[2] and "W/L 2/0" in lines[2]


def test_build_report_embed_recent_trades_capped_and_newest_first():
	window = resolve_window("week", now=NOW_UTC)
	trades = [
		_winning_trade(
			ticker=f"T{i}",
			pnl=float(i),
			exit_time=_et_iso(2026, 5, 18 + (i % 3), 10 + (i % 5)),
		)
		for i in range(MAX_TRADE_ROWS + 4)
	]

	embed = build_report_embed(window=window, trades=trades)
	by_name = {f["name"]: f["value"] for f in embed["fields"]}

	recent_lines = by_name["Recent Trades"].splitlines()
	assert len(recent_lines) == MAX_TRADE_ROWS
	# Lines start with the bare ISO date prefix; newest first means the
	# first line's timestamp >= the last line's timestamp.
	first_ts = recent_lines[0].split("`")[1]
	last_ts = recent_lines[-1].split("`")[1]
	assert first_ts >= last_ts


def test_build_report_embed_per_ticker_capped():
	window = resolve_window("week", now=NOW_UTC)
	trades = [
		_winning_trade(ticker=f"TKR{i:02d}", pnl=float(i + 1))
		for i in range(MAX_TICKER_ROWS + 5)
	]

	embed = build_report_embed(window=window, trades=trades)
	by_name = {f["name"]: f["value"] for f in embed["fields"]}

	lines = by_name["By Ticker"].splitlines()
	assert len(lines) == MAX_TICKER_ROWS


def test_build_report_embed_source_url_attached():
	window = resolve_window("today", now=NOW_UTC)

	embed = build_report_embed(
		window=window,
		trades=[],
		source_url="http://127.0.0.1:5555",
	)

	assert embed["url"] == "http://127.0.0.1:5555/trades"
	assert "http://127.0.0.1:5555" in embed["footer"]["text"]


# ---------------------------------------------------------------------------
# _embed_to_webhook_dict -- the shape we POST to Discord
# ---------------------------------------------------------------------------

def test_embed_to_webhook_dict_serialises_timestamp():
	window = resolve_window("today", now=NOW_UTC)
	embed = build_report_embed(window=window, trades=[])

	hook = _embed_to_webhook_dict(embed)

	assert isinstance(hook["timestamp"], str)
	assert hook["timestamp"].endswith("Z")
	# Title / color / footer round-trip
	assert hook["title"] == "Daily Performance Report"
	assert hook["color"] == COLOR_NEUTRAL
	assert "footer" in hook


def test_embed_to_webhook_dict_strips_missing_optional_keys():
	minimal_embed = {
		"title": "x",
		"description": "y",
		"color": 0x00C805,
		"fields": [],
	}

	hook = _embed_to_webhook_dict(minimal_embed)

	assert "timestamp" not in hook
	assert "url" not in hook
	assert "footer" not in hook
	# Empty fields list must not be emitted (Discord rejects it).
	assert "fields" not in hook


# ---------------------------------------------------------------------------
# Command-arg parser interaction (relevant to !report flag plumbing)
# ---------------------------------------------------------------------------

def test_parse_args_report_defaults_to_today():
	args = parse_args((), _SPECS["report"])
	assert args.subcommand == "today"
	assert args.positional == ()


def test_parse_args_report_picks_up_here_flag():
	args = parse_args(("week", "--here"), _SPECS["report"])
	assert args.subcommand == "week"
	assert args.flag("here") is True


def test_parse_args_supports_key_value_options():
	args = parse_args(("today", "--ticker=AMD", "--limit:50"), _SPECS["report"])
	assert args.option("ticker") == "AMD"
	assert args.option("limit") == "50"
	assert args.option("missing", default="x") == "x"
