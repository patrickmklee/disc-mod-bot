from discord_mod_bot.bot import (
    COLOR_GAIN,
    COLOR_LOSS,
    EMBED_FIELD_LIMIT,
    MAX_POSITION_FIELDS,
    POSITION_FIELD_VALUE_LIMIT,
    build_positions_embeds,
)


def test_build_positions_embed_summarizes_empty_status():
    embeds = build_positions_embeds(
        {
            "running": True,
            "open_positions": 0,
            "balance": 1000.0,
            "daily_pnl": 0.0,
        },
        source_url="http://127.0.0.1:5555",
    )

    embed = embeds[0]
    field_values = "\n".join(field["value"] for field in embed["fields"])

    assert embed["title"] == "Portfolio Positions"
    assert "`0` open positions" in embed["description"]
    assert embed["url"] == "http://127.0.0.1:5555/positions"
    assert "No open positions right now." in field_values


def test_build_positions_embed_formats_position_card():
    payload = {
        "running": True,
        "open_positions": 1,
        "balance": 2500,
        "daily_pnl": 42.75,
        "daily_wins": 2,
        "daily_losses": 1,
        "total_trades": 3,
        "positions": [
            {
                "pos_id": "P1",
                "ticker": "SPY",
                "direction": "call",
                "strike": 500,
                "contracts": 2,
                "status": "open",
                "entry_premium": 1.20,
                "target": 510.00,
                "peak_premium": 1.62,
                "current_stop": 1.30,
                "underlying_price": 503.21,
                "underlying_at_entry": 501.00,
                "phase": "initial",
            }
        ],
    }

    embed = build_positions_embeds(payload)[0]
    position_field = embed["fields"][3]

    assert embed["color"] == COLOR_GAIN
    assert position_field["name"] == "SPY CALL 500 | P1"
    assert "Contracts `2` | Status `Open`" in position_field["value"]
    assert "Entry `$1.20` -> Target `$510.00` (+1.8%)" in position_field["value"]
    assert "Peak `$1.62` | Stop `$1.30`" in position_field["value"]
    assert "Underlying `$503.21` from `$501.00`" in position_field["value"]


def test_build_positions_embed_uses_loss_accent_and_accessible_label():
    embed = build_positions_embeds({"daily_pnl": -12.34, "positions": []})[0]
    daily_field = embed["fields"][1]

    assert embed["color"] == COLOR_LOSS
    assert daily_field["value"] == "Loss -$12.34"


def test_build_positions_embed_caps_fields_and_truncates_values():
    positions = [
        {
            "pos_id": f"P{i}",
            "ticker": "SPY",
            "direction": "call",
            "strike": 500,
            "status": "open",
            "phase": "x" * 2000,
        }
        for i in range(MAX_POSITION_FIELDS + 4)
    ]

    embed = build_positions_embeds({"open_positions": len(positions), "positions": positions})[0]

    assert len(embed["fields"]) <= EMBED_FIELD_LIMIT
    assert len(embed["fields"][3]["value"]) <= POSITION_FIELD_VALUE_LIMIT
    assert embed["fields"][-1]["name"] == "More positions"
    assert "additional position(s)" in embed["fields"][-1]["value"]
