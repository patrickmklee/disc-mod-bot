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
      post.json      the Components V2 message, whose action rows carry the
                     select menu and the three day buttons
      cards/         one pre-rendered card per click, plus ledger.csv;
                     run.json's "custom_ids" maps a custom_id to one of them
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Discord message budgets (see docs/handoffs/2026-09-04-alert-grades-report.md).
EMBEDS_PER_MESSAGE = 10
EMBED_CHAR_BUDGET = 6000

MANIFEST_FILENAME = "run.json"
POST_FILENAME = "post.json"

# Components V2 budgets. A message may hold 40 components counting every
# nesting level, and the V2 flag suppresses `embeds` on the message it is set
# on and cannot be removed afterwards -- so the layout is decided before the
# send, never patched after it.
MAX_COMPONENTS = 40
LAYOUT_V2 = "v2"
LAYOUT_CLASSIC = "classic"

# The clicks that edit the ephemeral card in place instead of opening a new
# one. They are the tails of the grader's "alert_exits" / "alert_prev" /
# "alert_next" custom_id templates; a test pins them to run.json's
# "custom_id_scheme" so a rename upstream fails here rather than in Discord.
EDIT_IN_PLACE_SUFFIXES = ("exits", "prev", "next")


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
	post: dict[str, Any] = field(default_factory=dict)

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
		post=_read_json(directory / POST_FILENAME, {}),
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


# ----------------------------------------------------------------------
# Click routing
#
# Every card under `cards/` is pytrade-bot's, rendered and complete: this
# bot maps the clicked custom_id to one through the manifest and sends it
# back. Nothing here reads a grade or shapes a card.
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Card:
	"""A pre-rendered card and the files its components reference."""

	custom_id: str
	payload: dict[str, Any]
	attachments: list[Path]

	@property
	def components(self) -> list[dict[str, Any]]:
		"""The Components V2 array, sent verbatim."""
		return self.payload.get("components") or []


def resolve_click(day: DayReport, custom_id: str) -> Card | None:
	"""The card answering a click, or None when the manifest has no such id.

	A stale button -- a card kept open across a regrade that dropped an alert
	-- lands here, so an unknown id is an answer, not an error.
	"""
	relative = (day.manifest.get("custom_ids") or {}).get(custom_id)
	if not relative:
		return None
	path = day.directory / str(relative)
	if not path.is_file():
		return None
	payload = json.loads(path.read_text())
	return Card(custom_id=custom_id, payload=payload, attachments=_attachments(day, payload))


def day_action_rows(day: DayReport) -> list[dict[str, Any]]:
	"""The select menu and day buttons, lifted whole out of `post.json`.

	They sit inside the V2 container, so the walk is recursive. A day the
	grader wrote before it rendered a post has none, and the report still
	posts -- without the interaction layer.
	"""
	rows: list[dict[str, Any]] = []
	_collect_action_rows(day.post.get("components") or [], rows)
	return rows


def count_components(payload: dict[str, Any]) -> int:
	"""Every component in the tree. A V2 container is one component holding
	many, so counting the top-level list would pass any budget vacuously."""
	def walk(items: Iterable[Any]) -> int:
		total = 0
		for component in items:
			if not isinstance(component, dict):
				continue
			total += 1
			total += walk(component.get("components") or [])
			accessory = component.get("accessory")
			if isinstance(accessory, dict):
				total += 1 + walk(accessory.get("components") or [])
		return total
	return walk(payload.get("components") or [])


def post_components(day: "DayReport") -> list[dict[str, Any]] | None:
	"""pytrade-bot's V2 message body, or None when the day cannot send one."""
	return list(day.post["components"]) if v2_ready(day) else None


def _references_attachment(components: Iterable[Any]) -> bool:
	for component in components:
		if not isinstance(component, dict):
			continue
		for item in component.get("items") or []:
			url = ((item or {}).get("media") or {}).get("url") or ""
			if str(url).startswith("attachment://"):
				return True
		if _references_attachment(component.get("components") or []):
			return True
	return False


def v2_ready(day: "DayReport") -> bool:
	"""True when the day's post.json can go on the wire as written.

	Refused, and the classic embeds post instead, when: the grader wrote no
	post; it is over the component budget; or it points `attachment://` at an
	image this day has no file for, which Discord renders as a broken image
	rather than an error.
	"""
	components = day.post.get("components")
	if not isinstance(components, list) or not components:
		return False
	if count_components(day.post) > MAX_COMPONENTS:
		return False
	if _references_attachment(components) and day.tape_path is None:
		return False
	return True


def use_v2(day: "DayReport", layout: str) -> bool:
	"""The layout actually sent: the knob can force the classic embeds, but it
	cannot force V2 onto a day whose post is not sendable."""
	return layout != LAYOUT_CLASSIC and v2_ready(day)


def edits_in_place(custom_id: str) -> bool:
	"""True for the clicks that replace the open card rather than add one."""
	return ":alert:" in custom_id and custom_id.rsplit(":", 1)[-1] in EDIT_IN_PLACE_SUFFIXES


def _collect_action_rows(components: list[Any], out: list[dict[str, Any]]) -> None:
	for component in components:
		if not isinstance(component, dict):
			continue
		if component.get("type") == 1:
			out.append(component)
		else:
			_collect_action_rows(component.get("components") or [], out)


def _attachments(day: DayReport, payload: dict[str, Any]) -> list[Path]:
	"""Files the card's `attachment://` references need uploaded beside it.

	Images are written at the day root and `ledger.csv` under `cards/`, so
	both places are searched for the name the card gives.
	"""
	paths = []
	for name in (payload.get("image"), payload.get("attachment")):
		if not name:
			continue
		for candidate in (day.directory / str(name), day.directory / "cards" / str(name)):
			if candidate.is_file():
				paths.append(candidate)
				break
	return paths
