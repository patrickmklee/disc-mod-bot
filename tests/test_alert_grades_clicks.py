"""The click router: a custom_id resolves through run.json to a card file.

Every card and manifest under tests/fixtures/grades was rendered by
pytrade-bot's own `cards.build_cards` / `build_post` from the fixture day's
`grades.json`, so these tests exercise the real contract rather than a
hand-written imitation of it.
"""

from pathlib import Path

from discord_mod_bot import alert_grades

FIXTURES = Path(__file__).parent / "fixtures" / "grades"
DATE = "2026-09-02"


def _day():
	return alert_grades.load_day(FIXTURES, DATE)


def test_resolve_click_returns_the_card_pytrade_bot_rendered():
	card = alert_grades.resolve_click(_day(), f"ag:{DATE}:alert:1")
	assert card is not None
	assert card.payload["custom_id"] == f"ag:{DATE}:alert:1"
	assert card.components == card.payload["components"]


def test_resolve_click_attaches_the_alert_png_the_card_references():
	card = alert_grades.resolve_click(_day(), f"ag:{DATE}:alert:1")
	assert [p.name for p in card.attachments] == ["alert-1.png"]
	assert all(p.is_file() for p in card.attachments)


def test_resolve_click_attaches_the_ledger_csv_to_the_day_raw_card():
	card = alert_grades.resolve_click(_day(), f"ag:{DATE}:raw")
	assert [p.name for p in card.attachments] == ["ledger.csv"]
	assert card.attachments[0].is_file()


def test_resolve_click_is_none_for_an_id_the_manifest_does_not_carry():
	assert alert_grades.resolve_click(_day(), f"ag:{DATE}:alert:99") is None


def test_every_manifest_custom_id_resolves_to_a_file_on_disk():
	day = _day()
	assert day.manifest["custom_ids"]
	for custom_id in day.manifest["custom_ids"]:
		card = alert_grades.resolve_click(day, custom_id)
		assert card is not None, custom_id
		assert card.components, custom_id


def test_select_option_values_resolve_the_same_way_as_buttons():
	day = _day()
	for option in day.manifest["select_options"]:
		assert alert_grades.resolve_click(day, option["value"]) is not None


def test_day_action_rows_come_from_post_json_verbatim():
	rows = alert_grades.day_action_rows(_day())
	assert [c["type"] for row in rows for c in row["components"]] == [3, 2, 2, 2]
	assert rows[0]["components"][0]["custom_id"] == f"ag:{DATE}:select"


def test_day_action_rows_is_empty_when_the_day_has_no_post_json(tmp_path):
	day_dir = tmp_path / "days" / DATE
	day_dir.mkdir(parents=True)
	for name in ("grades.json", "run.json"):
		(day_dir / name).write_text((FIXTURES / "days" / DATE / name).read_text())
	assert alert_grades.day_action_rows(alert_grades.load_day(tmp_path, DATE)) == []


def test_alert_navigation_edits_the_card_in_place():
	for suffix in ("exits", "prev", "next"):
		assert alert_grades.edits_in_place(f"ag:{DATE}:alert:2:{suffix}")


def test_a_day_button_opens_a_new_reply_rather_than_editing_one():
	assert not alert_grades.edits_in_place(f"ag:{DATE}:exits")
	assert not alert_grades.edits_in_place(f"ag:{DATE}:alert:1")


def test_edit_in_place_suffixes_still_match_the_graders_custom_id_scheme():
	"""Guards against pytrade-bot renaming a suffix under us."""
	scheme = _day().manifest["custom_id_scheme"]
	for key in ("alert_exits", "alert_prev", "alert_next"):
		assert alert_grades.edits_in_place(scheme[key].replace("<n>", "2"))
