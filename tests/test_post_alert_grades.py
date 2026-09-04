"""Config routing and exit codes for the one-shot poster.

Everything here stops short of connecting to Discord -- `--dry-run` and the
failure paths return before login, and the production-channel guard is
asserted on the resolved channel id rather than by posting.
"""

import pytest

from discord_mod_bot import post_alert_grades
from discord_mod_bot.bot import ModBotConfig


FIXTURE = "tests/fixtures/grades"

DEV_CHANNEL = 1507130942488313998
PROD_CHANNEL = 1493353808900784251


@pytest.fixture
def env(monkeypatch):
	monkeypatch.setenv("DISCORD_MOD_BOT_TOKEN", "test-token")
	monkeypatch.setenv("MOD_BOT_ALERT_GRADES_DIR", FIXTURE)
	monkeypatch.setenv("MOD_BOT_ALERT_GRADES_CHANNEL_DEV", str(DEV_CHANNEL))
	monkeypatch.setenv("MOD_BOT_ALERT_GRADES_CHANNEL_PROD", str(PROD_CHANNEL))
	monkeypatch.delenv("MOD_BOT_ALERT_GRADES_ENV", raising=False)
	return monkeypatch


def test_default_env_is_development(env):
	config = ModBotConfig.from_env()
	assert config.grades_env == "development"
	assert config.grades_channel_id == DEV_CHANNEL


@pytest.mark.parametrize("value", ["development", "staging", "prod", "production-ish", ""])
def test_only_an_exact_production_value_reaches_the_production_channel(env, value):
	env.setenv("MOD_BOT_ALERT_GRADES_ENV", value)
	assert ModBotConfig.from_env().grades_channel_id == DEV_CHANNEL


@pytest.mark.parametrize("value", ["production", "PRODUCTION ", " Production"])
def test_production_is_opt_in(env, value):
	"""from_env strips and lowercases, so casing is forgiven -- but the
	operator still has to write the word."""
	env.setenv("MOD_BOT_ALERT_GRADES_ENV", value)
	assert ModBotConfig.from_env().grades_channel_id == PROD_CHANNEL


def test_dry_run_succeeds(env):
	assert post_alert_grades.main(["2026-09-02", "--dry-run"]) == 0


def test_dry_run_defaults_to_the_newest_day(env):
	assert post_alert_grades.main(["--dry-run"]) == 0


def test_unknown_day_exits_non_zero(env, capsys):
	assert post_alert_grades.main(["1999-01-01", "--dry-run"]) == 1
	assert "2026-09-02" in capsys.readouterr().err


def test_empty_ledger_exits_non_zero(env, tmp_path):
	env.setenv("MOD_BOT_ALERT_GRADES_DIR", str(tmp_path))
	assert post_alert_grades.main(["--dry-run"]) == 1


def test_missing_tape_exits_non_zero(env, tmp_path, capsys):
	day = tmp_path / "days" / "2026-09-02"
	day.mkdir(parents=True)
	(day / "grades.json").write_text("[]")
	env.setenv("MOD_BOT_ALERT_GRADES_DIR", str(tmp_path))
	assert post_alert_grades.main(["--dry-run"]) == 1
	assert "tape.png" in capsys.readouterr().err


def test_missing_channel_id_exits_non_zero(env, capsys):
	env.delenv("MOD_BOT_ALERT_GRADES_CHANNEL_DEV")
	assert post_alert_grades.main(["--dry-run"]) == 1
	assert "MOD_BOT_ALERT_GRADES_CHANNEL_DEV" in capsys.readouterr().err
