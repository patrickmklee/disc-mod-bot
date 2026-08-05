# `/signals` command — design

- **Status:** proposed (awaiting implementation plan)
- **Date:** 2026-06-06
- **Scope:** new read-only operator command listing the signals the bot
  ingested today (or a chosen session), with per-signal contract detail and
  the REJECTED/TRADED outcome.

## Summary

`/signals` shows every signal the running trading bot received in a window
(default: today), one block per signal:

- time received (rendered in ET)
- source: scanner name (+ Discord author label when present) and channel
- ATM contract (OCC) and its premium at alert time
- OTM contract (OCC) and its premium at alert time
- underlying price at alert time
- outcome: TRADED or REJECTED (+ reject reason)
- extra context: final score / tier, greeks + spread at alert, R:R / target /
  position id

It is a hybrid command (prefix `!signals` + slash `/signals`) and is
admin-gated, like `/report`.

## Why this shape

The mod bot is a **pure webhook proxy** — it never touches a database
directly (see `discord_mod_bot/CLAUDE.md`). Reading `options.db` from the bot
would couple it to the DB path/schema and break when the bot runs on a
different host than the trading server. So `/signals` mirrors `/trades`:

1. The **trading server** (`pytrade-bot`) exposes a new `GET /signals` HTTP
   endpoint that queries `options.db`.
2. The **mod bot** proxies that endpoint and renders the JSON as a Discord
   embed via a new pure, unit-tested `signals.py` module (no discord.py),
   exactly like `report.py`.

Alternatives rejected:

- *Mod bot reads `options.db` directly* — violates the proxy separation;
  host-coupled; bypasses the auth token the server already enforces.
- *Overload `/trades`* — `/trades` returns closed positions; signals are a
  different table and shape (most signals never become trades).

## Data source

The single source of truth is **`options.db` → `signals`** (written live by
`execute_signal()` via `app/options/db.py`). It is the only table that is
(a) written in real time and (b) carries both the alert-time contract data
*and* the gate outcome.

`signals.db → signal_embeds` is **not** used: it is populated by backfill /
A-B replay scripts (`scripts/backfill_signal_embeds.py`, `SignalEmbedDB`),
not the live path, and it lacks the trade outcome.

### Field mapping (all from one `signals` row)

| Display | Column(s) | Notes |
|---|---|---|
| Time received | `received_at` | stored UTC ISO; rendered ET |
| Source | `scanner_name` | primary label; `author` shown when non-empty |
| Channel | `channel` | derived from `scanner_name` when blank (see below) |
| Direction | `direction` | call / put |
| ATM contract | `ticker`, `contract_expire`‖`expire_date`, `direction`, `strike` | OCC built server-side |
| ATM premium | `mid_at_alert` | `-` when 0/absent |
| OTM contract | `ticker`, expiry, `direction`, `otm_strike` | OCC; line omitted when `otm_strike == 0` |
| OTM premium | `otm_premium` | |
| Underlying @ alert | `underlying_at_signal` | falls back to `current_price` when 0 |
| Outcome | `pos_id`, `reject_stage` | `pos_id` non-empty → TRADED; else REJECTED |
| Reject reason | `reject_stage`, `reject_reason` | shown for REJECTED |
| Score | `final_score` | tier **derived** by bucketing (70-79 / 80-89 / 90-100); `signals` has no `score_tier` column (that lives on `trades`) |
| Greeks / spread | `delta_at_alert`, `iv_at_alert`, `spread_pct` | IV rendered as a percentage; builder normalizes (×100 when stored as a fraction) |
| Targets / link | `rr_ratio`, `target_price`, `pos_id` | |

**Channel fallback** (when `channel` is blank, ~25% of rows): map
`scanner_name` via the lane rule already encoded in the `signal_lane` view —
`tinker`, `neverland_pan`, `captain_hook` → `#neverland`; `shrek`, `donkey`,
`lord_farquaad` → `#swamp`; anything else → `` (omit).

**OCC format** (standard OPRA, 21 chars): `ROOT + YYMMDD + C/P + strike*1000`
zero-padded to 8 digits, e.g. `SPY260605C00741000`. Built in the server
endpoint so the contract identity travels with the data.

**Filtered out:** simulated rows (`is_simulated = 1`). Homer-lane signals
(`signals_homer`) are **out of scope** for v1 — only the v2.2-lane `signals`
table is shown.

## Server side (`pytrade-bot`)

### `OptionsDB.get_signals_log(...)`

New method on `OptionsDB` (mirrors the existing trade-log getter), returning a
list of plain dicts:

```
get_signals_log(
    *, start_iso: str, end_iso: str,
    source: str | None = None, ticker: str | None = None,
    limit: int = 200,
) -> list[dict]
```

- Filters: `received_at >= start_iso AND received_at < end_iso`,
  `is_simulated = 0`, optional `scanner_name = source`,
  optional `ticker = upper(ticker)`.
- Orders newest-first, caps at `limit` (hard max 500).
- Returns the mapped/enriched fields above, including the built `atm_occ` /
  `otm_occ`, resolved `outcome` + `channel`, and the derived `score_tier`.
- The day **summary** (received / traded / rejected counts) is computed by the
  mod bot from the returned list, not by the endpoint. On a fallback, the
  handler filters the week response to the latest populated day before
  building, so the summary reflects only that session.

### `GET /signals` (in `app/options/receiver.py`)

Query params (all optional):

- `date=YYYY-MM-DD` — single session (UTC day bounds).
- `period=today|week` — relative window; ignored if `date` is set.
- `source=<scanner_name>` — exact scanner filter.
- `ticker=<symbol>` — exact ticker filter.
- `limit=<int>` — default 200, capped 500.

Default window (no `date`/`period`): **today**. The endpoint returns the
requested window verbatim — the *fallback-to-last-session* behavior lives in
the mod bot (it re-requests `period=week` and picks the most recent populated
day), so the server stays a thin query.

Response:

```json
{
  "signals": [
    {
      "received_at": "2026-06-05T18:28:00.746590+00:00",
      "scanner_name": "tinker",
      "author": "",
      "channel": "#neverland",
      "ticker": "SPY",
      "direction": "call",
      "atm_strike": 741.0,
      "atm_occ": "SPY260605C00741000",
      "atm_premium": 1.15,
      "otm_strike": 742.0,
      "otm_occ": "SPY260605C00742000",
      "otm_premium": 0.72,
      "underlying": 740.96,
      "outcome": "TRADED",
      "reject_stage": "",
      "reject_reason": "",
      "pos_id": "POS-20260605183304-6768",
      "final_score": 78.0,
      "score_tier": "70-79",
      "delta": 0.52,
      "iv": 0.142,
      "spread_pct": 0.031,
      "rr_ratio": 2.1,
      "target_price": 743.5
    }
  ],
  "count": 1
}
```

Auth: inherits the existing `Authorization: Bearer` middleware. No new state,
no writes — read-only.

## Mod bot side (`discord_mod_bot`)

### `signals.py` (new, pure — no discord.py)

Mirrors `report.py`. Responsibilities and key functions:

- `resolve_signal_window(period, date, *, now=...) -> Window` — same shape and
  `now=`-injection pattern as `report.resolve_window` (testable without
  clock drift). Returns UTC bounds + a human label (`Fri Jun 5`).
- `to_et(received_at_iso) -> "HH:MM"` — UTC→`America/New_York` via `zoneinfo`,
  DST-aware.
- `build_signals_embed(signals, *, window, is_fallback, filters_label,
  source_url) -> dict` — assembles the embed payload (plain dict, like the
  report builder). One embed field per signal; a summary header field on top;
  a `+N more` field when capped.
- Internal formatters for the field name line and the indented value block.

Volume handling: cap at **22** signal fields (Discord allows 25; reserve room
for the summary + `+N more`). Newest-first. When capped, the `+N more` field
says how many were hidden and suggests narrowing with `source=`/`ticker=`/
`date=`.

### Layout (rendered)

```
Signals — Fri Jun 5 (ET)              6 received · 2 traded · 4 rejected
=======================================================================
14:28 · tinker · #neverland · SPY call · score 78 (70-79) · ✅ TRADED
   ATM  SPY260605C00741000   $1.15     Δ0.52 · IV 14.2% · spr 3.1%
   OTM  SPY260605C00742000   $0.72
   und. $740.96   ·   R:R 2.1  tgt $743.50   ·   POS-20260605183304-6768

15:34 · tinker · #neverland · SPY call · score 71 (70-79) · ❌ REJECTED
   ATM  SPY260605C00738000   $2.10     Δ0.49 · IV 14.8% · spr 2.8%
   OTM  SPY260605C00739000   $0.67
   und. $737.43   ·   reason: validator (duplicate)
```

- The header line of each block is the embed field **name**; the indented
  lines are the field **value**.
- Outcome uses ✅ / ❌; greeks use Δ. (These are runtime embed strings, not
  Python identifiers, so the repo "no unicode in source" rule does not
  apply.) `und.` (not `undr.`) for underlying.
- OTM line omitted when there is no OTM strike. Any absent numeric renders as
  `-`. `author` appended to source only when non-empty:
  `tinker (Tinker 🛎#0000)`.
- Embed color: green when any TRADED, neutral grey when all rejected, grey
  when empty (reusing the existing `COLOR_*` constants).

### `bot.py` wiring

- New hybrid command `signals` with describe/args: `period`
  (`Literal["today","week"]` default `today`), `date: Optional[str]`,
  `source: Optional[str]`, `ticker: Optional[str]`.
- Handler `_cmd_signals` (thin: gate → fetch → fallback → build → send),
  callable directly in tests like the other `_cmd_*`.
- Fallback: request the window; if `today` yields zero rows and no explicit
  `date`/`period` was given, re-request `period=week`, pick the most recent
  populated day, set `is_fallback=True` for the header note.
- Autocomplete for `source` and `ticker` backed by `/signals` (see below).

### Autocomplete (`autocomplete.py`)

Add two accessors to `AutocompleteCache`:

- `sources()` → distinct `scanner_name` from `/signals?period=week`.
- `signal_tickers()` → distinct `ticker` from `/signals?period=week`.

Both reuse the existing `_cached` TTL pattern and degrade to last-good on
upstream error (same as `channels()`).

### `config.yaml`

Add (admin-gated, matching `report`; required because a missing entry
silently defaults to enabled/members-only):

```yaml
  signals:
    enabled: true
    allowed-by: admin
```

## Edge cases

- **Today empty** (weekends / pre-open) → fallback to most recent populated
  session, header note `(no signals today — showing last session: Fri Jun 5)`.
- **No signals at all in window** → single "No signals in <window>." embed.
- **Server unreachable / HTTP error** → same error-embed path as
  `/positions` (`Could not reach /signals: ...`).
- **Missing OTM** (`otm_strike == 0`) → omit the OTM line.
- **Blank author** → show scanner name only.
- **Blank channel** → derive from scanner; if still unknown, omit channel.
- **Truncation** → cap fields; embed name/value length capped via the
  existing `_truncate` helpers.

## Limitations (stated, not silent)

- A signal is recorded only if the **running bot ingested it**. Signals that
  arrive while the bot/receiver is down are not logged anywhere (the webhook
  POST fails), so `/signals` cannot show them. "Received today" means
  "ingested by the bot today." This is the intended meaning of the
  REJECTED/TRADED-depends-on-the-bot caveat.
- Homer-lane signals are excluded in v1.

## Testing

- **`tests/test_discord_mod_bot_signals.py`** (new, pure-function, bare
  `assert`): window resolution with injected `now=`; ET conversion incl. a DST
  boundary; OCC build (call/put, fractional strike, padding); outcome
  resolution; channel fallback; OTM omission; empty/fallback rendering; the
  22-field cap + `+N more`.
- **Server:** one `OptionsDB.get_signals_log` test against a `tmp_path` DB
  seeded with a traded + a rejected + a simulated row, asserting the simulated
  row is filtered and the dicts carry `atm_occ`/`outcome`/`channel`.
- Run `uv run pytest` in the mod bot; the relevant server suite in the main
  repo for the db method.

## File-change summary

Trading server (`pytrade-bot`):
- `app/options/db.py` — add `get_signals_log()` (+ OCC / outcome / channel
  enrichment helpers).
- `app/options/receiver.py` — add `GET /signals` route.

Mod bot (`discord_mod_bot`):
- `src/discord_mod_bot/signals.py` — new pure builder module.
- `src/discord_mod_bot/bot.py` — command + `_cmd_signals` + autocomplete wiring.
- `src/discord_mod_bot/autocomplete.py` — `sources()`, `signal_tickers()`.
- `src/discord_mod_bot/config.yaml` — `signals` command entry.
- `tests/test_discord_mod_bot_signals.py` — new tests.
