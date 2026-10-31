import json
import os
import httpx
from datetime import datetime, date, timezone

from scrapers.constants import NON_CDMX_MARKERS as _NON_CDMX_URL_MARKERS



def filter_cdmx(listings: list[dict]) -> list[dict]:
    """Drop listings whose URL contains a known non-CDMX city marker.

    Second gate after the scraper-level check — catches stale DB entries
    that were inserted before the scraper fix was deployed.

    Args:
        listings: Dicts with at least an 'url' key.

    Returns:
        Subset with non-CDMX listings removed.
    """
    clean = []
    for listing in listings:
        url_lower = listing.get("url", "").lower()
        dropped = next((m for m in _NON_CDMX_URL_MARKERS if m in url_lower), None)
        if dropped:
            print(f"[notify] cdmx_filter: dropped {listing.get('id', '?')} ({dropped} in URL)")
        else:
            clean.append(listing)
    return clean


def format_card(listing: dict, rank: int) -> str:
    nth = json.loads(listing.get("nice_to_haves") or "{}")
    washer = "🧺 Washer/dryer" if nth.get("washer_dryer") else ""
    fridge = "🧊 Fridge" if nth.get("fridge") else ""
    extras = "  ".join(x for x in [washer, fridge] if x) or "No extras detected"
    score = listing.get("vision_score") or 0
    lid = listing["id"]
    beds = listing.get("bedrooms") or 0
    baths = listing.get("bathrooms") or 0
    beds_str = f"{beds}bd" if beds else "?bd"
    baths_str = f"{baths}ba" if baths else "?ba"
    sqm = listing.get("sqm")
    sqm_str = f" · {sqm:.0f}m²" if sqm else ""
    # ✨ NEW! badge for same-day discoveries
    first_seen = listing.get("first_seen", "")
    try:
        seen_date = datetime.fromisoformat(first_seen).date()
        new_badge = "✨ NEW!  " if seen_date >= date.today() else ""
    except (ValueError, TypeError):
        new_badge = ""
    floor_val = listing.get("floor")
    floor_str = f"Floor {floor_val} \u00b7 " if floor_val is not None else ""
    source_str = listing.get("source", "")
    return (
        f"\U0001f3e0 #{rank} \u2014 {new_badge}{listing.get('neighborhood', '')} | ${listing['price']:,} MXN\n"
        f"\U0001f4cd {floor_str}{source_str}\n"
        f"\u2705 {beds_str} / {baths_str}{sqm_str} \u00b7 unfurnished\n"
        f"\U0001f441 Vision: {score:.1f}/10\n"
        f"{extras}\n"
        f"\U0001f517 {listing.get('url', '')}\n\n"
        f"\U0001f44d /up_{lid}   \U0001f44e /down_{lid}\n"
        f"\U0001f4ac Reply to this card (or send a voice note) to leave feedback"
    )


def validate_urls(listings: list[dict], conn=None) -> list[dict]:
    """HEAD-check listing URLs and flag unreachable ones.

    Drops listings that return 4xx/5xx or fail to connect.
    Increments dead_checks in DB on failure. Listings with dead_checks >= 3
    are skipped without a network request.

    Args:
        listings: List of listing dicts (must have 'id' and 'url' keys).
        conn: Optional sqlite3.Connection for dead_checks tracking.

    Returns:
        Subset of listings with reachable URLs.
    """
    valid = []
    for listing in listings:
        listing_id = listing["id"]
        url = listing.get("url", "")

        # Check permanent skip threshold
        dead_checks = 0
        if conn:
            row = conn.execute(
                "SELECT dead_checks FROM listings WHERE id=?", (listing_id,)
            ).fetchone()
            dead_checks = (row[0] or 0) if row else 0

        if dead_checks >= 3:
            print(f"[notify] permanent_skip: {listing_id}")
            continue

        try:
            resp = httpx.head(url, timeout=2.0, follow_redirects=True)
            code = resp.status_code
            if code < 400:
                valid.append(listing)
                continue
            if code in (403, 405):
                # Server rejected HEAD — retry with GET + browser UA to
                # distinguish bot-protection-on-HEAD from a genuinely dead page.
                try:
                    get_resp = httpx.get(
                        url, timeout=6.0, follow_redirects=True,
                        headers={"User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"
                        )},
                    )
                    if get_resp.status_code < 400:
                        valid.append(listing)
                        continue
                except Exception:
                    pass
                raise ValueError(f"HTTP {code} (HEAD) and GET also failed")
            raise ValueError(f"HTTP {code}")
        except Exception as e:
            print(f"[notify] dead_link: {listing_id} {url} ({e})")
            if conn:
                conn.execute(
                    "UPDATE listings SET dead_checks = COALESCE(dead_checks, 0) + 1 "
                    "WHERE id=?",
                    (listing_id,),
                )
                conn.commit()

    return valid
