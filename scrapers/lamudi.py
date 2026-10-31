import json
import re
import time

import httpx

from .base import (
    BaseScraper,
    RawListing,
    _extract_coords_from_detail,
    _extract_geo_from_jsonld,
)
from .httpx_adapter import HttpxAdapter
from .constants import BROAD_LOCATION_NAMES

_FURNISHED_AMENITY_TOKENS = ("furnished", "amueblado", "amoblado", "amueblada")


def _parse_floor_size(raw) -> float | None:
    """schema.org floorSize is usually QuantitativeValue {value, unitCode} or a bare number."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        raw = raw.get("value")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _parse_floor_level(raw) -> int | None:
    """floorLevel can be int, numeric string, or words like 'PB' (skip)."""
    if raw is None:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _amenity_says_furnished(amenities) -> bool:
    """Return True iff a furnished-named feature with truthy value is present."""
    if not isinstance(amenities, list):
        return False
    for feat in amenities:
        if not isinstance(feat, dict):
            continue
        name = str(feat.get("name") or "").lower()
        if not any(tok in name for tok in _FURNISHED_AMENITY_TOKENS):
            continue
        value = feat.get("value", True)
        if value is True or (isinstance(value, str) and value.lower() in ("true", "yes", "si", "sí")):
            return True
    return False

# Neighborhood slugs confirmed working (re-probed 2026-04-15).
# Each tuple: (borough, slug, display_name)
# slug=None → delegation-level URL (no neighborhood segment in path)
_NEIGHBORHOODS = [
    ("miguel-hidalgo", "polanco-7",           "Polanco"),
    ("miguel-hidalgo", "ampliacion-popo",     "Amp Polanco"),
    ("cuauhtemoc",     "roma-norte",          "Roma Norte"),
    ("cuauhtemoc",     "roma-sur",            "Roma Sur"),
    ("cuauhtemoc",     "condesa",             "Condesa"),
    ("cuauhtemoc",     "hipodromo",           "Hipódromo"),       # was hipodromo-condesa → 404
    ("cuauhtemoc",     "juarez",              "Juárez"),           # was juarez-col → 404
    ("cuauhtemoc",     None,                  "Cuauhtémoc"),       # no slug, delegation-level
    ("miguel-hidalgo", "lomas-de-chapultepec", "Lomas de Chapultepec"),
    ("miguel-hidalgo", "bosques-de-las-lomas", "Bosques de las Lomas"),
    ("miguel-hidalgo", "granada",             "Amp Granada"),
    ("miguel-hidalgo", None,                  "Miguel Hidalgo"),  # no slug, delegation-level
]

_BASE = "https://www.lamudi.com.mx/distrito-federal/{borough}/{slug}/departamento/departamentos/for-rent/"
_BASE_DELEGATION = "https://www.lamudi.com.mx/distrito-federal/{borough}/departamento/departamentos/for-rent/"

# Estado de México neighborhoods (different state path)
_BASE_EDOMEX = "https://www.lamudi.com.mx/estado-de-mexico/{borough}/{slug}/departamento/departamentos/for-rent/"
_BASE_EDOMEX_DELEGATION = "https://www.lamudi.com.mx/estado-de-mexico/{borough}/departamento/departamentos/for-rent/"
_NEIGHBORHOODS_EDOMEX = [
    ("naucalpan-de-juarez", "naucalpan", "Naucalpan"),  # was naucalpan-de-juarez → 404
]

# Price patterns to try on detail page HTML (Lamudi shows price as "$ 120,000 MXN")
_PRICE_PATTERNS = [
    r"\$\s*([\d]{2,3},[\d]{3}(?:\.\d{2})?)\s*MXN",  # "$ 120,000 MXN" or "$ 30,000 MXN"
    r'"price"\s*:\s*["\']?([\d,]+(?:\.\d+)?)["\']?',  # JSON price field
    r'data-price=["\'](\d+)["\']',  # data-price attribute
    r'itemprop=["\']price["\'][^>]*content=["\']([^"\']+)["\']',  # microdata
    r"([\d]{2,3},[\d]{3})\s*(?:MXN|pesos)\b",  # generic MXN amount
    r"(?:renta|alquiler|arrendamiento)[^$\d]*([\d]{2,3},[\d]{3})",  # "renta 30,000"
]


class LamudiScraper(BaseScraper):
    SOURCE_NAME = "lamudi"

    def __init__(self, config: dict):
        super().__init__(config)
        self._adapter = HttpxAdapter(
            timeout=20.0,
            source_name=self.SOURCE_NAME,
            rotate_ua=True,
            use_curl_cffi_on_403=True,
        )

    def fetch_listings(self) -> list[RawListing]:
        try:
            return self._try_json_api()
        except Exception as e:
            print(f"[lamudi] T1 failed: {e} — skipping source")
            return []
        finally:
            self._adapter.close()

    def _search_urls(self) -> list[str]:
        return [
            self._build_url(borough, slug, _BASE, _BASE_DELEGATION)
            for borough, slug, _ in _NEIGHBORHOODS
        ]

    @staticmethod
    def _build_url(borough: str, slug: str | None, base: str, base_delegation: str) -> str:
        """Build a Lamudi listing URL. Uses delegation-level if slug is None."""
        if slug:
            return base.format(borough=borough, slug=slug)
        return base_delegation.format(borough=borough)

    def _try_json_api(self) -> list[RawListing]:
        """T1: httpx + JSON-LD parsing. Confirmed to work by XHR probe."""
        c = self.config["criteria"]
        all_listings: list[RawListing] = []
        _all_hoods = [
            (borough, slug, neigh_name, False)
            for borough, slug, neigh_name in _NEIGHBORHOODS
        ] + [
            (borough, slug, neigh_name, True)
            for borough, slug, neigh_name in _NEIGHBORHOODS_EDOMEX
            if neigh_name in c.get("neighborhoods", []) or not c.get("neighborhoods")
        ]
        for borough, slug, neigh_name, is_edomex in _all_hoods:
            if is_edomex:
                url = self._build_url(borough, slug, _BASE_EDOMEX, _BASE_EDOMEX_DELEGATION)
            else:
                url = self._build_url(borough, slug, _BASE, _BASE_DELEGATION)
            try:
                html = self._adapter.fetch_html(url)
                if not html:
                    print(f"[lamudi] {neigh_name}: no content")
                    continue
                raw = self._parse_jsonld(html, neigh_name)
                # Filter by bedrooms before fetching detail pages (save HTTP calls)
                raw = [r for r in raw if r.bedrooms == 0 or r.bedrooms == c["bedrooms"]]
                # Fetch prices from detail pages (cap at 20 per neighborhood).
                # W3-T5 RC#19: while on the detail page, also backfill precise
                # coords when JSON-LD carried only neighborhood-level geo.
                for listing in raw[:20]:
                    price = self._fetch_detail_price(listing.url)
                    if price:
                        lat, lng = listing.latitude, listing.longitude
                        if lat is None or lng is None:
                            coords = self._fetch_detail_coords(listing.url)
                            if coords:
                                lat, lng = coords
                        all_listings.append(
                            RawListing(
                                id=listing.id,
                                source=listing.source,
                                url=listing.url,
                                price=price,
                                bedrooms=listing.bedrooms,
                                bathrooms=listing.bathrooms,
                                floor=listing.floor,
                                neighborhood=listing.neighborhood,
                                address=listing.address,
                                photos=listing.photos,
                                sqm=listing.sqm,
                                title=listing.title,
                                latitude=lat,
                                longitude=lng,
                                nice_to_haves=listing.nice_to_haves,
                            )
                        )
                    time.sleep(0.3)
            except Exception as e:
                print(f"[lamudi] {slug}: {e}")
                continue
        return all_listings

    def _parse_jsonld(self, html: str, neighborhood: str) -> list[RawListing]:
        """Parse schema.org/Apartment items from Lamudi's JSON-LD structure.

        Lamudi embeds listings inside:
        data[0]["@graph"][0]["mainEntity"][0]["itemListElement"][n]["item"]
        """
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
            items = self._extract_apartment_items(data)
            for item in items:
                listing = self._jsonld_to_listing(item, neighborhood)
                if listing:
                    results.append(listing)
        return results

    def _extract_apartment_items(self, data) -> list[dict]:
        """Walk Lamudi's nested JSON-LD to find Apartment items.

        Structure: list → dict[@graph] → SearchResultsPage → mainEntity
                   → ItemList → itemListElement → ListItem.item → Apartment
        """
        apartment_items = []
        top_list = data if isinstance(data, list) else [data]
        for top in top_list:
            if not isinstance(top, dict):
                continue
            for graph_item in top.get("@graph", [top]):
                if not isinstance(graph_item, dict):
                    continue
                main = graph_item.get("mainEntity", [])
                if isinstance(main, dict):
                    main = [main]
                for entity in main:
                    if not isinstance(entity, dict):
                        continue
                    for list_item in entity.get("itemListElement", []):
                        if not isinstance(list_item, dict):
                            continue
                        item = list_item.get("item", list_item)
                        if isinstance(item, dict) and item.get("@type") in (
                            "Apartment",
                            "House",
                            "Product",
                        ):
                            apartment_items.append(item)
        return apartment_items

    def _jsonld_to_listing(self, item: dict, neighborhood: str):
        """Convert a single schema.org/Apartment dict to a stub RawListing (no price yet)."""
        url = item.get("url", "") or item.get("@id", "")
        if not url:
            return None
        name = item.get("name", "")
        listing_id = f"lamudi:{_url_hash(url)}"
        # Bedrooms: numberOfRooms or numberOfBedrooms
        bedrooms = int(item.get("numberOfBedrooms") or item.get("numberOfRooms") or 0)
        # Address
        addr = item.get("address", {})
        if isinstance(addr, str):
            address = addr
        else:
            address = addr.get("streetAddress", "") or addr.get("name", "")
        neigh = (
            addr.get("addressLocality", "") if isinstance(addr, dict) else ""
        ) or neighborhood

        # Use fallback if extracted neighborhood is too broad
        if neigh and str(neigh).lower() in BROAD_LOCATION_NAMES:
            neigh = neighborhood

        # Photos
        photos = []
        img = item.get("image")
        if isinstance(img, str):
            photos = [img]
        elif isinstance(img, list):
            photos = [i if isinstance(i, str) else i.get("url", "") for i in img]

        sqm = _parse_floor_size(item.get("floorSize"))
        bathrooms = int(item.get("numberOfBathroomsTotal") or item.get("numberOfBathrooms") or 0)
        floor = _parse_floor_level(item.get("floorLevel"))
        latitude, longitude = _extract_geo_from_jsonld(item)
        furnished = _amenity_says_furnished(item.get("amenityFeature"))

        return RawListing(
            id=listing_id,
            source="lamudi",
            url=url,
            price=0,  # price filled in by _fetch_detail_price
            bedrooms=bedrooms,
            bathrooms=bathrooms,
            floor=floor,
            neighborhood=neigh,
            address=address,
            photos=[p for p in photos if p],
            sqm=sqm,
            title=str(name or "").strip()[:200],
            latitude=latitude,
            longitude=longitude,
            nice_to_haves={"furnished": furnished},
        )

    def _fetch_detail_coords(self, url: str):
        """Fetch detail page and extract precise coords via shared utility.

        Uses the adapter's underlying httpx client where possible, otherwise
        a one-shot ``httpx.Client``. Best-effort — returns None on failure.
        """
        if not url:
            return None

        adapter = self._adapter

        class _AdapterClient:
            def __init__(self, ad):
                self._ad = ad

            def get(self, u, **kw):
                inner = getattr(self._ad, "client", None)
                if inner is not None:
                    return inner.get(u, **kw)
                with httpx.Client(timeout=kw.get("timeout", 8.0)) as c:
                    return c.get(u, **kw)

        try:
            return _extract_coords_from_detail(url, _AdapterClient(adapter))
        except Exception:
            return None

    def _fetch_detail_price(self, url: str) -> int:
        """Fetch listing detail page and regex-parse the price in MXN."""
        if not url:
            return 0
        try:
            html = self._adapter.fetch_html(url)
            if not html:
                return 0
            for pattern in _PRICE_PATTERNS:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    raw = match.group(1).replace(",", "").replace(".", "")
                    price = int(float(raw))
                    if 5_000 <= price <= 200_000:  # sanity range for MXN rent
                        return price
            return 0
        except Exception:
            return 0


def _url_hash(url: str) -> str:
    import hashlib

    return hashlib.md5(url.encode()).hexdigest()[:10]
