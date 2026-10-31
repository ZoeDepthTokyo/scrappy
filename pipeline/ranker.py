import json

from taste import TAGS


def _infer_tags(listing: dict) -> set[str]:
    """Infer taste taxonomy tags from vision_flags stored in listing dict."""
    try:
        flags = json.loads(listing.get("vision_flags") or "[]")
    except (json.JSONDecodeError, TypeError):
        flags = []
    flag_set = set(f.lower() for f in flags)
    tags: set[str] = set()
    # Direct flag → tag mappings
    if "dark" in flag_set or "low_light" in flag_set:
        tags.add("dark")
    if "floor_risk" in flag_set:
        tags.add("ground_floor_vibe")
    if "noise" in flag_set or "street_noise" in flag_set:
        tags.add("noisy_area")
    if "good_natural_light" in flag_set or "great_light" in flag_set:
        tags.add("great_light")
    # Allow flags that match tag names directly
    tags |= flag_set & TAGS
    return tags


def rank(scored_listings: list[dict], taste_profile=None) -> list[dict]:
    """Sort listings by composite score: vision (60%) + nice-to-haves (20%) + price efficiency (20%).

    When taste_profile is provided, applies tag-based adjustments (+-0.15 per occurrence,
    capped at +-3.0 total) after the base formula.

    Args:
        scored_listings: List of listing dicts with vision_score, nice_to_haves, and price.
        taste_profile: Optional TasteProfile instance; its ranker_weights() method returns
            a {tag: float} dict used to adjust scores based on matched vision_flags tags.

    Returns:
        Same listings sorted by composite score descending.
    """
    weights = taste_profile.ranker_weights() if taste_profile else {}

    def score(listing: dict) -> float:
        vision = (listing.get("vision_score") or 0) * 0.6
        nth = json.loads(listing.get("nice_to_haves") or "{}")
        nice = min(len([v for v in nth.values() if v]), 3) / 3 * 10 * 0.2
        price_eff = max(0, (45000 - listing["price"]) / 15000) * 10 * 0.2
        base = vision + nice + price_eff

        if weights:
            listing_tags = _infer_tags(listing)
            adjustment = sum(weights.get(tag, 0.0) for tag in listing_tags)
            adjustment = max(-3.0, min(3.0, adjustment))
            return base + adjustment
        return base

    return sorted(scored_listings, key=score, reverse=True)
