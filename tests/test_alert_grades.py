"""Tests for the pure alert-grading ledger reader.

The fixture under tests/fixtures/grades is a trimmed copy of a real
~/pytrade-signal-grades day: two records, one with full contract/realizable
numbers and one where both blocks are status-only stubs. This module reads
the ledger and never renders it, so the tests assert what was loaded and how
it is split for posting -- never how a card looks.
"""

import json
from pathlib import Path

import pytest

from discord_mod_bot import alert_grades


FIXTURE = Path(__file__).parent / "fixtures" / "grades"


@pytest.fixture
def day():
	return alert_grades.load_day(FIXTURE, "2026-09-02")


def _day_dir(root, date, manifest=None, grades="[]"):
	d = root / "days" / date
	d.mkdir(parents=True)
	(d / "grades.json").write_text(grades)
	if manifest is not None:
		(d / alert_grades.MANIFEST_FILENAME).write_text(json.dumps(manifest))
	return d


def test_list_days_newest_first(tmp_path):
	for date in ("2026-09-01", "2026-09-03", "2026-09-02"):
		_day_dir(tmp_path, date)
	(tmp_path / "days" / "not-a-day").mkdir()
	assert alert_grades.list_days(tmp_path) == ["2026-09-03", "2026-09-02", "2026-09-01"]


def test_list_days_missing_root(tmp_path):
	assert alert_grades.list_days(tmp_path / "nope") == []


def test_load_day(day):
	assert day.date == "2026-09-02"
	assert len(day.records) == 2
	assert day.tape_path is not None and day.tape_path.name == "tape.png"
	assert day.embeds[0]["image"] == {"url": "attachment://tape.png"}
	assert day.username


def test_load_day_unknown_date_lists_available():
	with pytest.raises(alert_grades.DayNotFound) as exc:
		alert_grades.load_day(FIXTURE, "1999-01-01")
	assert exc.value.date == "1999-01-01"
	assert exc.value.available == ["2026-09-02"]


# ----------------------------------------------------------------------
# The run.json manifest
# ----------------------------------------------------------------------


def test_load_day_reads_the_manifest(day):
	assert day.manifest["verdict_version"] == 2
	assert day.partial_intraday is False
	assert day.png_name == "tape.png"


def test_a_day_without_a_manifest_names_no_png(tmp_path):
	"""An absent run.json is not a chartless day -- it is a day the grader
	never finished. The poster refuses it; the loader just reports nothing."""
	_day_dir(tmp_path, "2026-09-02")
	loaded = alert_grades.load_day(tmp_path, "2026-09-02")
	assert loaded.manifest == {}
	assert loaded.png_name is None
	assert loaded.tape_path is None


def test_a_null_png_is_a_day_with_no_chart(tmp_path):
	"""Zero contract bars is a legitimate, if poor, day: no image, still posted."""
	_day_dir(tmp_path, "2026-09-02", {"partial_intraday": False, "png": None})
	loaded = alert_grades.load_day(tmp_path, "2026-09-02")
	assert loaded.png_name is None
	assert loaded.tape_path is None
	assert loaded.partial_intraday is False


def test_a_named_png_that_is_absent_leaves_no_tape_path(tmp_path):
	_day_dir(tmp_path, "2026-09-02", {"png": "tape.png"})
	loaded = alert_grades.load_day(tmp_path, "2026-09-02")
	assert loaded.png_name == "tape.png"
	assert loaded.tape_path is None


def test_partial_intraday_is_read_from_the_manifest(tmp_path):
	_day_dir(tmp_path, "2026-09-02", {"partial_intraday": True, "png": None})
	assert alert_grades.load_day(tmp_path, "2026-09-02").partial_intraday is True


def test_newest_final_day_skips_a_still_open_session(tmp_path):
	_day_dir(tmp_path, "2026-09-02", {"partial_intraday": False})
	_day_dir(tmp_path, "2026-09-03", {"partial_intraday": True})
	assert alert_grades.list_days(tmp_path)[0] == "2026-09-03"
	assert alert_grades.newest_final_day(tmp_path) == "2026-09-02"


def test_newest_final_day_when_every_day_is_partial(tmp_path):
	_day_dir(tmp_path, "2026-09-03", {"partial_intraday": True})
	assert alert_grades.newest_final_day(tmp_path) is None


def test_newest_final_day_on_the_fixture():
	assert alert_grades.newest_final_day(FIXTURE) == "2026-09-02"


# ----------------------------------------------------------------------
# Message budget
# ----------------------------------------------------------------------


def test_payload_chars_counts_titles_descriptions_fields_and_footers():
	embed = {
		"title": "ab",
		"description": "cde",
		"footer": {"text": "f"},
		"fields": [{"name": "gh", "value": "ijk"}],
	}
	assert alert_grades.payload_chars([embed]) == 2 + 3 + 1 + 2 + 3


def test_split_for_post_respects_the_character_budget():
	big = {"title": "t", "description": "x" * 3500, "fields": []}
	groups = alert_grades.split_for_post([big, big, big])
	assert [len(g) for g in groups] == [1, 1, 1]
	for group in groups:
		assert alert_grades.payload_chars(group) <= alert_grades.EMBED_CHAR_BUDGET


def test_split_for_post_respects_the_ten_embed_cap():
	small = {"title": "t", "fields": []}
	groups = alert_grades.split_for_post([small] * 23)
	assert [len(g) for g in groups] == [10, 10, 3]


def test_split_for_post_keeps_a_fixture_day_in_one_message(day):
	assert len(alert_grades.split_for_post(day.embeds)) == 1


def test_split_for_post_emits_an_oversized_embed_alone():
	oversized = {"description": "x" * 7000, "fields": []}
	small = {"title": "t", "fields": []}
	assert alert_grades.split_for_post([small, oversized]) == [[small], [oversized]]


def test_real_day_json_shapes_match_the_fixture():
	"""Guards the repo contract: discord.json is post-shaped, the summary
	embed references the tape as an attachment, and run.json carries the two
	keys the poster branches on."""
	base = FIXTURE / "days/2026-09-02"
	payload = json.loads((base / "discord.json").read_text())
	assert set(payload) == {"username", "embeds"}
	assert payload["embeds"][0]["image"]["url"].startswith("attachment://")
	manifest = json.loads((base / "run.json").read_text())
	assert "png" in manifest and "partial_intraday" in manifest
