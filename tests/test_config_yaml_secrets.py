"""The shipped config.yaml must never carry a Discord webhook token."""

import re
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "src" / "discord_mod_bot" / "config.yaml"
WEBHOOK_TOKEN = re.compile(r"webhooks/\d{15,}/[A-Za-z0-9_-]{20,}")


def test_shipped_config_has_no_webhook_token():
	assert not WEBHOOK_TOKEN.search(CONFIG.read_text())
