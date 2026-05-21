"""Command scaffolding for the mod bot.

Each bot command parses its raw discord-args string into a `CommandArgs`
object so handlers see a uniform shape:

  - `subcommand`: the first positional, e.g. `!report today` -> "today"
  - `positional`: remaining positional args, e.g. `!report week AMD` ->
	("AMD",) after subcommand
  - `flags`: bare flags like `--json` -> {"json"}
  - `options`: key=value flags like `--ticker=AMD` -> {"ticker": "AMD"}

`CommandSpec` declares per-command metadata (enabled gate, allowed-by
role, default subcommand, accepted subcommands) so we can centralize the
plumbing in bot.py rather than duplicating it in every handler.

The accepted-subcommands list is advisory: unknown subcommands are still
delivered to the handler so it can decide whether to reject or accept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class CommandArgs:
	subcommand: str = ""
	positional: tuple[str, ...] = ()
	flags: frozenset[str] = field(default_factory=frozenset)
	options: dict[str, str] = field(default_factory=dict)

	def flag(self, name: str) -> bool:
		return name in self.flags

	def option(self, name: str, default: str = "") -> str:
		return self.options.get(name, default)


@dataclass(frozen=True)
class CommandSpec:
	name: str
	description: str = ""
	default_subcommand: str = ""
	allowed_subcommands: tuple[str, ...] = ()

	def normalise_subcommand(self, raw: str) -> str:
		"""Return the canonical subcommand, falling back to default.

		Empty input -> default_subcommand. An unknown subcommand is
		returned as-is so the handler can produce a usage message.
		"""
		text = (raw or "").strip().lower()
		if not text:
			return self.default_subcommand
		return text


def parse_args(raw_args: Iterable[str], spec: CommandSpec | None = None) -> CommandArgs:
	"""Split discord.py-style *args into positional / flag / option buckets.

	Rules:
	  - `--name=value` or `--name:value` -> options[name] = value
	  - `--name`                         -> flags add `name`
	  - anything else                    -> positional argument

	The first positional becomes `subcommand` (after normalisation via
	`spec` if provided). Case is preserved for option values; subcommand
	is lower-cased.
	"""
	positional: list[str] = []
	flags: set[str] = set()
	options: dict[str, str] = {}

	for raw in raw_args:
		token = str(raw).strip()
		if not token:
			continue
		if token.startswith("--"):
			body = token[2:]
			if not body:
				continue
			if "=" in body:
				key, value = body.split("=", 1)
			elif ":" in body:
				key, value = body.split(":", 1)
			else:
				flags.add(body.lower())
				continue
			options[key.strip().lower()] = value.strip()
		else:
			positional.append(token)

	subcommand_raw = positional[0] if positional else ""
	subcommand = spec.normalise_subcommand(subcommand_raw) if spec else subcommand_raw.lower()
	remaining = tuple(positional[1:]) if positional else ()

	return CommandArgs(
		subcommand=subcommand,
		positional=remaining,
		flags=frozenset(flags),
		options=options,
	)
