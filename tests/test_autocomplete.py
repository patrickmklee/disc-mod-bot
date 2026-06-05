"""Unit tests for the slash-autocomplete cache and matcher.

The cache is exercised with a fake `get_json` so the tests stay decoupled
from `requests` and from discord.py.
"""

from __future__ import annotations

import asyncio

from discord_mod_bot.autocomplete import AC_CHOICE_LIMIT, AutocompleteCache, match


def _run(coro):
	return asyncio.run(coro)


# ---------------------------------------------------------------------------
# match()
# ---------------------------------------------------------------------------

def test_match_returns_prefix_hits_before_substring_hits():
	values = ["AMD", "AMC", "NVDA", "PLTR", "PALANTIR"]

	out = match(values, "pa")

	# Both PLTR-related entries match by substring; PALANTIR is the only
	# prefix hit and must come first.
	assert out[0] == "PALANTIR"
	assert "PLTR" not in out  # "pa" not in "pltr"


def test_match_is_case_insensitive():
	values = ["momentum", "Reversal", "BREAKOUT"]
	assert match(values, "mo") == ["momentum"]
	assert match(values, "REV") == ["Reversal"]


def test_match_empty_current_returns_first_n_values():
	values = [f"v{i:02d}" for i in range(40)]
	out = match(values, "")
	assert out == values[:AC_CHOICE_LIMIT]


def test_match_caps_results_at_limit():
	values = [f"AMD{i}" for i in range(40)]
	out = match(values, "amd")
	assert len(out) == AC_CHOICE_LIMIT


# ---------------------------------------------------------------------------
# AutocompleteCache.symbols (from /positions)
# ---------------------------------------------------------------------------

def _make_fake(payloads: dict[str, object]):
	"""Build a fake get_json that returns a different payload per path."""

	def get_json(path, params=None):
		if path not in payloads:
			raise RuntimeError(f"unexpected path: {path}")
		value = payloads[path]
		if isinstance(value, Exception):
			raise value
		return value

	return get_json


def test_symbols_extracts_distinct_tickers_from_positions_list():
	get_json = _make_fake({
		"/positions": {
			"positions": [
				{"ticker": "AMD"},
				{"ticker": "amd"},          # case-folded duplicate
				{"symbol": "NVDA"},         # alt key
				{"ticker": ""},             # ignored
				{"other": "noise"},          # no ticker field
			]
		}
	})
	cache = AutocompleteCache(get_json)

	assert _run(cache.symbols()) == ["AMD", "NVDA"]


def test_symbols_handles_positions_as_dict_keyed_by_id():
	get_json = _make_fake({
		"/positions": {"positions": {"p1": {"ticker": "TSLA"}, "p2": {"ticker": "AMD"}}}
	})
	cache = AutocompleteCache(get_json)

	assert _run(cache.symbols()) == ["AMD", "TSLA"]


def test_symbols_returns_empty_on_upstream_error():
	get_json = _make_fake({"/positions": RuntimeError("boom")})
	cache = AutocompleteCache(get_json)

	assert _run(cache.symbols()) == []


# ---------------------------------------------------------------------------
# Trade-field accessors (strategy / channel)
# ---------------------------------------------------------------------------

def test_strategies_extracts_distinct_values_from_trades():
	get_json = _make_fake({
		"/trades": {
			"trades": [
				{"strategy": "momentum"},
				{"strategy": "momentum"},
				{"strategy": "reversal"},
				{"strategy": None},
				{"strategy": "  breakout  "},  # trimmed
				{},                            # missing field
			]
		}
	})
	cache = AutocompleteCache(get_json)

	assert _run(cache.strategies()) == ["breakout", "momentum", "reversal"]


def test_channels_extracts_distinct_values_from_trades():
	get_json = _make_fake({
		"/trades": {"trades": [{"channel": "alerts-spx"}, {"channel": "alerts-tech"}]}
	})
	cache = AutocompleteCache(get_json)

	assert _run(cache.channels()) == ["alerts-spx", "alerts-tech"]


def test_trade_accessor_returns_empty_when_payload_shape_is_wrong():
	get_json = _make_fake({"/trades": "not-a-dict"})
	cache = AutocompleteCache(get_json)

	assert _run(cache.strategies()) == []


# ---------------------------------------------------------------------------
# TTL behavior
# ---------------------------------------------------------------------------

def test_cached_value_is_reused_within_ttl():
	calls = {"n": 0}

	def get_json(path, params=None):
		calls["n"] += 1
		return {"positions": [{"ticker": "AMD"}]}

	cache = AutocompleteCache(get_json, ttl_seconds=60.0)

	_run(cache.symbols())
	_run(cache.symbols())
	_run(cache.symbols())

	assert calls["n"] == 1


def test_cached_value_refetches_after_ttl_expires():
	calls = {"n": 0}

	def get_json(path, params=None):
		calls["n"] += 1
		return {"positions": [{"ticker": f"T{calls['n']}"}]}

	cache = AutocompleteCache(get_json, ttl_seconds=0.0)

	_run(cache.symbols())
	# ttl=0 means every call should refetch.
	_run(cache.symbols())

	assert calls["n"] == 2


def test_error_after_success_keeps_last_good_value():
	state = {"raise": False}

	def get_json(path, params=None):
		if state["raise"]:
			raise RuntimeError("upstream down")
		return {"positions": [{"ticker": "AMD"}]}

	cache = AutocompleteCache(get_json, ttl_seconds=0.0)

	first = _run(cache.symbols())
	state["raise"] = True
	second = _run(cache.symbols())

	assert first == ["AMD"]
	assert second == ["AMD"]  # fell back to last good, not empty
