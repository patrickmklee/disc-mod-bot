"""Phase 3: the day post goes out as pytrade-bot's Components V2 message.

The V2 flag cannot be removed from a message once set and it suppresses
`embeds`, so the choice between layouts is made before anything is sent, and
the classic embeds stay the fallback for a day that has no usable post.
"""

from pathlib import Path

from discord.http import handle_message_parameters

from discord_mod_bot import alert_grades, views
from discord_mod_bot.bot import ModBotConfig

FIXTURES = Path(__file__).parent / "fixtures" / "grades"
DATE = "2026-09-02"
COMPONENTS_V2 = 1 << 15


def _day(root=FIXTURES, date=DATE):
	return alert_grades.load_day(root, date)


def _copy_day(tmp_path, *names):
	day_dir = tmp_path / "days" / DATE
	day_dir.mkdir(parents=True)
	for name in names:
		(day_dir / name).write_bytes((FIXTURES / "days" / DATE / name).read_bytes())
	return day_dir


def test_the_post_is_usable_and_is_the_grader_array_verbatim():
	day = _day()
	assert alert_grades.v2_ready(day)
	assert alert_grades.post_components(day) == day.post["components"]


def test_post_view_sends_the_array_with_the_v2_flag():
	day = _day()
	payload = handle_message_parameters(view=views.post_view(day)).payload
	assert payload["components"] == day.post["components"]
	assert payload["flags"] & COMPONENTS_V2
	# a V2 message carries no embeds; the tape rides as an uploaded file
	assert not payload.get("embeds")


def test_no_post_json_means_no_v2_and_the_classic_report_still_posts(tmp_path):
	_copy_day(tmp_path, "grades.json", "run.json", "discord.json", "tape.png")
	day = _day(tmp_path)
	assert not alert_grades.v2_ready(day)
	assert alert_grades.post_components(day) is None
	assert views.post_view(day) is None
	assert day.embeds					# the fallback is intact


def test_a_post_referencing_an_image_the_day_lacks_is_not_v2_ready(tmp_path):
	"""attachment:// against a file that is never uploaded renders broken."""
	_copy_day(tmp_path, "grades.json", "run.json", "discord.json", "post.json")
	day = _day(tmp_path)
	assert day.tape_path is None
	assert not alert_grades.v2_ready(day)


def test_a_post_over_the_component_budget_is_refused(tmp_path):
	import json

	_copy_day(tmp_path, "grades.json", "run.json", "discord.json", "tape.png", "post.json")
	path = tmp_path / "days" / DATE / "post.json"
	post = json.loads(path.read_text())
	box = post["components"][0]
	box["components"] += [{"type": 14} for _ in range(alert_grades.MAX_COMPONENTS)]
	path.write_text(json.dumps(post))
	day = _day(tmp_path)
	assert alert_grades.count_components(day.post) > alert_grades.MAX_COMPONENTS
	assert not alert_grades.v2_ready(day)


def test_component_count_walks_the_tree():
	"""A container is one component holding many: counting the top-level list
	would pass any budget vacuously."""
	assert alert_grades.count_components({"components": [
		{"type": 17, "components": [{"type": 1, "components": [{"type": 2}, {"type": 2}]}]}]}) == 4


def test_layout_knob_can_force_the_classic_embeds():
	day = _day()
	assert alert_grades.use_v2(day, "v2")
	assert not alert_grades.use_v2(day, "classic")
	assert ModBotConfig(token="t").grades_layout == "v2"


def test_the_v2_message_carries_the_click_layer_the_router_resolves():
	"""The select and buttons live inside the container, so sending the post
	whole is what attaches them -- no separate view on the V2 path."""
	day = _day()
	rows = alert_grades.day_action_rows(day)
	assert rows
	flat = str(alert_grades.post_components(day))
	for row in rows:
		for component in row["components"]:
			ident = component.get("custom_id")
			if ident:
				assert ident in flat
			for option in component.get("options") or []:
				assert alert_grades.resolve_click(day, option["value"]) is not None


class _Destination:
	def __init__(self):
		self.sends = []

	async def send(self, **kwargs):
		self.sends.append(kwargs)


def _bot(layout=alert_grades.LAYOUT_V2):
	from discord_mod_bot.bot import DiscordModBot
	return DiscordModBot(ModBotConfig(token="test-token", grades_layout=layout))


def test_the_v2_path_sends_one_message_with_the_tape_and_no_embeds():
	import asyncio

	mod, dest, day = _bot(), _Destination(), _day()
	asyncio.run(mod.send_day_report(dest, day))
	assert len(dest.sends) == 1
	sent = dest.sends[0]
	assert not sent.get("embeds")
	assert sent["view"].to_components() == day.post["components"]
	assert sent["view"].has_components_v2()
	assert [f.filename for f in sent["files"]] == [day.png_name]


def test_the_classic_path_is_unchanged_when_the_layout_is_forced():
	import asyncio

	mod, dest, day = _bot(alert_grades.LAYOUT_CLASSIC), _Destination(), _day()
	asyncio.run(mod.send_day_report(dest, day))
	assert dest.sends and all("embeds" in s for s in dest.sends)
	last = dest.sends[-1]
	assert last["view"].to_components() == alert_grades.day_action_rows(day)
	assert not last["view"].has_components_v2()
