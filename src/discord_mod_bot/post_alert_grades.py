"""One-shot poster for the daily alert-grading report.

    uv run python -m discord_mod_bot.post_alert_grades [YYYY-MM-DD] [--dry-run]

Logs in, posts the day's report to the channel chosen by
MOD_BOT_ALERT_GRADES_ENV, logs out. Called by pytrade-bot's
`scripts/grade_alerts_daily.sh` after the grader runs -- there is no
in-process scheduler on purpose.

Exits non-zero if the day directory, the tape image, or the destination
channel id is missing.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from discord_mod_bot import alert_grades
from discord_mod_bot.bot import DiscordModBot, ModBotConfig


LOG = logging.getLogger("discord_mod_bot.post_alert_grades")


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(prog="python -m discord_mod_bot.post_alert_grades")
	parser.add_argument("date", nargs="?", help="Ledger day (default: newest).")
	parser.add_argument(
		"--dry-run",
		action="store_true",
		help="Resolve and describe the post without connecting to Discord.",
	)
	args = parser.parse_args(argv)

	logging.basicConfig(
		level="INFO",
		format="%(asctime)s %(levelname)s %(name)s: %(message)s",
	)
	config = ModBotConfig.from_env()

	days = alert_grades.list_days(config.grades_dir)
	if not days:
		print(f"No ledger days under {config.grades_dir}", file=sys.stderr)
		return 1
	try:
		day = alert_grades.load_day(config.grades_dir, args.date or days[0])
	except alert_grades.DayNotFound as exc:
		print(
			f"No grades for {exc.date}. Available: {', '.join(exc.available)}",
			file=sys.stderr,
		)
		return 1
	if day.tape_path is None:
		print(f"Missing {alert_grades.TAPE_FILENAME} in {day.directory}", file=sys.stderr)
		return 1

	channel_id = config.grades_channel_id
	if not channel_id:
		print(
			f"No channel id for MOD_BOT_ALERT_GRADES_ENV={config.grades_env}"
			" (set MOD_BOT_ALERT_GRADES_CHANNEL_DEV / _PROD)",
			file=sys.stderr,
		)
		return 1

	groups = alert_grades.split_for_post(day.embeds)
	LOG.info(
		"%s: %d alerts, %d embeds in %d message(s), %d chars, tape %s -> %s channel %d",
		day.date,
		len(day.records),
		len(day.embeds),
		len(groups),
		alert_grades.payload_chars(day.embeds),
		day.tape_path,
		config.grades_env,
		channel_id,
	)
	if args.dry_run:
		return 0

	try:
		asyncio.run(_post(config, day, channel_id))
	except Exception as exc:
		LOG.exception("Post failed", exc_info=exc)
		return 1
	return 0


async def _post(config: ModBotConfig, day: alert_grades.DayReport, channel_id: int) -> None:
	mod = DiscordModBot(config)
	client = mod.bot
	async with client:
		session = asyncio.create_task(client.start(config.token))
		await client.wait_until_ready()
		channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
		await mod.send_day_report(channel, day)
		await client.close()
		await session


if __name__ == "__main__":
	raise SystemExit(main())
