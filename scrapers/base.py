import hashlib
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


from .constants import BROAD_LOCATION_NAMES


@dataclass
class RawListing:
    id: str
    source: str
    url: str
    price: int
    bedrooms: int
    bathrooms: int
    floor: Optional[int]
    neighborhood: str
    address: str
    photos: list[str] = field(default_factory=list)
    nice_to_haves: dict = field(default_factory=dict)
    sqm: Optional[float] = None  # area in m² — None means unknown
    # Real listing identity — load-bearing for map pins, card titles, and
    # bidirectional trace between Telegram and the HTML cockpit.
    title: str = ""  # e.g. "Depto 2 rec en Polanco con balcón"
    latitude: Optional[float] = None   # decimal degrees, WGS84
    longitude: Optional[float] = None  # decimal degrees, WGS84

    @property
    def address_hash(self) -> str:
        """Deterministic hash of price + address for deduplication.

        Same price + address from different sources will produce identical hash,
        enabling robust cross-source deduplication.
        """
        key = f"{self.price}:{self.address.lower().strip()}"
        return hashlib.md5(key.encode()).hexdigest()

    def to_db_dict(self) -> dict:
        """Convert RawListing to database insert dict.

        Returns only the keys allowed by db._ALLOWED_COLUMNS.
        Vision scoring fields (vision_score, vision_flags) and alerted flag
        are set later by the pipeline, not here.
        """
        now = datetime.now(timezone.utc).isoformat()
        return {
            "id": self.id,
            "source": self.source,
            "url": self.url,
            "price": self.price,
            "bedrooms": self.bedrooms,
            "bathrooms": self.bathrooms,
            "floor": self.floor,
            "sqm": self.sqm,
            "neighborhood": self.neighborhood,
            "address": self.address,
            "address_hash": self.address_hash,
            "title": self.title,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "photos_json": json.dumps(self.photos),
            "nice_to_haves": json.dumps(self.nice_to_haves),
            "first_seen": now,
            "last_seen": now,
            "dead_checks": 0,
        }


# Shared Firecrawl extraction schema — all adapters use this
LISTING_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "listings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "external_id": {
                        "type": "string",
                        "description": "Listing ID or unique identifier from the site",
                    },
                    "url": {
                        "type": "string",
                        "description": "Full URL to the listing detail page",
                    },
                    "price_mxn": {
                        "type": "number",
                        "description": "Monthly rent in Mexican pesos (MXN)",
                    },
                    "bedrooms": {
                        "type": "number",
                        "description": "Number of bedrooms (recamaras)",
                    },
                    "bathrooms": {
                        "type": "number",
                        "description": "Number of bathrooms (banos)",
                    },
                    "floor": {
                        "type": "number",
                        "description": "Floor number. Null if not mentioned.",
                    },
                    "neighborhood": {
                        "type": "string",
                        "description": "Neighborhood or colonia name (e.g. Polanco, Condesa)",
                    },
                    "address": {"type": "string", "description": "Street address"},
                    "photo_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "URLs of listing photos",
                    },
                    "furnished": {
                        "type": "boolean",
                        "description": "true if explicitly listed as amueblado (furnished)",
                    },
                    "has_washer_dryer": {
                        "type": "boolean",
                        "description": "true if washer/dryer (lavadora/secadora) included",
                    },
                    "has_fridge": {
                        "type": "boolean",
                        "description": "true if refrigerator (refrigerador) included",
                    },
                },
                "required": ["url", "price_mxn"],
            },
        }
    },
    "required": ["listings"],
}

EXTRACT_PROMPT = (
    "Extract all apartment rental listings shown on this page. "
    "For each listing capture: any unique ID from the site, the listing URL, "
    "monthly rent in Mexican pesos (MXN), number of bedrooms (recamaras), "
    "number of bathrooms (banos), floor number if mentioned, "
    "neighborhood/colonia name, street address, all photo URLs, "
    "and whether it is furnished (amueblado), includes washer/dryer (lavadora), "
    "or includes refrigerator (refrigerador). "
    "Only extract apartments for rent. Skip listings without a price."
)


class BaseScraper(ABC):
    """Abstract base class for all listing scrapers.

    Concrete implementations must override fetch_listings() and _search_urls().
    T2 Firecrawl extraction is handled here via _try_firecrawl() + _parse_extract().
    T1 JSON API is adapter-specific via _try_json_api() + _parse().
    """

    SOURCE_NAME: str = ""  # Set in each subclass

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def fetch_listings(self) -> list[RawListing]:
        """Return raw listings from this source.

        Implementations should try methods in priority order:
        T1 (JSON API) -> T2 (Firecrawl) -> T3 (Lightpanda).
        """
        ...

    def _search_urls(self) -> list[str]:
        """Return search page URLs for this source.

        Used by shared _try_firecrawl(). Override in each adapter.
        """
        raise NotImplementedError

    def _try_json_api(self) -> list[RawListing]:
        """Tier 1: Fastest, most reliable — direct API calls."""
        raise NotImplementedError

    def _try_firecrawl(self) -> list[RawListing]:
        """Tier 2: Firecrawl scrape() + JSON-LD/HTML parsing.

        Uses scrape() (which bypasses Cloudflare) instead of extract() which
        has its own internal pipeline that returns empty for most MX RE sites.
        Parses JSON-LD blocks from the rendered HTML for structured listing data.
        """
        import re
        from firecrawl import Firecrawl  # noqa: PLC0415

        from ai_providers import credential
        api_key = credential("firecrawl")
        if not api_key:
            raise RuntimeError(
                f"[{self.SOURCE_NAME}] No Firecrawl API key configured"
            )
        app = Firecrawl(api_key=api_key)
        results: list[RawListing] = []
        for url in self._search_urls():
            try:
                doc = app.scrape(url, formats=["html"], only_main_content=False)
                html = (
                    doc.html
                    if hasattr(doc, "html")
                    else (doc.get("html", "") if isinstance(doc, dict) else "")
                )
                if not html:
                    continue
                # Parse JSON-LD blocks for structured listing data
                blocks = re.findall(
                    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                    html,
                    re.DOTALL | re.IGNORECASE,
                )
                for block in blocks:
                    try:
                        data = json.loads(block.strip())
                    except (json.JSONDecodeError, ValueError):
                        continue
                    items = self._flatten_jsonld_items(data)
                    results.extend(self._parse_extract(items))
            except Exception as e:
                self._source_error = True
                print(f"[{self.SOURCE_NAME}] Firecrawl scrape failed")
                continue
        return results

    def _flatten_jsonld_items(self, data) -> list[dict]:
        """Flatten any JSON-LD structure into a list of item dicts."""
        if isinstance(data, list):
            out = []
            for d in data:
                out.extend(self._flatten_jsonld_items(d))
            return out
        if not isinstance(data, dict):
            return []
        t = data.get("@type", "")
        if t in ("Apartment", "House", "Product", "RealEstateListing"):
            return [data]
        if t == "ItemList":
            return [
                e.get("item", e)
                for e in data.get("itemListElement", [])
                if isinstance(e, dict)
            ]
        return []

    def _parse_extract(
        self, items: list, fallback_hood: Optional[str] = None
    ) -> list[RawListing]:
        """Convert JSON-LD / Firecrawl extract items to RawListing objects.

        Handles both schema.org/Apartment JSON-LD field names and the
        LISTING_EXTRACT_SCHEMA field names for compatibility.
        Shared logic — adapters don't need to override this.
        """
        results = []
        for item in items:
            try:
                url = item.get("url", "") or item.get("@id", "")
                ext_id = str(item.get("external_id") or url)
                listing_id = (
                    f"{self.SOURCE_NAME}:{hashlib.md5(ext_id.encode()).hexdigest()[:8]}"
                )
                # Price: try schema.org offer, then direct price_mxn, then skip
                price = _extract_price(item)
                if not price:
                    continue
                # Bedrooms: schema.org uses numberOfBedrooms/numberOfRooms
                bedrooms = int(
                    item.get("bedrooms")
                    or item.get("numberOfBedrooms")
                    or item.get("numberOfRooms")
                    or 0
                )
                bathrooms = int(
                    item.get("bathrooms") or item.get("numberOfBathroomsTotal") or 0
                )
                floor_raw = item.get("floor") or item.get("floorLevel")
                floor = int(floor_raw) if floor_raw is not None else None
                # Address: schema.org uses nested address object
                addr = item.get("address", "")
                if isinstance(addr, dict):
                    address = addr.get("streetAddress", "") or addr.get("name", "")
                    neighborhood = item.get("neighborhood", "") or addr.get(
                        "addressLocality", ""
                    )
                else:
                    address = str(addr)
                    neighborhood = item.get("neighborhood", "")

                # Use fallback if neighborhood is missing or too broad (e.g. 'Ciudad de Mexico')
                if fallback_hood and (
                    not neighborhood or neighborhood.lower() in BROAD_LOCATION_NAMES
                ):
                    neighborhood = fallback_hood
                # Photos
                photos = item.get("photo_urls") or []
                img = item.get("image")
                if img and not photos:
                    if isinstance(img, str):
                        photos = [img]
                    elif isinstance(img, list):
                        photos = [
                            i
                            if isinstance(i, str)
                            else (i.get("url", "") if isinstance(i, dict) else "")
                            for i in img
                        ]
                photos = [p for p in photos if p]
                # Title + geo from schema.org — optional, null-safe downstream.
                title = str(item.get("name") or "").strip()[:200]
                latitude, longitude = _extract_geo_from_jsonld(item)
                results.append(
                    RawListing(
                        id=listing_id,
                        source=self.SOURCE_NAME,
                        url=url,
                        price=price,
                        bedrooms=bedrooms,
                        bathrooms=bathrooms,
                        floor=floor,
                        neighborhood=neighborhood,
                        address=address,
                        photos=photos,
                        title=title,
                        latitude=latitude,
                        longitude=longitude,
                        nice_to_haves={
                            "washer_dryer": bool(item.get("has_washer_dryer")),
                            "fridge": bool(item.get("has_fridge")),
                        },
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return results

    def _try_lightpanda(self) -> list[RawListing]:
        """Tier 3: Fallback browser-based crawling with Lightpanda."""
        raise NotImplementedError


def _extract_coords_from_detail(url: str, client) -> Optional[tuple[float, float]]:
    """Fetch a listing detail page and extract precise (lat, lng).

    Strategy (most-precise first):
    1. JSON-LD ``geo.latitude`` / ``geo.longitude`` from any
       ``<script type="application/ld+json">`` block.
    2. Regex patterns: ``"latitude":N,"longitude":N`` |
       ``lat=N&lng=N`` | ``google.maps.LatLng(N,N)`` | ``"lat":N,"lng":N``.

    All hits are sanity-gated against a CDMX bounding box
    (lat 18–20.5, lng -100.5 to -98.5). Returns None on any failure
    so callers can keep the listing's neighborhood-level fallback.

    ``client`` is any object exposing ``.get(url, timeout=..., follow_redirects=...)``
    that returns an ``httpx.Response``-like object with ``.status_code`` and ``.text``.
    """
    import json as _json
    import re as _re

    try:
        resp = client.get(url, timeout=8.0, follow_redirects=True)
        if resp.status_code != 200:
            return None
        html = resp.text
    except Exception:
        return None

    # 1. JSON-LD geo block
    for m in _re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        _re.DOTALL | _re.IGNORECASE,
    ):
        try:
            data = _json.loads(m.group(1))
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                geo = item.get("geo")
                if not geo:
                    addr = item.get("address")
                    if isinstance(addr, dict):
                        geo = addr.get("geo")
                if isinstance(geo, dict) and geo.get("latitude") and geo.get("longitude"):
                    try:
                        return float(geo["latitude"]), float(geo["longitude"])
                    except (TypeError, ValueError):
                        continue
        except (ValueError, TypeError, AttributeError):
            continue

    # 2. Regex patterns with CDMX bbox sanity gate
    _CDMX_LAT = (18.0, 20.5)
    _CDMX_LNG = (-100.5, -98.5)
    for pat in [
        r'"latitude"\s*:\s*(-?\d+\.\d+)\s*,\s*"longitude"\s*:\s*(-?\d+\.\d+)',
        r'lat\s*=\s*(-?\d+\.\d+)[&,;]\s*(?:lng|lon)\s*=\s*(-?\d+\.\d+)',
        r'google\.maps\.LatLng\(\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)\s*\)',
        r'"lat"\s*:\s*(-?\d+\.\d+)\s*,\s*"(?:lng|lon)"\s*:\s*(-?\d+\.\d+)',
    ]:
        hit = _re.search(pat, html)
        if hit:
            try:
                lat, lng = float(hit.group(1)), float(hit.group(2))
                if (
                    _CDMX_LAT[0] <= lat <= _CDMX_LAT[1]
                    and _CDMX_LNG[0] <= lng <= _CDMX_LNG[1]
                ):
                    return lat, lng
            except (ValueError, IndexError):
                continue

    return None


def _extract_geo_from_jsonld(item: dict) -> tuple[Optional[float], Optional[float]]:
    """Extract (lat, lng) from a schema.org JSON-LD item.

    Accepts the nested ``geo: {latitude, longitude}`` form used by
    RealEstateListing / Apartment, and the flat GeoCoordinates shape.
    Returns (None, None) on absence or malformed values so callers can
    distinguish "unknown" (fall back to centroid) from "known but bad".
    """
    geo = item.get("geo")
    if isinstance(geo, dict):
        lat = geo.get("latitude") or geo.get("lat")
        lng = geo.get("longitude") or geo.get("lng") or geo.get("lon")
    else:
        lat, lng = item.get("latitude"), item.get("longitude")
    try:
        lat_f = float(lat) if lat is not None else None
        lng_f = float(lng) if lng is not None else None
    except (TypeError, ValueError):
        return None, None
    # CDMX + suburbs bounding box sanity gate.
    if lat_f is not None and lng_f is not None:
        if not (18.5 <= lat_f <= 20.5 and -100.5 <= lng_f <= -98.0):
            return None, None
    return lat_f, lng_f


def _extract_price(item: dict) -> int:
    """Extract price in MXN from a schema.org or Firecrawl extract item dict.

    Tries multiple field paths: price_mxn (extract schema), offers.price
    (schema.org), and a raw 'price' field.
    """
    # Direct field from extract schema
    raw = item.get("price_mxn")
    if raw:
        try:
            return int(float(str(raw).replace(",", "")))
        except ValueError:
            pass
    # schema.org offers object
    offers = item.get("offers")
    if isinstance(offers, dict):
        raw = offers.get("price")
        if raw:
            try:
                return int(float(str(raw).replace(",", "")))
            except ValueError:
                pass
    elif isinstance(offers, list) and offers:
        raw = offers[0].get("price")
        if raw:
            try:
                return int(float(str(raw).replace(",", "")))
            except ValueError:
                pass
    # Generic price field
    raw = item.get("price")
    if raw:
        try:
            val = int(float(str(raw).replace(",", "")))
            if 5_000 <= val <= 200_000:
                return val
        except ValueError:
            pass
    return 0
