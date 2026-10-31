import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import json
from unittest.mock import MagicMock
from pipeline.ranker import rank

BASE = {
    "price": 35000,
    "nice_to_haves": "{}",
    "vision_flags": "[]",
}


def _listing(lid, vision_score, flags=None):
    return {
        **BASE,
        "id": lid,
        "vision_score": vision_score,
        "vision_flags": json.dumps(flags or []),
    }


def _mock_profile(weights):
    p = MagicMock()
    p.ranker_weights.return_value = weights
    return p


def test_rank_without_profile_unchanged():
    listings = [_listing("a", 8.0), _listing("b", 6.0)]
    result = rank(listings)
    assert result[0]["id"] == "a"


def test_rank_with_profile_penalizes_matched_tag():
    """Listing with 'dark' flag should rank lower when profile penalizes 'dark'."""
    dark_listing = _listing("dark", 8.0, flags=["dark"])
    clean_listing = _listing("clean", 6.0, flags=[])
    profile = _mock_profile({"dark": -3.0})
    result = rank([dark_listing, clean_listing], taste_profile=profile)
    assert result[0]["id"] == "clean"


def test_rank_with_profile_boosts_positive_tag():
    """Listing with 'great_light' flag should rank higher when profile boosts it."""
    light_listing = _listing("light", 6.0, flags=["great_light"])
    plain_listing = _listing("plain", 8.0, flags=[])
    profile = _mock_profile({"great_light": 3.0})
    result = rank([light_listing, plain_listing], taste_profile=profile)
    assert result[0]["id"] == "light"


def test_taste_adjustment_capped_at_three():
    """Cap at ±3.0 prevents unlimited penalization.

    penalized: base=6.733, uncapped-final=2.233, capped-final=3.733
    middle:    base=2.833 (between uncapped and capped penalized)
    If cap works:  penalized(3.733) > middle(2.833) → penalized ranks first
    If cap absent: penalized(2.233) < middle(2.833) → middle ranks first
    """
    penalized = _listing("penalized", 9.0, flags=["dark", "old_building", "no_parking"])
    middle = _listing("middle", 2.5, flags=[])
    profile = _mock_profile({"dark": -1.5, "old_building": -1.5, "no_parking": -1.5})
    result = rank([penalized, middle], taste_profile=profile)
    assert result[0]["id"] == "penalized", (
        "Cap should limit penalty to -3.0, keeping penalized above middle"
    )


def test_rank_no_profile_no_crash():
    listings = [_listing("a", 7.0)]
    result = rank(listings, taste_profile=None)
    assert len(result) == 1
