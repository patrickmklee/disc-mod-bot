"""Slash-command autocomplete sources.

Pure (no discord.py import) so the cache and matcher can be unit-tested
against a fake `get_json`. The bot wraps the returned strings in
`app_commands.Choice` at call time.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable


GetJson = Callable[..., Any]
AC_CHOICE_LIMIT = 25  # Discord's per-autocomplete-response cap.


class AutocompleteCache:
	"""Cache of distinct values pulled from `/positions` and `/trades`.

	Each accessor is best-effort: on upstream error it returns the last
	good value (which may be empty). Discord allows ~3 seconds per
	autocomplete call, so the in-memory TTL keeps us well under budget
	even when the trading server is slow.
	"""

	def __init__(
		self,
		get_json: GetJson,
		*,
		ttl_seconds: float = 60.0,
		history_days: int = 30,
		trade_limit: int = 5000,
	):
		self._get_json = get_json
		self._ttl = ttl_seconds
		self._history_days = history_days
		self._trade_limit = trade_limit
		self._cache: dict[str, tuple[float, list[str]]] = {}

	async def symbols(self) -> list[str]:
		return await self._cached("symbol", self._fetch_symbols)

	async def strategies(self) -> list[str]:
		return await self._cached("strategy", lambda: self._fetch_trade_field("strategy"))

	async def channels(self) -> list[str]:
		return await self._cached("channel", lambda: self._fetch_trade_field("channel"))

	async def _cached(self, key: str, sync_fetcher: Callable[[], list[str]]) -> list[str]:
		ts, values = self._cache.get(key, (0.0, []))
		if values and (time.monotonic() - ts) < self._ttl:
			return values
		try:
			fresh = await asyncio.to_thread(sync_fetcher)
		except Exception:
			# Keep the last-good value rather than flicker the UI to empty.
			return values
		self._cache[key] = (time.monotonic(), fresh)
		return fresh

	def _fetch_symbols(self) -> list[str]:
		payload = self._get_json("/positions")
		positions = _normalise_positions(payload)
		seen: set[str] = set()
		for pos in positions:
			ticker = str(pos.get("ticker") or pos.get("symbol") or "").strip().upper()
			if ticker:
				seen.add(ticker)
		return sorted(seen)

	def _fetch_trade_field(self, field: str) -> list[str]:
		since = (datetime.now(timezone.utc) - timedelta(days=self._history_days)).isoformat()
		payload = self._get_json("/trades", {"limit": self._trade_limit, "since": since})
		trades = payload.get("trades") if isinstance(payload, dict) else []
		if not isinstance(trades, list):
			return []
		seen: set[str] = set()
		for trade in trades:
			if not isinstance(trade, dict):
				continue
			value = trade.get(field)
			if value is None:
				continue
			text = str(value).strip()
			if text:
				seen.add(text)
		return sorted(seen)


def match(values: list[str], current: str, *, limit: int = AC_CHOICE_LIMIT) -> list[str]:
	"""Return up to `limit` values matching `current` (case-insensitive).

	Prefix matches come first, then substring matches, preserving the
	original order within each bucket. Empty `current` returns the first
	`limit` values as-is so the user sees suggestions immediately.
	"""
	needle = current.strip().lower()
	if not needle:
		return values[:limit]
	prefix_hits: list[str] = []
	contains_hits: list[str] = []
	for value in values:
		low = value.lower()
		if low.startswith(needle):
			prefix_hits.append(value)
		elif needle in low:
			contains_hits.append(value)
	return (prefix_hits + contains_hits)[:limit]


def _normalise_positions(payload: Any) -> list[dict[str, Any]]:
	if not isinstance(payload, dict):
		return []
	raw = payload.get("positions")
	if isinstance(raw, dict):
		raw = list(raw.values())
	if not isinstance(raw, list):
		return []
	return [p for p in raw if isinstance(p, dict)]
