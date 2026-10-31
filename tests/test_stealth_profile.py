# tests/test_stealth_profile.py
from scrapers.stealth_profile import StealthProfile, PROFILE_POOL


def test_profile_pool_non_empty():
    assert len(PROFILE_POOL) >= 3


def test_profile_consistency():
    """All profiles must expose a coherent identity — UA, viewport, tz, accept-lang."""
    for p in PROFILE_POOL:
        assert p.user_agent
        assert p.viewport["width"] > 0
        assert p.viewport["height"] > 0
        assert p.timezone_id
        assert p.accept_language
        assert p.platform in {"Win32", "MacIntel", "Linux x86_64"}


def test_pick_deterministic_by_seed():
    """Same seed → same profile; different seeds can differ."""
    a1 = StealthProfile.pick(seed="user-42")
    a2 = StealthProfile.pick(seed="user-42")
    assert a1.user_agent == a2.user_agent

    b = StealthProfile.pick(seed="other")
    # Different seeds can pick the same slot by chance — this assertion is
    # probabilistic on seed pool size; relax to just "valid profile".
    assert b.user_agent in {p.user_agent for p in PROFILE_POOL}


def test_to_playwright_context_args():
    p = PROFILE_POOL[0]
    args = p.to_playwright_context_args()
    assert args["user_agent"] == p.user_agent
    assert args["viewport"] == p.viewport
    assert args["locale"].startswith("es")
    assert args["timezone_id"] == p.timezone_id
