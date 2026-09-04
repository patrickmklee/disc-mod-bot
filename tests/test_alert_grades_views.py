"""The delivery layer: what goes on the wire is pytrade-bot's array, verbatim.

These assert against discord.py's own outgoing-payload builder rather than a
mock, so a change in how the library serializes a view fails here.
"""

import json
from pathlib import Path

from discord import InteractionResponseType
from discord.http import handle_message_parameters
from discord.webhook.async_ import interaction_message_response_params

from discord_mod_bot import alert_grades, views

FIXTURES = Path(__file__).parent / "fixtures" / "grades"
DATE = "2026-09-02"
COMPONENTS_V2 = 1 << 15


def _day():
	return alert_grades.load_day(FIXTURES, DATE)


def _payload(view):
	return handle_message_parameters(view=view).payload


def test_a_card_goes_out_as_the_component_array_the_grader_wrote():
	card = alert_grades.resolve_click(_day(), f"ag:{DATE}:alert:1")
	assert _payload(views.card_view(card))["components"] == card.components


def test_a_card_message_declares_components_v2():
	card = alert_grades.resolve_click(_day(), f"ag:{DATE}:alert:1")
	assert _payload(views.card_view(card))["flags"] & COMPONENTS_V2


def test_the_day_rows_ride_a_classic_embed_message_so_they_set_no_v2_flag():
	day = _day()
	payload = _payload(views.day_view(day))
	assert payload["components"] == alert_grades.day_action_rows(day)
	assert not payload.get("flags", 0) & COMPONENTS_V2


def test_no_day_view_when_the_grader_wrote_no_post(tmp_path):
	day_dir = tmp_path / "days" / DATE
	day_dir.mkdir(parents=True)
	for name in ("grades.json", "run.json"):
		(day_dir / name).write_text((FIXTURES / "days" / DATE / name).read_text())
	assert views.day_view(alert_grades.load_day(tmp_path, DATE)) is None


def test_card_files_upload_what_the_cards_attachment_urls_reference():
	card = alert_grades.resolve_click(_day(), f"ag:{DATE}:raw")
	assert [f.filename for f in views.card_files(card)] == ["ledger.csv"]


def test_click_date_is_read_off_the_custom_id():
	assert views.click_date(f"ag:{DATE}:alert:1:next") == DATE
	assert views.click_date("some-other-button") is None


# The in-place edits ("All 6 exits", prev / next) go out through a different
# builder than a fresh reply, so the array and the flag are asserted again on
# that path rather than assumed to carry over.


def _edit_payload(view, files):
	"""The JSON discord.py sends for an edit -- inside the multipart body,
	since the new card's image is uploaded with it."""
	params = interaction_message_response_params(
		type=InteractionResponseType.message_update.value, view=view, attachments=files
	)
	part = next(p for p in params.multipart if p["name"] == "payload_json")
	return json.loads(part["value"])


def test_an_in_place_edit_replaces_the_card_with_the_next_ones_components():
	card = alert_grades.resolve_click(_day(), f"ag:{DATE}:alert:1:next")
	payload = _edit_payload(views.card_view(card), views.card_files(card))
	assert payload["data"]["components"] == card.components
	assert payload["data"]["flags"] & COMPONENTS_V2


def test_an_in_place_edit_swaps_the_attachment_set_to_the_new_alerts_png():
	card = alert_grades.resolve_click(_day(), f"ag:{DATE}:alert:1:next")
	payload = _edit_payload(views.card_view(card), views.card_files(card))
	assert [a["filename"] for a in payload["data"]["attachments"]] == ["alert-2.png"]


def test_click_date_refuses_anything_that_is_not_a_ledger_date():
	"""A day is a directory name, so only the grader's date shape is one."""
	assert views.click_date("ag:../../../etc:alert:1") is None
	assert views.click_date("ag::alert:1") is None
