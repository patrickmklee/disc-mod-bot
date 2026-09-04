"""Read the alert-grading ledger produced by pytrade-bot.

The ledger lives outside both repos (default `~/pytrade-signal-grades`) and
is the contract between them: pytrade-bot owns every number and the summary
layout, this module reads the files verbatim and shapes the pieces this bot
needs to deliver. Nothing here imports discord.py -- `bot.py` converts the
embed dicts.

Layout, per day:

    days/<YYYY-MM-DD>/
      discord.json   {"username", "embeds": [...]}, already post-shaped
      grades.json    one record per alert
      tape.png       composite chart referenced as attachment://tape.png
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

# Discord message budgets (see docs/handoffs/2026-09-04-alert-grades-report.md).
EMBEDS_PER_MESSAGE = 10
EMBED_CHAR_BUDGET = 6000

MESSAGE_CHAR_LIMIT = 2000

TAPE_FILENAME = "tape.png"

_FENCE_OPEN = "```json\n"
_FENCE_CLOSE = "\n```"

COLOR_RIGHT = 0x00C805
COLOR_WRONG = 0xFF5000
COLOR_FLAT = 0x59636E

# Mirrors GLYPH in pytrade-bot's discord_report.py -- same vocabulary.
_ACCURACY_MARK = {"right": "✅", "wrong": "❌", "flat": "➖", "unknown": "❔"}
_ACCURACY_COLOR = {"right": COLOR_RIGHT, "wrong": COLOR_WRONG}


class DayNotFound(LookupError):
	"""Raised when a requested ledger day has no directory."""

	def __init__(self, date: str, available: list[str]):
		super().__init__(f"No ledger day {date!r}")
		self.date = date
		self.available = available


@dataclass(frozen=True)
class DayReport:
	date: str
	directory: Path
	payload: dict[str, Any]
	records: list[dict[str, Any]]
	tape_path: Path | None

	@property
	def embeds(self) -> list[dict[str, Any]]:
		embeds = self.payload.get("embeds")
		return embeds if isinstance(embeds, list) else []

	@property
	def username(self) -> str:
		return str(self.payload.get("username") or "")


def days_dir(root: str | Path) -> Path:
	return Path(root).expanduser() / "days"


def list_days(root: str | Path) -> list[str]:
	"""Available ledger dates, newest first."""
	base = days_dir(root)
	if not base.is_dir():
		return []
	return sorted(
		(d.name for d in base.iterdir() if (d / "grades.json").is_file()),
		reverse=True,
	)


def load_day(root: str | Path, date: str) -> DayReport:
	directory = days_dir(root) / date
	if not (directory / "grades.json").is_file():
		raise DayNotFound(date, list_days(root))
	records = json.loads((directory / "grades.json").read_text())
	payload_path = directory / "discord.json"
	payload = json.loads(payload_path.read_text()) if payload_path.is_file() else {"embeds": []}
	tape = directory / TAPE_FILENAME
	return DayReport(
		date=date,
		directory=directory,
		payload=payload,
		records=records if isinstance(records, list) else [],
		tape_path=tape if tape.is_file() else None,
	)


# ----------------------------------------------------------------------
# Views over the records
# ----------------------------------------------------------------------


def alert_options(records: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
	"""Select-menu options, one per alert in time order."""
	options = []
	for record in sorted(records, key=lambda r: str(r.get("alert_et") or "")):
		verdict = record.get("verdict") or {}
		accuracy = str(verdict.get("accuracy") or "")
		options.append({
			"label": _truncate(f"{_mark(accuracy)} {_headline(record)}", 100),
			"description": _truncate(
				f"{record.get('lane', '?')}/{record.get('source', '?')}"
				f" · {record.get('alert_et', '?')} ET"
				f" · {accuracy}",
				100,
			),
			"value": _truncate(str(record.get("key") or _headline(record)), 100),
		})
	return options


def find_record(records: Iterable[dict[str, Any]], key: str) -> dict[str, Any] | None:
	for record in records:
		if str(record.get("key")) == key:
			return record
	return None


def alert_card(record: dict[str, Any]) -> dict[str, Any]:
	"""Embed dict for one alert. Renders whichever blocks the record has --
	verdict v1 and v2 differ, and contract / realizable are often status-only
	stubs when no option bars or quotes were available.
	"""
	verdict = record.get("verdict") or {}
	accuracy = str(verdict.get("accuracy") or "")
	embed: dict[str, Any] = {
		"title": _truncate(f"{_mark(accuracy)} {_headline(record)}", 256),
		"description": _describe(record),
		"color": _ACCURACY_COLOR.get(accuracy, COLOR_FLAT),
		"fields": [],
	}
	_add(embed, "Verdict", _verdict_lines(verdict))
	_add(embed, "Direction", _direction_lines(record.get("direction_touch") or {}))
	_add(embed, "Payoff & risk", _payoff_risk_lines(record.get("payoff_risk") or {}))
	_add(embed, "Erraticness", _erraticness_lines(record.get("erraticness") or {}))
	_add(embed, "Underlying", _underlying_lines(record.get("underlying") or {}))
	_add(embed, "Contract", _contract_lines(record.get("contract") or {}))
	_add(embed, "Realizable", _realizable_lines(record.get("realizable") or {}))
	_add(embed, "Clues", _kv_lines(record.get("clues") or {}))
	embed["footer"] = {"text": _truncate(str(record.get("key") or ""), 2048)}
	return embed


def raw_blocks(record: dict[str, Any]) -> list[str]:
	"""Every ledger field for one alert, as Discord code blocks.

	A full record runs past the 2000-character message limit once the
	`what_if` exits are included, so this splits on line boundaries and
	returns one block per message rather than truncating fields away.
	"""
	body = json.dumps(record, indent=1, sort_keys=True, default=str)
	budget = MESSAGE_CHAR_LIMIT - len(_FENCE_OPEN) - len(_FENCE_CLOSE)
	blocks, current = [], ""
	for line in body.splitlines():
		line = _truncate(line, budget)
		if current and len(current) + 1 + len(line) > budget:
			blocks.append(_FENCE_OPEN + current + _FENCE_CLOSE)
			current = ""
		current = f"{current}\n{line}" if current else line
	blocks.append(_FENCE_OPEN + current + _FENCE_CLOSE)
	return blocks


# ----------------------------------------------------------------------
# Message budget
# ----------------------------------------------------------------------


def payload_chars(embeds: Iterable[dict[str, Any]]) -> int:
	"""Characters Discord counts against the 6000-per-message budget."""
	total = 0
	for embed in embeds:
		for key in ("title", "description"):
			total += len(str(embed.get(key) or ""))
		total += len(str((embed.get("footer") or {}).get("text") or ""))
		total += len(str((embed.get("author") or {}).get("name") or ""))
		for field in embed.get("fields") or []:
			total += len(str(field.get("name") or "")) + len(str(field.get("value") or ""))
	return total


def split_for_post(embeds: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
	"""Split embeds into messages that fit the per-message embed and char caps.

	An embed larger than the whole budget still goes out alone -- Discord
	rejects it, and silently dropping it would hide a grader bug.
	"""
	messages: list[list[dict[str, Any]]] = []
	current: list[dict[str, Any]] = []
	for embed in embeds:
		size = payload_chars([embed])
		if current and (
			len(current) >= EMBEDS_PER_MESSAGE
			or payload_chars(current) + size > EMBED_CHAR_BUDGET
		):
			messages.append(current)
			current = []
		current.append(embed)
	if current:
		messages.append(current)
	return messages


# ----------------------------------------------------------------------
# Formatting helpers
# ----------------------------------------------------------------------


def _headline(record: dict[str, Any]) -> str:
	bits = [str(record.get("ticker", "?")), str(record.get("direction", "?"))]
	if record.get("strike") is not None:
		bits.append(_num(record["strike"]))
	return " ".join(bits) + f" — {record.get('alert_et', '?')} ET"


def _describe(record: dict[str, Any]) -> str:
	bits = [f"`{record.get('lane', '?')}/{record.get('source', '?')}`"]
	if record.get("occ"):
		bits.append(f"`{record['occ']}`")
	levels = [
		f"{name} {_num(record[key])}"
		for name, key in (("ref", "ref_price"), ("target", "target"), ("stop", "stop"))
		if record.get(key) is not None
	]
	head = " · ".join(bits)
	return head + "\n" + " · ".join(levels) if levels else head


def _verdict_lines(verdict: dict[str, Any]) -> list[str]:
	"""Generic on purpose: v1 and v2 differ, and v2 may grow again. `version`
	is bookkeeping, not a verdict."""
	return [
		f"{_label(k)}: **{_word(v)}**"
		for k, v in verdict.items()
		if k != "version" and v not in (None, "")
	]


def _direction_lines(block: dict[str, Any]) -> list[str]:
	"""verdict v2: first touch of a noise-scaled band, not a 30-min snapshot.
	The band is recorded per alert so the call can be re-cut from the ledger.
	"""
	if not block:
		return []
	if block.get("reason") and block.get("first_touch_min") is None:
		return [f"**{_word(block.get('verdict'))}** — _{block['reason']}_"]
	touch = block.get("first_touch_min")
	adverse = block.get("adverse_touch_min")
	lines = [
		f"**{_word(block.get('verdict'))}**"
		+ (f" · first touch {_num(touch)}m" if touch is not None else " · no touch")
		+ (f" · adverse touch {_num(adverse)}m" if adverse is not None else "")
	]
	if block.get("band_pct") is not None:
		lines.append(
			f"band ±{_num(block['band_pct'])}%"
			f" = {_num(block.get('band_sigmas'))}σ × {_num(block.get('rs_per_min_pct'))}%/min"
			f" over {_num(block.get('band_bars'))} bars, {_num(block.get('horizon_min'))}m horizon"
		)
	return lines


def _payoff_risk_lines(block: dict[str, Any]) -> list[str]:
	if not block:
		return []
	realizable = block.get("payoff_realizable")
	lines = [
		f"**{_word(block.get('payoff'))}**"
		+ (
			""
			if realizable is None
			else f" · {'realizable' if realizable else 'not realizable'}"
			f" (stop {_num(block.get('realizable_stop_pct'))}%)"
		)
	]
	if block.get("dd_before_payoff_pct") is not None:
		lines.append(
			f"{_word(block.get('risk'))} drawdown {_pct(block['dd_before_payoff_pct'])} before payoff"
			f" · trough before peak {_pct(block.get('trough_before_peak_pct'))}"
		)
	stops = block.get("stops_surviving_to_peak")
	if stops is not None:
		lines.append("stops surviving to peak: " + (", ".join(stops) if stops else "none"))
	return lines


def _erraticness_lines(block: dict[str, Any]) -> list[str]:
	if not block:
		return []
	if block.get("status") != "ok":
		return _status_only(block)
	return [
		f"Kaufman ER {_num(block.get('kaufman_er_to_peak'))} to peak"
		f" · {_num(block.get('kaufman_er_session'))} session",
		f"range {_num(block.get('rs_range_vol_per_min_pct'))}%/min"
		f" · jumps {_num(block.get('jumps_n'))}"
		f" ({_num(block.get('jumps_before_peak_n'))} before peak)"
		f" · wick-only hits {_num(block.get('wick_only_hits'))}"
		f" at -{_num(block.get('ref_stop_pct'))}%",
		f"suddenness {_num(block.get('suddenness_best5_over_peak'))}"
		f" · {_num(block.get('velocity_pct_per_min_to_peak'))}%/min to peak"
		f" · peak at {_num(block.get('peak_fraction_of_session'))} of session",
	]


def _underlying_lines(block: dict[str, Any]) -> list[str]:
	if not _has_numbers(block):
		return _status_only(block)
	lines = []
	moves = " · ".join(
		f"{window}m {_pct(block.get(f'und_{window}m_signed_pct'))}"
		for window in (5, 15, 30, 60)
		if block.get(f"und_{window}m_signed_pct") is not None
	)
	if moves:
		lines.append(f"underlying {moves}")
	lines.append(
		f"EOD {_pct(block.get('und_eod_signed_pct'))}"
		f" · MFE {_pct(block.get('und_mfe_pct'))}"
		f" · MAE {_pct(block.get('und_mae_pct'))}"
	)
	if block.get("target_hit_min") is not None:
		lines.append(f"target hit in {_num(block['target_hit_min'])}m")
	if block.get("stop_hit_min") is not None:
		lines.append(f"stop hit in {_num(block['stop_hit_min'])}m")
	return lines


def _contract_lines(block: dict[str, Any]) -> list[str]:
	if not _has_numbers(block):
		return _status_only(block)
	lines = [
		f"baseline {_num(block.get('baseline'))}"
		+ (
			f" · entry lag {_num(block['entry_lag_min'])}m"
			if block.get("entry_lag_min") is not None
			else ""
		),
		f"peak {_pct(block.get('peak_pct'))} @ {_num(block.get('peak_min'))}m"
		f" · trough {_pct(block.get('trough_pct'))}"
		f" · EOD {_pct(block.get('eod_pct'))}",
	]
	if block.get("t_plus10_min") is not None:
		lines.append(
			f"+10% at {_num(block['t_plus10_min'])}m"
			f" (drawdown first {_pct(block.get('dd_before_plus10_pct'))})"
		)
	if block.get("best_exit_template"):
		lines.append(
			f"best exit **{block['best_exit_template']}** {_pct(block.get('best_exit_pct'))}"
		)
	return lines


def _realizable_lines(block: dict[str, Any]) -> list[str]:
	if not _has_numbers(block):
		return _status_only(block)
	lines = [
		f"entry bid/ask {_num(block.get('entry_bid'))}/{_num(block.get('entry_ask'))}"
		f" · spread {_num(block.get('spread_pct_at_entry'))}%"
	]
	if block.get("entry_lag_s") is not None:
		lines.append(f"quote lag {_num(block['entry_lag_s'])}s")
	lines.append(
		f"real peak {_pct(block.get('real_peak_pct'))}"
		f" · real EOD {_pct(block.get('real_eod_pct'))}"
	)
	return lines


def _kv_lines(block: dict[str, Any]) -> list[str]:
	"""Generic renderer for blocks this bot has no data for yet (verdict v2's
	`erraticness` and `clues`). Shows them rather than dropping them.
	"""
	return [
		f"{_label(k)}: {_num(v) if isinstance(v, (int, float)) else v}"
		for k, v in block.items()
		if v not in (None, "", {}, [])
	]


def _has_numbers(block: dict[str, Any]) -> bool:
	return any(k != "status" for k in block)


def _status_only(block: dict[str, Any]) -> list[str]:
	status = block.get("status")
	return [f"_{status}_"] if status else []


def _add(embed: dict[str, Any], name: str, lines: list[str]) -> None:
	if not lines:
		return
	embed["fields"].append({
		"name": name,
		"value": _truncate("\n".join(lines), 1024),
		"inline": False,
	})


def _mark(accuracy: str) -> str:
	return _ACCURACY_MARK.get(accuracy, "❔")


def _label(key: str) -> str:
	return str(key).replace("_", " ")


def _word(value: Any) -> str:
	if isinstance(value, bool):
		return "yes" if value else "no"
	return str(value).replace("_", " ") if value not in (None, "") else "unknown"


def _pct(value: Any) -> str:
	if not isinstance(value, (int, float)):
		return "-"
	return f"{value:+.1f}%"


def _num(value: Any) -> str:
	if isinstance(value, bool) or not isinstance(value, (int, float)):
		return "-" if value in (None, "") else str(value)
	if float(value).is_integer():
		return str(int(value))
	return f"{value:,.2f}".rstrip("0").rstrip(".")


def _truncate(text: str, limit: int) -> str:
	text = str(text)
	return text if len(text) <= limit else text[: limit - 3] + "..."
