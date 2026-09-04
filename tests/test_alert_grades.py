"""Tests for the pure alert-grading ledger reader.

The fixture under tests/fixtures/grades is a trimmed copy of a real
~/pytrade-signal-grades day: two records, one with full contract/realizable
numbers and one where both blocks are status-only stubs.
"""

import json
from pathlib import Path

import pytest

from discord_mod_bot import alert_grades


FIXTURE = Path(__file__).parent / "fixtures" / "grades"
STUB_KEY = "2026-09-02|sidea|homer|AVGO|put|08:35:16"
OK_KEY = "2026-09-02|sidea|homer|META|put|09:15:37"


@pytest.fixture
def v2():
	"""A real verdict-v2 record; the ledger on disk is still v1 until the
	grader is re-run against pytrade-bot main."""
	return json.loads((FIXTURE / "record-v2-aapl.json").read_text())


def _field(embed, name):
	return next(f["value"] for f in embed["fields"] if f["name"] == name)


@pytest.fixture
def day():
	return alert_grades.load_day(FIXTURE, "2026-09-02")


def record(day, key):
	found = alert_grades.find_record(day.records, key)
	assert found is not None
	return found


def test_list_days_newest_first(tmp_path):
	for date in ("2026-09-01", "2026-09-03", "2026-09-02"):
		d = tmp_path / "days" / date
		d.mkdir(parents=True)
		(d / "grades.json").write_text("[]")
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


def test_load_day_without_tape(tmp_path):
	d = tmp_path / "days" / "2026-09-02"
	d.mkdir(parents=True)
	(d / "grades.json").write_text("[]")
	assert alert_grades.load_day(tmp_path, "2026-09-02").tape_path is None


def test_alert_options_are_time_ordered_and_within_limits(day):
	options = alert_grades.alert_options(day.records)
	assert [o["value"] for o in options] == [STUB_KEY, OK_KEY]
	for option in options:
		assert 0 < len(option["label"]) <= 100
		assert len(option["description"]) <= 100
		assert len(option["value"]) <= 100
	assert "AVGO" in options[0]["label"]


def test_alert_card_full_record(day):
	embed = alert_grades.alert_card(record(day, OK_KEY))
	names = [f["name"] for f in embed["fields"]]
	assert names == ["Verdict", "Underlying", "Contract", "Realizable"]
	assert embed["title"].startswith("❌ META put 577.5")
	assert embed["color"] == alert_grades.COLOR_WRONG
	assert embed["footer"]["text"] == OK_KEY
	contract = dict(zip(names, [f["value"] for f in embed["fields"]]))["Contract"]
	assert "peak +21.0%" in contract
	assert "trail_arm30_gb30_stop30" in contract


def test_alert_card_tolerates_status_only_blocks(day):
	embed = alert_grades.alert_card(record(day, STUB_KEY))
	values = {f["name"]: f["value"] for f in embed["fields"]}
	assert values["Contract"] == "_no_option_bars_"
	assert values["Realizable"] == "_no_quotes_"
	assert "accuracy: **right**" in values["Verdict"]
	assert embed["color"] == alert_grades.COLOR_RIGHT


def test_alert_card_renders_a_verdict_v2_record(v2):
	"""Real v2 record (pytrade-bot PR #450). v2 adds blocks rather than
	replacing v1 keys, so both shapes render from the same builder."""
	embed = alert_grades.alert_card(v2)
	values = {f["name"]: f["value"] for f in embed["fields"]}
	assert [f["name"] for f in embed["fields"]] == [
		"Verdict", "Direction", "Payoff & risk", "Erraticness",
		"Underlying", "Contract", "Realizable", "Clues",
	]
	# The design's copy is trimmed and carries no `strike`; the headline
	# drops the field rather than printing a placeholder for it.
	assert embed["title"] == "✅ AAPL call — 10:31:28 ET"
	assert "payoff: **paid big**" in values["Verdict"]
	assert "accuracy 30m: **flat**" in values["Verdict"]
	assert "version" not in values["Verdict"]


def test_alert_card_direction_field_shows_the_band_behind_the_call(v2):
	direction = _field(alert_grades.alert_card(v2), "Direction")
	assert "**right** · first touch 58.5m" in direction
	assert "band ±0.63% = 1σ × 0.08%/min over 30 bars, 60m horizon" in direction


def test_alert_card_payoff_risk_field(v2):
	payoff = _field(alert_grades.alert_card(v2), "Payoff & risk")
	assert "**paid big** · realizable (stop 30%)" in payoff
	assert "moderate drawdown -26.1% before payoff" in payoff
	assert "stops surviving to peak: stop40" in payoff


def test_alert_card_payoff_risk_names_an_unrealizable_payoff_and_no_stops(v2):
	rec = dict(v2, payoff_risk=dict(
		v2["payoff_risk"], payoff_realizable=False, stops_surviving_to_peak=[]
	))
	payoff = _field(alert_grades.alert_card(rec), "Payoff & risk")
	assert "not realizable (stop 30%)" in payoff
	assert "stops surviving to peak: none" in payoff


def test_alert_card_erraticness_field_carries_the_sourced_numbers(v2):
	err = _field(alert_grades.alert_card(v2), "Erraticness")
	assert "Kaufman ER 0.32 to peak" in err
	assert "range 8.61%/min" in err
	assert "jumps 2 (0 before peak)" in err
	assert "wick-only hits 23 at -30%" in err
	assert "stop_survival" not in err  # the nested dict is not dumped raw


def test_alert_card_erraticness_falls_back_to_status(v2):
	rec = dict(v2, erraticness={"status": "no_path"})
	assert _field(alert_grades.alert_card(rec), "Erraticness") == "_no_path_"


def test_alert_card_direction_falls_back_to_the_reason(v2):
	rec = dict(v2, direction_touch={"verdict": "unknown", "reason": "no_underlying_bars"})
	assert _field(alert_grades.alert_card(rec), "Direction") == (
		"**unknown** — _no_underlying_bars_"
	)


def test_alert_card_clues_are_shown_and_empty_ones_dropped(v2):
	clues = _field(alert_grades.alert_card(v2), "Clues")
	assert "pre alert rel volume: 2.98" in clues
	assert "tod bucket: morning_10-11_30" in clues
	rec = dict(v2, clues=dict(v2["clues"], vwap_dist_signed_pct=None))
	assert "vwap dist" not in _field(alert_grades.alert_card(rec), "Clues")


def test_v1_record_has_no_v2_fields(day):
	names = [f["name"] for f in alert_grades.alert_card(record(day, OK_KEY))["fields"]]
	assert names == ["Verdict", "Underlying", "Contract", "Realizable"]


def test_alert_card_fields_fit_discord_limits(day):
	for rec in day.records:
		embed = alert_grades.alert_card(rec)
		assert len(embed["title"]) <= 256
		assert len(embed["fields"]) <= 25
		for f in embed["fields"]:
			assert len(f["value"]) <= 1024


def test_raw_blocks_keep_every_field_of_a_real_record(day):
	"""A full record does not fit one message once what_if is included, so
	the fields have to survive across blocks rather than be truncated."""
	rec = record(day, OK_KEY)
	blocks = alert_grades.raw_blocks(rec)
	assert len(blocks) > 1
	joined = "".join(blocks)
	for key in rec["contract"]["what_if"]:
		assert key in joined
	assert "entry_lag_min" in joined and "und_mfe_pct" in joined


def test_raw_blocks_fit_the_message_limit(day):
	for rec in day.records:
		for block in alert_grades.raw_blocks(rec):
			assert block.startswith("```json\n") and block.endswith("\n```")
			assert len(block) <= alert_grades.MESSAGE_CHAR_LIMIT


def test_raw_blocks_truncate_a_single_oversized_line():
	"""Splitting happens on line boundaries, so one absurdly long value is
	still cut -- visibly, with an ellipsis, rather than silently."""
	blocks = alert_grades.raw_blocks({"blob": "x" * 5000})
	assert len(blocks) == 3
	assert "..." in blocks[1]
	for block in blocks:
		assert len(block) <= alert_grades.MESSAGE_CHAR_LIMIT


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
	"""Guards the repo contract: discord.json is post-shaped and the summary
	embed references the tape as an attachment."""
	payload = json.loads((FIXTURE / "days/2026-09-02/discord.json").read_text())
	assert set(payload) == {"username", "embeds"}
	assert payload["embeds"][0]["image"]["url"].startswith("attachment://")
