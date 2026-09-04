"""Read the alert-grading ledger produced by pytrade-bot.

The ledger lives outside both repos (default `~/pytrade-signal-grades`) and
is the contract between them: pytrade-bot owns every number, every card and
every image; this bot owns delivery. This module lists days and loads a day's
files verbatim -- it never composes or reformats a card. Nothing here imports
discord.py; `bot.py` converts the embed dicts.

Layout, per day:

    days/<YYYY-MM-DD>/
      discord.json   {"username", "embeds": [...]}, already post-shaped
      grades.json    one record per alert
      run.json       the poster's manifest: "png" (filename, or null when no
                     contract had bars, so there is no chart) and
                     "partial_intraday" (true while the session was still
                     open: not final, do not post)
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

MANIFEST_FILENAME = "run.json"


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
	manifest: dict[str, Any]
	tape_path: Path | None

	@property
	def embeds(self) -> list[dict[str, Any]]:
		embeds = self.payload.get("embeds")
		return embeds if isinstance(embeds, list) else []

	@property
	def username(self) -> str:
		return str(self.payload.get("username") or "")

	@property
	def partial_intraday(self) -> bool:
		"""True while the day was graded with its session still open."""
		return bool(self.manifest.get("partial_intraday"))

	@property
	def png_name(self) -> str | None:
		"""The chart the grader says it wrote, or None when it wrote none."""
		png = self.manifest.get("png")
		return str(png) if png else None


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
	payload = _read_json(directory / "discord.json", {"embeds": []})
	manifest = _read_json(directory / MANIFEST_FILENAME, {})
	png = manifest.get("png")
	tape = directory / str(png) if png else None
	return DayReport(
		date=date,
		directory=directory,
		payload=payload,
		records=records if isinstance(records, list) else [],
		manifest=manifest if isinstance(manifest, dict) else {},
		tape_path=tape if tape and tape.is_file() else None,
	)


def newest_final_day(root: str | Path) -> str | None:
	"""The newest day the grader called final -- what a bare post targets.

	A day still being graded intraday is not postable, so it is skipped here
	rather than becoming a default that posts a half-graded session.
	"""
	for date in list_days(root):
		manifest = _read_json(days_dir(root) / date / MANIFEST_FILENAME, {})
		if not manifest.get("partial_intraday"):
			return date
	return None


def _read_json(path: Path, default: Any) -> Any:
	return json.loads(path.read_text()) if path.is_file() else default


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
