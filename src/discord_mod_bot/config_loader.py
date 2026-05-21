"""YAML config loader for the mod bot.

The mod bot has two layers of config:

  - `config.yaml` (this loader): declarative -- per-command enable / role
    gate, plus webhook URLs (Discord webhooks the bot POSTs to). Lives in
    `discord_mod_bot/config.yaml` and is checked into the repo.
  - `.env` (handled in bot.py): secrets and per-host wiring -- bot token,
    local webhook server URL, channel allowlist.

Keeping them separate means the YAML can ship in git while secrets stay
in the environment.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


LOG = logging.getLogger("discord_mod_bot.config")

DEFAULT_CONFIG_FILENAME = "config.yaml"


@dataclass(frozen=True)
class CommandConfig:
	enabled: bool = True
	allowed_by: str = "members"  # "members" | "admin"


@dataclass(frozen=True)
class WebhookConfig:
	enabled: bool = True
	name: str = ""
	url: str = ""

	@property
	def full_url(self) -> str:
		"""Resolve to a full https URL.

		Stored YAML often has the path portion only
		(`webhooks/<id>/<token>`); prepend the Discord API host when
		needed so callers can POST directly.
		"""
		raw = (self.url or "").strip().strip('"').strip("'")
		if not raw:
			return ""
		if raw.startswith(("http://", "https://")):
			return raw
		return "https://discord.com/api/" + raw.lstrip("/")


@dataclass(frozen=True)
class YamlConfig:
	command_prefix: str = "!"
	commands: dict[str, CommandConfig] = field(default_factory=dict)
	webhooks: dict[str, WebhookConfig] = field(default_factory=dict)

	def command(self, name: str) -> CommandConfig:
		"""Return the config for a command, defaulting to enabled+members."""
		return self.commands.get(name, CommandConfig())

	def webhook(self, name: str) -> WebhookConfig:
		"""Return the config for a webhook target, defaulting to empty."""
		return self.webhooks.get(name, WebhookConfig(enabled=False))


def load_yaml_config(path: str | os.PathLike | None = None) -> YamlConfig:
	"""Load config.yaml, returning defaults on any failure.

	`path` defaults to `discord_mod_bot/config.yaml` next to this file.
	Override with the `MOD_BOT_CONFIG_PATH` env var if the bot is run
	from a different working directory.
	"""
	resolved = _resolve_path(path)
	if resolved is None or not resolved.exists():
		LOG.info("No config.yaml found at %s; using defaults", resolved)
		return YamlConfig()

	try:
		import yaml  # PyYAML
	except ImportError:
		LOG.warning("PyYAML not installed; cannot load %s", resolved)
		return YamlConfig()

	try:
		with resolved.open("r", encoding="utf-8") as fh:
			raw = yaml.safe_load(fh) or {}
	except Exception as exc:
		LOG.warning("Failed to parse %s: %s -- using defaults", resolved, exc)
		return YamlConfig()

	if not isinstance(raw, dict):
		LOG.warning("%s is not a mapping; using defaults", resolved)
		return YamlConfig()

	prefix = str(raw.get("command_prefix") or "!").strip() or "!"
	commands = _parse_commands(raw.get("commands"))
	webhooks = _parse_webhooks(raw.get("webhooks"))
	LOG.info("Loaded %d commands and %d webhooks from %s",
			 len(commands), len(webhooks), resolved)
	return YamlConfig(
		command_prefix=prefix,
		commands=commands,
		webhooks=webhooks,
	)


def _resolve_path(path: str | os.PathLike | None) -> Path | None:
	if path is not None:
		return Path(path)
	env_path = os.environ.get("MOD_BOT_CONFIG_PATH", "").strip()
	if env_path:
		return Path(env_path)
	return Path(__file__).parent / DEFAULT_CONFIG_FILENAME


def _parse_commands(raw: Any) -> dict[str, CommandConfig]:
	out: dict[str, CommandConfig] = {}
	if not isinstance(raw, dict):
		return out
	for name, body in raw.items():
		if not isinstance(body, dict):
			continue
		out[str(name)] = CommandConfig(
			enabled=bool(body.get("enabled", True)),
			allowed_by=str(body.get("allowed-by") or body.get("allowed_by") or "members"),
		)
	return out


def _parse_webhooks(raw: Any) -> dict[str, WebhookConfig]:
	out: dict[str, WebhookConfig] = {}
	if not isinstance(raw, dict):
		return out
	for name, body in raw.items():
		if not isinstance(body, dict):
			continue
		out[str(name)] = WebhookConfig(
			enabled=bool(body.get("enabled", True)),
			name=str(body.get("name") or "").strip(),
			url=str(body.get("url") or "").strip(),
		)
	return out
