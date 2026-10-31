import unicodedata

from scrapers.base import RawListing
from scrapers.constants import NON_CDMX_MARKERS


def _normalize_neighborhood(value: str | None) -> str:
    """Normalize user-visible neighborhood labels for exact-allowlist checks."""
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value.strip().casefold())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _point_in_polygon(lat: float, lng: float, polygon: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon test (Jordan curve theorem)."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i]
        yj, xj = polygon[j]
        if ((yi > lat) != (yj > lat)) and (
            lng < (xj - xi) * (lat - yi) / (yj - yi) + xi
        ):
            inside = not inside
        j = i
    return inside


def hard_filter(listings: list[RawListing], config: dict) -> list[RawListing]:
    """Filter listings against hard criteria.

    Rejects listings that fail any of:
    - neighborhoods: must be in the allowed list (case-insensitive, if list non-empty)
    - require_photos: must have at least one photo
    - min_floor: floor must be >= min_floor (None floor passes — unknown)
    - price_min/price_max: price must be in range
    - min_bedrooms: at least this many bedrooms (0 = unknown, passes through)
    - bathrooms: if known, must be >= configured minimum (0 = unknown, passes)
    - furnished=False: reject listings explicitly marked as furnished
    - min_sqm: reject listings with known area < threshold (None = unknown, passes)
    """
    c = config["criteria"]
    min_beds = c.get("bedrooms", 2)
    min_baths = c.get("bathrooms", 0)  # 0 = no minimum enforced
    min_sqm = c.get("min_sqm", 60)

    # Build lowercase neighborhood set for O(1) lookup (empty = allow all)
    allowed_hoods = {
        normalized
        for name in c.get("neighborhoods", [])
        if (normalized := _normalize_neighborhood(name))
    }

    out = []
    for l in listings:
        # CDMX URL check: reject listings whose URL contains a non-CDMX city marker.
        # Runs before DB insert so non-CDMX listings never consume vision credits or storage.
        url_lower = l.url.lower()
        if any(m in url_lower for m in NON_CDMX_MARKERS):
            continue
        # Neighborhood: must be in allowed list (None = unknown = reject when list is set)
        listing_hood = _normalize_neighborhood(l.neighborhood)
        if allowed_hoods and (not listing_hood or listing_hood not in allowed_hoods):
            continue
        # Photos required
        if c.get("require_photos") and not l.photos:
            continue
        # Floor: only reject if we know it and it's below minimum
        if l.floor is not None and l.floor < c.get("min_floor", 3):
            continue
        # Price range
        if l.price < c["price_min"] or l.price > c["price_max"]:
            continue
        # Bedrooms: 0 = unknown (passes). Known must be >= configured minimum.
        if l.bedrooms != 0 and l.bedrooms < min_beds:
            continue
        # Bathrooms: 0 = unknown (passes). Known must be >= configured minimum
        if min_baths > 0 and l.bathrooms != 0 and l.bathrooms < min_baths:
            continue
        # Furnished: reject explicitly furnished listings
        if c.get("furnished") is False and l.nice_to_haves.get("furnished"):
            continue
        # Area: only reject if we know sqm and it's below threshold
        if l.sqm is not None and l.sqm < min_sqm:
            continue
        # Geo filter: reject listings whose precise coords fall outside the polygon
        # (listings with no coords pass through — benefit of doubt).
        geo_filter = c.get("geo_filter")
        if geo_filter and geo_filter.get("type") == "polygon":
            coords = geo_filter.get("coords", [])
            if (coords
                    and l.latitude is not None
                    and l.longitude is not None
                    and not _point_in_polygon(l.latitude, l.longitude, coords)):
                continue
        out.append(l)
    return out
