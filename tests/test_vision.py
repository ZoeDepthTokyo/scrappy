from unittest.mock import patch, MagicMock
import json
from vision import VisionScorer

CONFIG = {"criteria": {"min_vision_score": 5}}

MOCK_VISION_RESULT = {
    "light_score": 8,
    "floor_risk": False,
    "street_noise_risk": False,
    "has_washer_dryer": True,
    "has_fridge": False,
    "flags": ["good_natural_light"],
    "overall_score": 8,
}


def test_score_uses_sdk_when_available():
    """When _call_sdk succeeds, score_listing returns its result without metadata fallback."""
    from scrapers.base import RawListing

    scorer = VisionScorer({**CONFIG, "ai": {"provider": "openai"}})
    listing = RawListing(
        id="i24:test", source="inmuebles24", url="https://example.com",
        price=35000, bedrooms=2, bathrooms=2, floor=5,
        neighborhood="Polanco", address="Masaryk 1",
        photos=["https://example.com/photo.jpg"],
    )
    with patch.object(scorer, "_call_sdk", return_value=MOCK_VISION_RESULT):
        result = scorer.score_listing(listing, [], [])
    assert result is not None
    assert result["light_score"] == 8
    assert "metadata_score" not in result.get("flags", [])


def test_sdk_failure_falls_back_to_metadata():
    """When _call_sdk raises, score_listing falls back to metadata scoring."""
    from scrapers.base import RawListing

    scorer = VisionScorer({**CONFIG, "ai": {"provider": "openai"}})
    listing = RawListing(
        id="i24:test", source="inmuebles24", url="https://example.com",
        price=35000, bedrooms=2, bathrooms=2, floor=5,
        neighborhood="Polanco", address="Masaryk 1",
        photos=["https://example.com/photo.jpg"],
    )
    with patch.object(scorer, "_call_sdk", side_effect=Exception("SDK offline")):
        result = scorer.score_listing(listing, [], [])
    assert result is not None
    assert "metadata_score" in result.get("flags", [])
    assert scorer._sdk_online is None


def test_hard_reject_on_floor_risk():
    scorer = VisionScorer(CONFIG)
    result = {
        "light_score": 7,
        "floor_risk": True,
        "overall_score": 7,
        "street_noise_risk": False,
        "has_washer_dryer": False,
        "has_fridge": False,
        "flags": [],
    }
    assert scorer._is_hard_reject(result) is True


def test_hard_reject_on_low_light():
    scorer = VisionScorer(CONFIG)
    result = {
        "light_score": 3,
        "floor_risk": False,
        "overall_score": 4,
        "street_noise_risk": False,
        "has_washer_dryer": False,
        "has_fridge": False,
        "flags": [],
    }
    assert scorer._is_hard_reject(result) is True


def test_passes_good_listing():
    scorer = VisionScorer(CONFIG)
    result = {
        "light_score": 8,
        "floor_risk": False,
        "overall_score": 8,
        "street_noise_risk": False,
        "has_washer_dryer": True,
        "has_fridge": False,
        "flags": ["good_natural_light"],
    }
    assert scorer._is_hard_reject(result) is False



