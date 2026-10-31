"""Inmuebles24 scraper — Firecrawl browser rendering required.

Cloudflare blocks all httpx JSON API requests (403). Firecrawl's headless
browser bypasses the WAF and renders the full listing cards in HTML.
Confirmed 27 PROPERTY listings per page with 9 photos each (2026-04-05).

Card structure in rendered HTML:
  data-qa="posting PROPERTY"
  data-id="{navent_id}"
  data-to-posting="{relative_url}"
  postingPrices-module__price → "MN 34,000"
  postingLocations-module__location-address → street
  postingMainFeatures → bedrooms (2 amb / 2 rec)
  img src / data-flickity-lazyload → photo URLs
"""

import re
import json
import time

import httpx
from .base import BaseScraper, RawListing, _extract_coords_from_detail
from .constants import NON_CDMX_MARKERS as _NON_CDMX_MARKERS
from .httpx_adapter import HttpxAdapter

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "es-MX,es;q=0.9",
}

_BASE_SEARCH = (
    "https://www.inmuebles24.com/departamentos-en-renta-en-{slug}.html"
    "?precio-maximo={price_max}&precio-minimo={price_min}&habitaciones={bedrooms}"
)

# Neighborhood slugs — format: (url_slug, colonia_name)
# Confirmed working pattern: {colonia}-{alcaldia}-distrito-federal
_NEIGHBORHOODS = [
    ("polanco-mexico-distrito-federal", "Polanco"),
    ("polanco-ii-seccion-miguel-hidalgo-distrito-federal", "Polanco II"),
    ("polanco-iv-seccion-miguel-hidalgo-distrito-federal", "Polanco IV"),
    ("polanco-v-seccion-miguel-hidalgo-distrito-federal", "Polanco V"),
    ("ampliacion-granada-miguel-hidalgo-distrito-federal", "Amp Granada"),
    ("lomas-de-chapultepec-miguel-hidalgo-distrito-federal", "Lomas de Chapultepec"),
    ("condesa-mexico-distrito-federal", "Condesa"),
    ("hipodromo-cuauhtemoc-distrito-federal", "Hipódromo"),
    ("roma-norte-cuauhtemoc-distrito-federal", "Roma Norte"),
    ("roma-sur-cuauhtemoc-distrito-federal", "Roma Sur"),
    ("juarez-cuauhtemoc-distrito-federal", "Juárez"),
    ("cuauhtemoc-cuauhtemoc-distrito-federal", "Cuauhtémoc"),
    ("bosques-de-las-lomas-cuajimalpa-de-morelos-distrito-federal", "Bosques de las Lomas"),
    ("naucalpan-de-juarez-estado-de-mexico",                        "Naucalpan"),
]


_NAVENT_IMG = "https://img10.naventcdn.com/avisos/"


class Inmuebles24Scraper(BaseScraper):
    SOURCE_NAME = "inmuebles24"

    def fetch_listings(self) -> list[RawListing]:
        # T1: native httpx + curl_cffi (bypasses Cloudflare WAF via TLS impersonation)
        try:
            results = self._try_native_httpx()
            if results:
                return results
            print("[inmuebles24] T1 native returned 0 — falling back to Firecrawl")
        except Exception as e:
            print(f"[inmuebles24] T1 native failed: {e} — falling back to Firecrawl")
        # T2: Firecrawl headless browser (paid, last resort)
        try:
            return self._try_firecrawl_html()
        except Exception as e:
            self._source_error = True
            print("[inmuebles24] Firecrawl failed — skipping source")
            return []

    def _search_urls(self) -> list[str]:
        c = self.config["criteria"]
        return [
            _BASE_SEARCH.format(
                slug=slug,
                price_max=c["price_max"],
                price_min=c["price_min"],
                bedrooms=c["bedrooms"],
            )
            for slug, _ in _NEIGHBORHOODS
        ]

    def _try_native_httpx(self) -> list[RawListing]:
        """T1: Fetch search pages via HttpxAdapter (curl_cffi Chrome-TLS on 403).

        Inmuebles24 uses Cloudflare; curl_cffi impersonation bypasses the WAF.
        If the returned HTML lacks rendered card elements (CSR gap), returns []
        and the caller falls back to Firecrawl.
        """
        c = self.config["criteria"]
        results: list[RawListing] = []
        adapter = HttpxAdapter(
            timeout=30.0,
            source_name=self.SOURCE_NAME,
            rotate_ua=True,
            use_curl_cffi_on_403=True,
        )
        try:
            for slug, neigh_name in _NEIGHBORHOODS:
                url = _BASE_SEARCH.format(
                    slug=slug,
                    price_max=c["price_max"],
                    price_min=c["price_min"],
                    bedrooms=c["bedrooms"],
                )
                html = adapter.fetch_html(url)
                if not html:
                    continue
                listings = self._parse_property_cards(html, neigh_name)
                results.extend(listings)
                print(f"[inmuebles24] native {neigh_name}: {len(listings)} listings")
            # W3-T5 RC#19: backfill precise coords from each detail page where
            # the card-level lat/lng was empty. Reuses the same impersonating
            # adapter and respects rate-limits via a 300ms inter-request sleep.
            self._backfill_detail_coords(results, adapter)
        finally:
            adapter.close()
        return results

    def _backfill_detail_coords(self, listings: list[RawListing], adapter) -> None:
        """Fetch detail pages for listings missing coords; mutate in place.

        Uses ``_extract_coords_from_detail`` which speaks the minimal
        ``client.get(url, timeout=..., follow_redirects=...)`` protocol — the
        HttpxAdapter exposes its inner client via ``.client`` so we wrap it.
        Falls back to a fresh ``httpx.Client`` if the adapter shape changes.
        """

        class _AdapterClient:
            """Shim turning HttpxAdapter into the minimal client protocol."""

            def __init__(self, ad):
                self._ad = ad

            def get(self, url, **kw):
                inner = getattr(self._ad, "client", None)
                if inner is not None:
                    return inner.get(url, **kw)
                # Last resort: spin a one-shot client per call
                with httpx.Client(timeout=kw.get("timeout", 8.0)) as c:
                    return c.get(url, **kw)

        client = _AdapterClient(adapter)
        for listing in listings:
            if listing.latitude is not None and listing.longitude is not None:
                continue
            try:
                coords = _extract_coords_from_detail(listing.url, client)
                if coords:
                    listing.latitude, listing.longitude = coords
            except Exception:
                # Coord backfill is best-effort; never break the run.
                pass
            time.sleep(0.3)

    def _try_firecrawl_html(self) -> list[RawListing]:
        """Scrape rendered HTML with Firecrawl, parse PROPERTY listing cards."""
        from firecrawl import Firecrawl  # noqa: PLC0415

        from ai_providers import credential
        api_key = credential("firecrawl")
        if not api_key:
            raise RuntimeError("[inmuebles24] No Firecrawl API key")
        app = Firecrawl(api_key=api_key)
        c = self.config["criteria"]
        results: list[RawListing] = []

        for slug, neigh_name in _NEIGHBORHOODS:
            url = _BASE_SEARCH.format(
                slug=slug,
                price_max=c["price_max"],
                price_min=c["price_min"],
                bedrooms=c["bedrooms"],
            )
            try:
                doc = app.scrape(url, formats=["html"], wait_for=3000)
                html = doc.html if hasattr(doc, "html") else ""
                if not html:
                    continue
                listings = self._parse_property_cards(html, neigh_name)
                results.extend(listings)
                print(f"[inmuebles24] {neigh_name}: {len(listings)} listings")
            except Exception as e:
                self._source_error = True
                print("[inmuebles24] Firecrawl scrape failed")
                continue

        return results

    def _parse_property_cards(self, html: str, neighborhood: str) -> list[RawListing]:
        """Parse PROPERTY card blocks from Firecrawl-rendered Inmuebles24 HTML."""
        # Split HTML at each posting card boundary
        card_pattern = re.compile(
            r'data-qa="posting PROPERTY"\s+data-id="(\d+)"\s+data-posting-type="PROPERTY"\s+data-to-posting="([^"]+)"',
        )
        matches = list(card_pattern.finditer(html))
        results = []

        for i, m in enumerate(matches):
            listing_id_raw = m.group(1)
            url_path = m.group(2).split("?")[0]  # strip tracking params

            # Reject promoted listings from non-CDMX cities.
            # I24 injects ads from other cities into search results; their URL
            # slug contains the city name (e.g. "vallarta-norte-guadalajara").
            url_lower = url_path.lower()
            if any(marker in url_lower for marker in _NON_CDMX_MARKERS):
                continue

            card_start = m.start()
            card_end = matches[i + 1].start() if i + 1 < len(matches) else len(html)
            card_html = html[card_start:card_end]

            # Price — "MN 34,000" format
            price = self._extract_price(card_html)
            if not price:
                continue

            # Address
            addr_m = re.search(
                r"postingLocations-module__location-address[^>]*>([^<]+)", card_html
            )
            address = addr_m.group(1).strip() if addr_m else ""

            # Bedrooms from postingMainFeatures (looks like "2 amb")
            beds_m = re.search(r"(\d+)\s*(?:amb|rec[aá]mara)", card_html, re.IGNORECASE)
            bedrooms = int(beds_m.group(1)) if beds_m else 0

            # Photos — src and lazy-load
            photos = re.findall(
                rf'src="({re.escape(_NAVENT_IMG)}[^"]+\.jpg)[^"]*"', card_html
            )
            photos += re.findall(
                rf'data-flickity-lazyload="({re.escape(_NAVENT_IMG)}[^"]+\.jpg)"',
                card_html,
            )
            # Upgrade thumbnail resolution (360x266 → 720x532)
            photos = [p.replace("360x266", "720x532") for p in dict.fromkeys(photos)]

            # Nice-to-haves from card text
            furnished = bool(re.search(r"\bamueblad[ao]\b", card_html, re.IGNORECASE))
            has_washer = bool(
                re.search(
                    r"\b(?:lavadora|cuarto\s+de\s+lavado|área\s+de\s+lavado)\b",
                    card_html,
                    re.IGNORECASE,
                )
            )
            has_fridge = bool(
                re.search(r"\b(?:refrigerador|nevera)\b", card_html, re.IGNORECASE)
            )

            # Floor number from card features
            floor_m = re.search(r"\bpiso\s+(\d+)\b", card_html, re.IGNORECASE)
            floor = int(floor_m.group(1)) if floor_m else None

            # Bathrooms from card features
            baths_m = re.search(r"(\d+)\s*ba[ñn]o", card_html, re.IGNORECASE)
            bathrooms = int(baths_m.group(1)) if baths_m else 0

            # Area in m²
            sqm_m = re.search(r"(\d+(?:\.\d+)?)\s*m[²2]", card_html, re.IGNORECASE)
            sqm = float(sqm_m.group(1)) if sqm_m else None

            listing_url = f"https://www.inmuebles24.com{url_path}"
            listing_id = f"i24:{listing_id_raw}"

            # Title: i24 search cards carry an aria-label on the anchor or a
            # h3 with the posting name. Fall back to a derived title from the
            # address so the HTML cockpit never shows an empty header.
            title_m = (
                re.search(r'aria-label="([^"]{5,200})"', card_html)
                or re.search(r"postingCardTitle[^>]*>([^<]{5,200})", card_html)
                or re.search(r"<h[23][^>]*>([^<]{5,200})</h[23]>", card_html)
            )
            if title_m:
                title = title_m.group(1).strip()
            else:
                parts_title = [f"Depto en {neighborhood}"] if neighborhood else ["Departamento"]
                if address:
                    parts_title.append(address)
                title = " — ".join(parts_title)[:200]

            # Lat/lng: occasionally embedded as data-lat/data-lng or in a
            # `"latitude":-99.x,"longitude":19.x` JS blob near the card.
            lat: float | None = None
            lng: float | None = None
            geo_m = re.search(
                r'data-lat="(-?\d+\.\d+)"\s+data-lng="(-?\d+\.\d+)"', card_html
            )
            if geo_m:
                try:
                    lat = float(geo_m.group(1))
                    lng = float(geo_m.group(2))
                except ValueError:
                    lat = lng = None
            # Sanity gate (CDMX bbox). Reject out-of-region or swapped values.
            if lat is not None and lng is not None:
                if not (18.5 <= lat <= 20.5 and -100.5 <= lng <= -98.0):
                    lat = lng = None

            results.append(
                RawListing(
                    id=listing_id,
                    source="inmuebles24",
                    url=listing_url,
                    price=price,
                    bedrooms=bedrooms,
                    bathrooms=bathrooms,
                    floor=floor,
                    neighborhood=neighborhood,
                    address=address,
                    photos=photos,
                    sqm=sqm,
                    title=title,
                    latitude=lat,
                    longitude=lng,
                    nice_to_haves={
                        "furnished": furnished,
                        "washer_dryer": has_washer,
                        "fridge": has_fridge,
                    },
                )
            )

        return results

    def _extract_price(self, card_html: str) -> int:
        """Parse 'MN 34,000' or similar from Inmuebles24 card HTML."""
        m = re.search(r"postingPrices-module__price[^>]*>([^<]+)", card_html)
        if not m:
            return 0
        raw = m.group(1).strip()  # e.g. "MN 34,000" or "Departamentos desde"
        # Strip "MN " prefix and parse
        num_m = re.search(r"[\d,]+", raw.replace(".", ""))
        if not num_m:
            return 0
        try:
            price = int(num_m.group(0).replace(",", ""))
            return price if 5_000 <= price <= 500_000 else 0
        except ValueError:
            return 0
