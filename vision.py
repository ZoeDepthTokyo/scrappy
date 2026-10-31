"""Score listing photos using the selected provider or local metadata."""

import json
import httpx
import ai_providers
from scrapers.base import RawListing

SYSTEM_PROMPT = """You are scoring apartment photos for a renter in Mexico City.
They value: natural light, high floors (3rd+), quiet streets, unfurnished units.
They dislike: dark rooms, ground/2nd floor indicators, busy streets visible from windows.

Score each set of photos and return ONLY valid JSON with no markdown:
{
  "light_score": <0-10>,
  "floor_risk": <true if looks like 1st or 2nd floor or basement>,
  "street_noise_risk": <true if busy street visible>,
  "has_washer_dryer": <true if washer/dryer hookup or unit visible>,
  "has_fridge": <true if refrigerator visible>,
  "flags": [<list of notable observations as strings>],
  "overall_score": <0-10>
}"""

# Neutral fallback — used only when API and metadata scoring both fail
_DEFAULT_SCORE = {
    "light_score": 5,
    "floor_risk": False,
    "street_noise_risk": False,
    "has_washer_dryer": False,
    "has_fridge": False,
    "flags": ["vision_skipped"],
    "overall_score": 5,
}

# Source reliability bonus (0–1 added to overall_score)
_SOURCE_BONUS = {
    "inmuebles24": 0.3,  # professional listings, usually accurate
    "lamudi": 0.3,
    "pincali": 0.2,
    "mercadolibre": 0.0,  # user-posted, more noise
}


class VisionScorer:
    def __init__(self, config: dict, taste_profile=None) -> None:
        """
        Initialize VisionScorer.

        Args:
            config: Configuration dict with criteria keys.
            taste_profile: Optional TasteProfile instance. When provided and
                has_signal() is True, its addendum is appended to the system prompt.
        """
        self.config = config
        self._sdk_online: bool | None = None
        self.ai_status = "ok"
        self._taste_profile = taste_profile  # TasteProfile | None

    def _build_system_prompt(self) -> str:
        """Build system prompt, injecting taste addendum if profile has enough signal.

        Returns:
            SYSTEM_PROMPT with addendum appended when taste_profile has signal,
            otherwise bare SYSTEM_PROMPT unchanged.
        """
        if self._taste_profile is not None and self._taste_profile.has_signal():
            addendum = self._taste_profile.system_prompt_addendum()
            if addendum:
                return SYSTEM_PROMPT + "\n\n" + addendum
        return SYSTEM_PROMPT

    def score_listing(
        self, listing: RawListing, pos_ids: list[str], neg_ids: list[str]
    ) -> dict | None:
        """Score a listing's photos via API, or metadata if API is offline.

        Returns:
            dict  — vision scores (listing passes)
            None  — hard-rejected (floor_risk AND low score)
        """
        photos = listing.photos[:3]

        provider = self.config.get("ai", {}).get("provider", "none")
        if self._sdk_online is not False and photos and provider != "none":
            try:
                result = self._call_sdk(photos, pos_ids, neg_ids)
                if result is not None:
                    self._sdk_online = True
                    return None if self._is_hard_reject(result, photo_count=len(photos)) else result
            except json.JSONDecodeError:
                pass  # A malformed reply affects only this listing.
            except Exception as exc:
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                if status in (401, 403) or status == 429 or (status is not None and status >= 500):
                    self._sdk_online = False
                    self.ai_status = "reconnect" if status in (401, 403) else "degraded"
        return self._metadata_score(listing)

    def probe_and_report(self) -> dict:
        provider = self.config.get("ai", {}).get("provider", "none")
        available = provider == "none" or ai_providers.connected(provider)
        if not available:
            self._sdk_online = False
            self.ai_status = "degraded"
        return {"ok": available, "stderr": "" if available else "Provider unavailable"}

    def _call_sdk(self, photo_urls, pos_ids, neg_ids):
        settings = self.config.get("ai", {})
        prompt = self._build_system_prompt()
        if pos_ids or neg_ids:
            prompt += "\nPreviously liked listing IDs: " + ", ".join(pos_ids) + "\nPreviously disliked listing IDs: " + ", ".join(neg_ids)
        return ai_providers.score(settings.get("provider", "none"), settings.get("model", ""), prompt, photo_urls)

    def _metadata_score(self, listing: RawListing) -> dict | None:
        """Score based on listing metadata when photos can't be analyzed.

        Rewards: high floor, known sqm ≥ 60, multiple photos, reliable sources.
        Penalizes: floor 1-2 (hard reject), no photos when required.
        """
        floor = listing.floor
        photos = listing.photos
        sqm = listing.sqm
        source = listing.source

        # Hard reject: confirmed low floor
        if floor is not None and floor <= 2:
            return None

        # Build score components
        # Floor bonus: 3rd+ floors get +1.5, unknown gets +0
        floor_bonus = 1.5 if (floor is not None and floor >= 3) else 0.0

        # Photo count signal: more photos = more confident seller
        photo_bonus = min(len(photos) / 3, 1.0) * 1.5

        # Area bonus: known sqm ≥ 80 is a good sign
        sqm_bonus = 0.0
        if sqm and sqm >= 80:
            sqm_bonus = 1.0
        elif sqm and sqm >= 60:
            sqm_bonus = 0.5

        # Source reliability
        source_bonus = _SOURCE_BONUS.get(source, 0.0)

        # Base 5.0 + bonuses, capped at 8.5 (never give perfect score without vision)
        overall = min(5.0 + floor_bonus + photo_bonus + sqm_bonus + source_bonus, 8.5)

        flags = ["metadata_score"]
        if floor is not None and floor >= 3:
            flags.append(f"floor_{floor}_confirmed")
        if len(photos) >= 3:
            flags.append("photos_ok")

        return {
            "light_score": 5,
            "floor_risk": floor is not None and floor <= 2,
            "street_noise_risk": False,
            "has_washer_dryer": listing.nice_to_haves.get("washer_dryer", False),
            "has_fridge": listing.nice_to_haves.get("fridge", False),
            "flags": flags,
            "overall_score": round(overall, 1),
        }

    def _is_hard_reject(self, result: dict, photo_count: int = 2) -> bool:
        if result.get("floor_risk", False):
            return True
        # light_score < 4 is only meaningful when vision assessed actual interior shots.
        # With a single photo (common for ML JSON-LD thumbnails, often exterior), the score
        # reflects the image we have, not the apartment — fall through to metadata instead.
        return photo_count > 1 and result.get("light_score", 10) < 4


def _cli_smoke_test() -> int:
    """Ad-hoc CLI: score a tiny batch of unrated listings from scrapy.db.

    Intended for manual verification ONLY — run from a plain shell so the
    Claude Code SDK Tier 1 path picks up Max OAuth auth. Inside an agent
    session, this will fall back to API / metadata scoring (see module
    docstring for the full explanation).

    Usage:
        python vision.py            # scores up to 3 unrated listings
        python vision.py --limit 10 # scores up to 10
    """
    import argparse
    import json as _json
    from config_loader import load_config  # noqa: PLC0415
    from db import connect  # noqa: PLC0415
    from scrapers.base import RawListing  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="vision.py smoke test CLI")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--db", default="scrapy.db")
    args = parser.parse_args()

    cfg = load_config()
    conn = connect(args.db)
    rows = conn.execute(
        "SELECT id, source, url, price, bedrooms, bathrooms, floor, sqm, "
        "neighborhood, address_hash, photos_json, nice_to_haves "
        "FROM listings WHERE vision_score IS NULL LIMIT ?",
        (args.limit,),
    ).fetchall()
    if not rows:
        print("[vision-cli] no unrated listings found")
        return 0

    scorer = VisionScorer(cfg)
    for r in rows:
        (lid, source, url, price, beds, baths, floor, sqm,
         nbhd, _addrh, photos_json, nth_json) = r
        listing = RawListing(
            id=lid, source=source, url=url, price=price or 0,
            bedrooms=beds or 0, bathrooms=baths or 0, floor=floor,
            neighborhood=nbhd or "", address="",
            photos=_json.loads(photos_json) if photos_json else [],
            nice_to_haves=_json.loads(nth_json) if nth_json else {},
            sqm=sqm,
        )
        result = scorer.score_listing(listing, [], [])
        print(f"[vision-cli] {lid}: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli_smoke_test())
