"""MercadoLibre Inmuebles scraper — replaces dead MetrosCubicos.

Confirmed working: 48 RealEstateListing JSON-LD items per page with
price, photo, URL, and description (2026-04-05 XHR probe).
Bedrooms/floor extracted via regex from listing description.
"""

import json
import re

import httpx
from .base import BaseScraper, RawListing

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "es-MX,es;q=0.9",
}

# Neighborhood slugs confirmed working (404 = not a valid ML slug)
_NEIGHBORHOODS = [
    ("polanco", "Polanco"),
    ("ampliacion-polanco", "Amp Polanco"),
    ("la-condesa", "Condesa"),
    ("hipodromo-condesa", "Hipódromo"),
    ("roma-norte", "Roma Norte"),
    # Bare slugs route to wrong Mexican states — must qualify with ciudad-de-mexico/
    ("ciudad-de-mexico/roma-sur", "Roma Sur"),
    ("juarez-ciudad-de-mexico", "Juárez"),
    ("cuauhtemoc", "Cuauhtémoc"),
    ("ampliacion-granada", "Amp Granada"),
    ("ciudad-de-mexico/lomas-de-chapultepec", "Lomas de Chapultepec"),
]

_SEARCH_URL = "https://inmuebles.mercadolibre.com.mx/departamentos/renta/{slug}/"

# URL slug → canonical neighborhood map. Ordered most-specific-first so e.g.
# "ampliacion-polanco" matches before the looser "polanco" slug.
# MercadoLibre frequently widens query results (a 'cuauhtemoc' query can return
# a Lomas-de-Sotelo listing), so the URL slug is a more trustworthy neighborhood
# signal than the query bucket. When the URL matches a known off-target slug,
# the listing gets an honest label that hard_filter then rejects because it's
# not in config's neighborhoods allow-list.
_URL_NEIGHBORHOOD_MAP: list[tuple[str, str]] = [
    # Target (in-scope) neighborhoods
    ("ampliacion-polanco", "Amp Polanco"),
    ("polanco-ii-seccion", "Polanco II"),
    ("polanco", "Polanco"),
    ("hipodromo-condesa", "Hipódromo"),
    ("hipodromo", "Hipódromo"),
    ("la-condesa", "Condesa"),
    ("condesa", "Condesa"),
    ("roma-norte", "Roma Norte"),
    ("roma-sur", "Roma Sur"),
    ("juarez-ciudad-de-mexico", "Juárez"),
    ("colonia-juarez", "Juárez"),
    ("ampliacion-granada", "Amp Granada"),
    ("granada", "Amp Granada"),
    ("lomas-de-chapultepec", "Lomas de Chapultepec"),
    ("bosques-de-las-lomas", "Bosques de las Lomas"),
    ("cuauhtemoc", "Cuauhtémoc"),
    # Known adjacent / off-target slugs that leak into results.
    # Labelled honestly so hard_filter drops them (not in allow-list).
    ("lomas-de-sotelo", "Lomas de Sotelo"),
    ("san-miguel-chapultepec", "San Miguel Chapultepec"),
    ("anzures", "Anzures"),
    ("escandon", "Escandón"),
]


def _infer_neighborhood(url: str, text: str, fallback: str) -> tuple[str, bool]:
    """Re-derive neighborhood from URL slug / description.

    Returns (neighborhood, retagged) where retagged=True when the URL-derived
    label disagrees with the query-bucket fallback. Retagged listings are
    either out-of-scope (dropped later) or simply mislabelled in the bucket.
    """
    haystack = url.lower()
    for slug, label in _URL_NEIGHBORHOOD_MAP:
        if slug in haystack:
            return label, (label != fallback)
    return fallback, False


# Regex for bedroom count in listing description
_BEDS_RE = re.compile(r"(\d+)\s*rec[aá]mara", re.IGNORECASE)
_FLOOR_RE = re.compile(r"(?:piso|nivel|planta)\s*(\d+)", re.IGNORECASE)
_FURNISHED_RE = re.compile(r"\bamueblad[ao]\b", re.IGNORECASE)
_WASHER_RE = re.compile(
    r"\b(?:lavadora|cuarto\s+de\s+lavado|área\s+de\s+lavado)\b", re.IGNORECASE
)
_FRIDGE_RE = re.compile(r"\b(?:refrigerador|nevera|heladera)\b", re.IGNORECASE)
# Studio/loft detection — mark as 1 bed so the 2-bed minimum filter rejects them
_STUDIO_RE = re.compile(r"\b(?:loft|estudio|studio|monoambiente)\b", re.IGNORECASE)
# Area extraction — "X m²" or "X metros cuadrados"
_SQM_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*m[²2]|\b(\d+)\s*metros?\s*cuadrados?", re.IGNORECASE
)


class MercadoLibreScraper(BaseScraper):
    SOURCE_NAME = "mercadolibre"

    def fetch_listings(self) -> list[RawListing]:
        try:
            return self._try_json_api()
        except Exception as e:
            print(f"[mercadolibre] T1 failed: {e} — trying Firecrawl")
            try:
                return self._try_firecrawl()
            except Exception as e2:
                self._source_error = True
                print("[mercadolibre] Firecrawl failed — skipping source")
                return []

    def _search_urls(self) -> list[str]:
        """Used by base._try_firecrawl() as fallback."""
        c = self.config["criteria"]
        price_filter = f"?price={c['price_min']}-{c['price_max']}"
        return [
            _SEARCH_URL.format(slug=slug) + price_filter for slug, _ in _NEIGHBORHOODS
        ]

    def _try_json_api(self) -> list[RawListing]:
        """T1: httpx + RealEstateListing JSON-LD.

        MercadoLibre Inmuebles embeds 48 RealEstateListing items per search page
        in a @graph JSON-LD block — price, photo, URL, description included.
        """
        c = self.config["criteria"]
        price_min, price_max = c["price_min"], c["price_max"]
        results: list[RawListing] = []

        for slug, neigh_name in _NEIGHBORHOODS:
            url = f"{_SEARCH_URL.format(slug=slug)}?price={price_min}-{price_max}"
            try:
                resp = httpx.get(
                    url, headers=HEADERS, timeout=20, follow_redirects=True
                )
                if resp.status_code != 200:
                    print(f"[mercadolibre] {slug}: HTTP {resp.status_code}")
                    continue
                listings = self._parse_jsonld(resp.text, neigh_name)
                results.extend(listings)
                print(f"[mercadolibre] {slug}: {len(listings)} listings")
            except Exception as e:
                print(f"[mercadolibre] {slug}: {e}")
                continue

        return results

    def _parse_jsonld(self, html: str, neighborhood: str) -> list[RawListing]:
        """Parse @graph JSON-LD with RealEstateListing items."""
        blocks = re.findall(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        results = []
        for block in blocks:
            try:
                data = json.loads(block.strip())
            except json.JSONDecodeError:
                continue
            graph = data.get("@graph", []) if isinstance(data, dict) else []
            for item in graph:
                if item.get("@type") != "RealEstateListing":
                    continue
                listing = self._item_to_listing(item, neighborhood)
                if listing:
                    results.append(listing)
        return results

    def _item_to_listing(self, item: dict, neighborhood: str) -> RawListing | None:
        """Convert a RealEstateListing @graph item to RawListing."""
        offers = item.get("offers", {})
        price = offers.get("price")
        if not price:
            return None
        try:
            price = int(price)
        except (TypeError, ValueError):
            return None

        url = offers.get("url", "")
        if not url:
            return None

        name = item.get("name", "")
        description = item.get("description", "")
        text = f"{name} {description}"

        # Extract bedrooms from description text
        beds_match = _BEDS_RE.search(text)
        if beds_match:
            bedrooms = int(beds_match.group(1))
        elif _STUDIO_RE.search(text):
            bedrooms = 1  # studio/loft — will be rejected by 2-bed minimum
        else:
            bedrooms = 0  # unknown

        # Extract floor from description text
        floor_match = _FLOOR_RE.search(text)
        floor = int(floor_match.group(1)) if floor_match else None

        # Extract nice-to-haves from description
        furnished = bool(_FURNISHED_RE.search(text))
        has_washer = bool(_WASHER_RE.search(text))
        has_fridge = bool(_FRIDGE_RE.search(text))

        # Extract area in m² from description
        sqm: float | None = None
        sqm_match = _SQM_RE.search(text)
        if sqm_match:
            raw_sqm = sqm_match.group(1) or sqm_match.group(2)
            try:
                sqm = float(raw_sqm)
            except (TypeError, ValueError):
                pass

        # Photos: ML provides one image in JSON-LD; detail page has gallery
        photos = []
        img = item.get("image", "")
        if img:
            photos = [img.replace("-C.jpg", "-O.jpg")]  # -O = original size

        listing_id = f"ml:{_url_hash(url)}"
        # Re-derive neighborhood from URL/text — query bucket is only a prior.
        resolved_neighborhood, retagged = _infer_neighborhood(url, text, neighborhood)

        # Geo from schema.org RealEstateListing — MercadoLibre sometimes ships
        # a nested `geo: {latitude, longitude}` or an `address: {streetAddress}`.
        # When present this is the only reliable source of per-listing coords;
        # neighborhood centroid is a fallback, not the truth.
        latitude, longitude = _extract_geo(item)

        # Address for persistence: prefer structured streetAddress, then name.
        raw_addr = item.get("address")
        address_str = ""
        if isinstance(raw_addr, dict):
            address_str = (raw_addr.get("streetAddress") or "").strip()
        elif isinstance(raw_addr, str):
            address_str = raw_addr.strip()

        return RawListing(
            id=listing_id,
            source="mercadolibre",
            url=url,
            price=price,
            bedrooms=bedrooms,
            bathrooms=0,  # not in JSON-LD — filter.py treats 0 as unknown
            floor=floor,
            neighborhood=resolved_neighborhood,
            address=address_str,
            photos=photos,
            sqm=sqm,
            title=(name or "").strip()[:200],
            latitude=latitude,
            longitude=longitude,
            nice_to_haves={
                "washer_dryer": has_washer,
                "fridge": has_fridge,
                "furnished": furnished,
                "wrong_bucket": retagged,
            },
        )


def _url_hash(url: str) -> str:
    import hashlib

    return hashlib.md5(url.encode()).hexdigest()[:10]


def _extract_geo(item: dict) -> tuple[float | None, float | None]:
    """Extract (lat, lng) from a schema.org item.

    Supports both the nested ``geo: {latitude, longitude}`` form used by
    RealEstateListing and the flat ``{"@type": "GeoCoordinates", ...}`` shape.
    Returns (None, None) when geo is absent or malformed — the caller treats
    that as "unknown" and the map falls back to neighborhood centroid.
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
    # Sanity gate: CDMX-ish bounding box. Reject obvious garbage but allow a
    # wide buffer (EdoMex suburbs included). Real geocoding outside this gate
    # is almost certainly a scraper bug; we'd rather fall back than mislead.
    if lat_f is not None and lng_f is not None:
        if not (18.5 <= lat_f <= 20.5 and -100.5 <= lng_f <= -98.0):
            return None, None
    return lat_f, lng_f
