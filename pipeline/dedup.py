from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from scrapers.base import RawListing

_TRACKING_QUERY_KEYS = frozenset(
    {
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "mkt_tok",
        "ref",
        "ref_src",
        "spm",
        "trk",
        "trkcampaign",
    }
)


def _normalize_url(url: str) -> str:
    """Canonicalize listing URLs for dedup keys.

    Keeps the semantic location while dropping common marketing noise:
    fragments, tracking params, default ``www.`` host prefix, and a trailing
    slash on non-root paths.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_QUERY_KEYS
    ]
    query = urlencode(sorted(query_pairs))
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def deduplicate(
    listings: list[RawListing],
    *,
    known_urls: set[str] | None = None,
    known_hashes: set[str] | None = None,
) -> list[RawListing]:
    """Remove duplicate listings using two independent keys (first-seen wins).

    Gate 1 — URL: exact URL match. Catches same-source repeated queries
    (e.g. Lamudi delegation-level + neighborhood query returning the same URL).

    Gate 2 — address_hash (MD5 of price:address): catches cross-source
    duplicates where the same property is listed on multiple portals.

    When ``known_urls``/``known_hashes`` are supplied (typically the full set
    already stored in the listings DB), the gates also reject listings that
    match any prior run. Cross-run gate prevents paid vision scoring and
    duplicate Telegram alerts when a listing resurfaces with a new scraper id.
    """
    seen_urls: set[str] = {
        normalized for url in (known_urls or ()) if (normalized := _normalize_url(url))
    }
    seen_hashes: set[str] = set(known_hashes or ())
    seen_geo: set[str] = set()
    out: list[RawListing] = []
    for listing in listings:
        url_key = _normalize_url(listing.url)
        if url_key and url_key in seen_urls:
            continue
        if listing.address_hash in seen_hashes:
            continue
        # Gate 3: same price + rounded coordinates = same building (within-run only)
        if listing.latitude is not None and listing.longitude is not None:
            geo_key = (
                f"{listing.price}:"
                f"{round(listing.latitude, 4)}:"
                f"{round(listing.longitude, 4)}"
            )
            if geo_key in seen_geo:
                continue
            seen_geo.add(geo_key)
        if url_key:
            seen_urls.add(url_key)
        seen_hashes.add(listing.address_hash)
        out.append(listing)
    return out
