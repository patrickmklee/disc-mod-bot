"""Deliver the cards pytrade-bot rendered, and route the clicks on them.

pytrade-bot owns everything rendered; this bot owns everything delivered or
clicked. So nothing here builds a component: `run.json` maps a `custom_id` to
a file under `days/<date>/cards/`, that file already holds a Components V2
array, and this module puts it on the wire unchanged. The day post's select
menu and three buttons come the same way -- lifted out of `post.json`.

This is the only module besides `bot.py` that imports discord.py.
"""

from __future__ import annotations

from typing import Any, Optional

import discord

from discord_mod_bot import alert_grades

CLICK_PREFIX = "ag:"


class RawView(discord.ui.LayoutView):
	"""Emit a component array exactly as pytrade-bot wrote it.

	discord.py builds its outgoing payload from `to_components()` and sets the
	Components V2 flag from `has_components_v2()`, so overriding the pair is
	enough to send a pre-rendered array through the library's normal send
	paths. Nothing is registered as a child: clicks are routed by `custom_id`
	in `ClickRouter`, not by view dispatch.
	"""

	def __init__(self, components: list[dict[str, Any]], *, components_v2: bool):
		super().__init__(timeout=None)
		self._components = components
		self._components_v2 = components_v2

	def to_components(self) -> list[dict[str, Any]]:
		return self._components

	def has_components_v2(self) -> bool:
		return self._components_v2


def card_view(card: alert_grades.Card) -> RawView:
	"""The ephemeral reply for one click."""
	return RawView(card.components, components_v2=True)


def day_view(day: alert_grades.DayReport) -> Optional[RawView]:
	"""The select and buttons for the day post, or None when it has none.

	They ride the phase-1 classic-embed message, which cannot also declare
	Components V2 -- that flag suppresses `embeds` on the message it is set on.
	"""
	rows = alert_grades.day_action_rows(day)
	return RawView(rows, components_v2=False) if rows else None


def card_files(card: alert_grades.Card) -> list[discord.File]:
	"""Upload the files the card's `attachment://` references resolve against."""
	return [discord.File(path, filename=path.name) for path in card.attachments]


def click_date(custom_id: str) -> Optional[str]:
	"""The ledger day a click belongs to, read off the grader's id scheme."""
	parts = custom_id.split(":")
	return parts[1] if len(parts) > 2 and parts[0] == CLICK_PREFIX[:-1] else None


class ClickRouter:
	"""Resolve a component interaction to a card and reply with it.

	Routing is by `custom_id` rather than by `discord.ui` children because the
	components are pytrade-bot's: they arrive as JSON, never as items this bot
	constructed, and they outlive any view object -- the day post keeps working
	across a restart.
	"""

	def __init__(self, grades_dir: str, logger):
		self.grades_dir = grades_dir
		self.log = logger

	async def on_interaction(self, interaction: discord.Interaction) -> None:
		if interaction.type is not discord.InteractionType.component:
			return
		data = interaction.data or {}
		values = data.get("values") or []
		custom_id = str(values[0] if values else data.get("custom_id") or "")
		date = click_date(custom_id)
		if not date:
			return
		try:
			day = alert_grades.load_day(self.grades_dir, date)
		except alert_grades.DayNotFound:
			await self._gone(interaction, date)
			return
		card = alert_grades.resolve_click(day, custom_id)
		if card is None:
			await self._gone(interaction, date)
			return
		self.log.info("alert-grades click %s -> %s", custom_id, card.payload.get("kind"))
		view = card_view(card)
		files = card_files(card)
		if alert_grades.edits_in_place(custom_id):
			await interaction.response.edit_message(view=view, attachments=files)
		else:
			await interaction.response.send_message(view=view, files=files, ephemeral=True)

	async def _gone(self, interaction: discord.Interaction, date: str) -> None:
		"""A card the manifest no longer carries -- a regrade dropped it."""
		await interaction.response.send_message(
			f"That card is no longer in the `{date}` ledger; run `/grade {date}` again.",
			ephemeral=True,
		)
