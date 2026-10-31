import json
import os
from notify import format_card


def test_format_card_contains_price():
    listing = {
        "id": "i24:1",
        "price": 32000,
        "neighborhood": "Polanco",
        "floor": 5,
        "bedrooms": 2,
        "bathrooms": 2,
        "vision_score": 8.2,
        "url": "http://x.com",
        "nice_to_haves": '{"washer_dryer": true, "fridge": false}',
        "vision_flags": '["good_natural_light"]',
    }
    card = format_card(listing, rank=1)
    assert "32,000" in card
    assert "Polanco" in card
    assert "/up_i24:1" in card
    assert "/down_i24:1" in card


def test_format_card_suppresses_floor_when_none():
    from notify import format_card
    listing = {
        "id": "t:1", "neighborhood": "Polanco", "price": 38000,
        "floor": None, "source": "inmuebles24", "bedrooms": 2, "bathrooms": 2,
        "sqm": None, "vision_score": 7.0, "nice_to_haves": "{}",
        "url": "http://x.com/1", "first_seen": "2026-04-16T00:00:00",
    }
    card = format_card(listing, rank=1)
    assert "None" not in card
    assert "Floor" not in card


def test_format_card_shows_floor_when_known():
    from notify import format_card
    listing = {
        "id": "t:2", "neighborhood": "Condesa", "price": 36000,
        "floor": 5, "source": "lamudi", "bedrooms": 2, "bathrooms": 2,
        "sqm": 75.0, "vision_score": 8.0, "nice_to_haves": "{}",
        "url": "http://x.com/2", "first_seen": "2026-04-16T00:00:00",
    }
    card = format_card(listing, rank=2)
    assert "Floor 5" in card


