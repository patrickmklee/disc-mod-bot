# Handoff: alert-grades report through the mod bot (Hasselhoff)

Written 2026-09-04 for the session that implements this in `discord_mod_bot`.
Untracked on purpose; commit it with the work. Tracking issue: this repo #2
(phases, acceptance, decide-by dates). Rendering side: pytrade-bot #452;
grading follow-ups and the live-mode data decision: pytrade-bot #453. Read `CLAUDE.md` and
`docs/slash-migration.md` in this repo first; they define the conventions
(hybrid commands, the `_gate` pattern, pure modules with no discord.py, `uv`).

## Why this bot, and the one rule

Two bots exist. `discord_bot.py` in pytrade-bot (Tick-Tock Crocodile,
`DISCORD_BOT_TOKEN`) is a listener in partner-controlled scanner channels; it
cannot post and must never grow a send path. This bot (Hasselhoff,
`DISCORD_MOD_BOT_TOKEN`) has full control of the operator's own channels. Every
outbound or interactive piece of the report lives here.

Target channels, both operator-owned:

| env | channel | id |
|---|---|---|
| development (default) | `#test` | 1507130942488313998 |
| production | `#general` | 1493353808900784251 |

Never default to production. A run posts to production only when the config
says so explicitly.

## What already exists (do not rebuild)

pytrade-bot (PR #437 and PR #450 merged; the `run.json` manifest keys below and the real-expiry fix are PR #451, `fix/alert-grading-real-expiry`, until it merges)
produces, every trading morning, a ledger outside both repos:

```
~/pytrade-signal-grades/days/<YYYY-MM-DD>/
  grades.json       one record per alert (verdict v2: direction, payoff, risk,
                    erraticness, timing; erraticness block; clues block)
  discord.json      webhook-shaped {"username", "embeds": [...]}; the summary
                    embed references the chart as {"image": {"url": "attachment://tape.png"}}
  tape.png          composite image: contract path per alert + exit-template bars
  memo.md, alerts.json, disposition.json
  run.json          the poster's manifest: "png" (filename or null when no
                    contract had bars, so no chart), "partial_intraday" (true
                    when the day was graded while its session was still open:
                    not final, do not post), "verdict_version"
~/pytrade-signal-grades/rollup_scorecard.json    scorecard across all days
```

Poster rules that follow from `run.json`: skip a day whose `partial_intraday`
is true; when `png` is null post the embeds without the image rather than
failing (a day with zero contract bars is a legitimate, if poor, day). The
newest final day is the newest directory whose `run.json` has
`partial_intraday` false.

The contract between the repos is those files. Layout and every number are
pytrade-bot's job; this bot reads the files verbatim and adds delivery and
interaction. Do not compute or reformat grades here. If a field is missing for
a view you need, ask pytrade-bot to add it to `grades.json`.

Reference implementations in pytrade-bot you may copy from, not import:

- `app/options/alert_grading/discord_report.py`: `build_payload` (embed
  shape), `payload_chars` and `split_for_post` (the 6000-character message
  budget), `post_webhook` (multipart post with the PNG as `files[0]` and an
  `attachments` entry; strips `image` from embeds that carry no file).
- `docs/agents/alert-grading-routine.md`: verdict vocabulary and ledger layout.
- `docs/research/2026-09-03-premium-path-erraticness-and-leading-indicators.md`:
  what the erraticness numbers mean, if a help view needs wording.

## The design (source of truth for layout)

`~/SignalReportCardDesign/Signal Report Card.dc.html` is the operator's Claude
Design canvas, a Components V2 layout: one final post per day with one image
and four click paths, plus a live intraday variant. `DESIGN-BRIEF-verdict-v2.md`
beside it lists the verdict fields the card must show, and
`example-record-aapl-2026-09-02.json` is a real record. The design is NOT
final yet; the operator is revising it for verdict v2. Build in phases so
nothing here waits on it.

Click paths in the design, for the interaction layer:

- select menu "Drill into an alert" with one option per alert in time order;
  choosing one sends an ephemeral alert card, with previous / next buttons and
  an "All 6 exits" button that edits the card in place;
- button "Sources": ephemeral per-source scorecard for the day plus the
  rolling ledger;
- button "What do the exits mean": ephemeral explainer drawn on the day's
  cleanest tape;
- button "Raw numbers": ephemeral code block of every ledger field for the
  selected alert, plus a ledger CSV file.
- live intraday post (later phase): the bot edits its own message every five
  minutes until the close, then it becomes the final card.

## Seams in this repo (from the 2026-09-03 exploration)

- Commands are registered in `_register_commands` in `src/discord_mod_bot/bot.py`
  (around lines 283-350) as `@self.bot.hybrid_command`, each gated by
  `await self._gate(ctx, "<name>")` and delegated to a `_cmd_*` handler.
  `report` is the one to copy: it has `@app_commands.describe` and
  autocomplete backed by `AutocompleteCache` in `src/discord_mod_bot/autocomplete.py`.
- Embeds are built as plain dicts by pure modules (`report.py`) and converted
  by `_discord_embed` in `bot.py` (around line 562). That converter drops the
  `image` key; the new converter must keep it and pair it with `discord.File`.
- Config: `ModBotConfig.from_env` in `bot.py` (lines 53-84) reads `.env`;
  `config.yaml` carries per-command `enabled` / `allowed-by` gates and a
  `webhooks:` block. Command channel allowlist is `MOD_BOT_CHANNEL_IDS`; there
  is no report-destination concept yet.
- Slash sync: `_setup_hook` (lines 225-238) syncs only to `MOD_BOT_SYNC_GUILD_ID`.
  A new slash command will not appear unless that variable is set.
- There is no scheduler, no `discord.ui` usage, and no `discord.File` usage
  anywhere; all three are greenfield. The bot is not containerized and reads
  the ledger directly from disk. It is not currently running on the host;
  deploy is `uv sync && uv run discord-mod-bot`.
- Tests: `uv run pytest`, plain pytest against pure functions; discord.py is
  never mocked. Keep that split: data selection in a pure module, discord.py
  only in `bot.py` and the new views module.

## Work, in order

Phase 1 delivers a posted report today from the files that already exist.

1. **Config.** Add to `ModBotConfig.from_env`: `MOD_BOT_ALERT_GRADES_DIR`
   (default `~/pytrade-signal-grades`), `MOD_BOT_ALERT_GRADES_ENV`
   (`development` | `production`, default `development`), and the two channel
   ids as `MOD_BOT_ALERT_GRADES_CHANNEL_DEV` / `_PROD`. Add a `grade` gate to
   `config.yaml`.
2. **Pure loader** `src/discord_mod_bot/alert_grades.py`: `list_days(dir)`,
   `load_day(dir, date) -> DayReport` holding the parsed `discord.json`, the
   `grades.json` records, and the `tape.png` path; `alert_options(records)`
   for the select menu (label, description, value = record key);
   `alert_card(record) -> embed dict` for the ephemeral card; `raw_block(record)`.
   No discord.py imports. Unit-test all of it against a copied day directory
   fixture.
3. **Sender** in `bot.py`: convert each embed dict (keeping `image`), attach
   `discord.File(tape_path, filename="tape.png")` to the message that carries
   the summary embed, respect the 6000-character budget by splitting embeds
   across messages (copy `split_for_post`), and post to the channel chosen by
   `MOD_BOT_ALERT_GRADES_ENV`.
4. **One-shot poster** `python -m discord_mod_bot.post_alert_grades [date]`
   that logs in, posts, logs out. The cron line in pytrade-bot's
   `scripts/grade_alerts_daily.sh` calls it after the grader; do not add an
   in-process scheduler (repo convention: no hypothetical abstractions).
5. **`/grade <date>`** (alias `/scorecard`): copy the `report` command; date
   autocomplete from `list_days`; replies with the same payload and file.

Phase 2 adds the interaction layer once phase 1 is posting.

Ownership rule (operator, 2026-09-04): **pytrade-bot owns everything that is
rendered; this bot owns everything that is delivered or clicked.** The select
menu options, button labels, the `custom_id` scheme, every ephemeral card, the
sources card, the exits explainer, the raw block + CSV, and every image are
files pytrade-bot writes into the day directory. This bot never composes a
card; it maps a click to a file and replies with it. So step 2's
`alert_card` / `raw_block` builders are NOT built here; the pure module only
lists days, loads files, and resolves a `custom_id` or select value to a path.

Phase 2 landed 2026-09-04 (steps 6-8 below). It depends on pytrade-bot
PRs #454 (cards), #456 (images) and #457 (`post.json`), which were still
open and stacked when it was written: until they merge, a day directory has
no `cards/` and no `post.json`, and the report posts exactly as it did in
phase 1 -- without the interaction layer, by design rather than by failure.

6. **Views** `src/discord_mod_bot/views.py`: a `discord.ui.View` whose select
   and button callbacks resolve the interaction's `custom_id` / value to a
   pre-rendered file under `days/<date>/cards/` (naming and the `custom_id`
   scheme are defined by pytrade-bot in that day's `run.json` manifest), then
   reply ephemerally with it; "All 6 exits" and previous / next edit the
   ephemeral message in place with another pre-rendered file. Attach the view
   to the phase-1 post and to the `/grade` reply.
7. **Raw numbers** attaches the per-day CSV pytrade-bot already wrote.
8. **Glyph emoji**: upload the four verdict glyph PNGs from the design once to
   the server and hand the emoji ids to the operator; pytrade-bot needs them in
   its config to render the verdict line.

Phase 3, only after the design is final: switch the sender from classic embeds
to Components V2 (`IS_COMPONENTS_V2` flag, container with accent color, text
displays, separators, media gallery for the image). Note the flag disables
`content` and `embeds` on that message and cannot be removed afterwards, and
the message budget becomes 40 components. Keep phase 1 as the fallback path.

## Discord API facts that bound the work

Embeds: 10 per message, 25 fields each, 1024 characters per field value, 6000
characters across all embeds in a message. Images inside embeds: jpg, png,
webp, gif only, referenced as `attachment://<filename>` and uploaded as
`files[n]` in a multipart request. Interactive components (buttons with a
`custom_id`, select menus) send interactions only for application-owned
messages; link buttons never send interactions. Ephemeral replies are visible
only to the clicker. A bot can edit its own messages.

## Acceptance

- `uv run pytest` green, including new tests for the loader and card builders.
- `python -m discord_mod_bot.post_alert_grades 2026-09-02` posts to `#test`
  with the image attached, and returns non-zero if the day directory or the
  PNG is missing.
- `/grade 2026-09-02` in `#test` returns the same content; unknown dates fail
  ephemerally with the list of available days.
- Nothing posts to `#general` unless `MOD_BOT_ALERT_GRADES_ENV=production`.
- No change to pytrade-bot except the cron wrapper calling the poster.

## Open items for the operator

- The design revision for verdict v2 (`DESIGN-BRIEF-verdict-v2.md`) decides
  the phase-3 layout; phases 1 and 2 do not wait for it.
- `MOD_BOT_SYNC_GUILD_ID` must be set for the new slash command to appear.
- The bot is not running on the host; starting it is the operator's call.
