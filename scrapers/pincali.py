"""Pincali.com scraper — Firecrawl T2 (JS-rendered HTML).

Pincali uses EasyBroker's marketplace platform with client-side rendering.
Direct httpx GET returns the JS shell without listing cards. Firecrawl renders
the JS and returns the full DOM with property__component cards.

Search URL: /inmuebles/departamentos-en-renta?search_criteria[...]=...
Card structure: <li class="property__component" data-id="EB-...">
Photos: assets.easybroker.com/property_images/
"""

import hashlib
import os
import re

import httpx
from .base import BaseScraper, RawListing

_SEARCH_URL = (
    "https://www.pincali.com/inmuebles/departamentos-en-renta"
    "?search_criteria[min_price]={price_min}"
    "&search_criteria[max_price]={price_max}"
    "&search_criteria[currency]=MXN"
    "&search_criteria[min_bedrooms]={bedrooms}"
    "&page={page}"
)

_PRICE_RE = re.compile(r"\$([\d,]+)")
_BEDS_RE = re.compile(r"(\d+)\s*(?:rec[aá]mara|hab\.?|recam)", re.IGNORECASE)
_BATHS_RE = re.compile(r"(\d+)\s*ba[ñn]o", re.IGNORECASE)
_SQM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*m[²2]", re.IGNORECASE)
_FLOOR_RE = re.compile(r"piso\s+(\d+)", re.IGNORECASE)
_FURNISHED_RE = re.compile(r"\bamueblad[ao]\b", re.IGNORECASE)
_WASHER_RE = re.compile(r"\blavadora\b", re.IGNORECASE)
_FRIDGE_RE = re.compile(r"\brefrigerador\b", re.IGNORECASE)


class PincaliScraper(BaseScraper):
    SOURCE_NAME = "pincali"

    def fetch_listings(self) -> list[RawListing]:
        try:
            return self._try_firecrawl_pincali()
        except Exception as e:
            self._source_error = True
            print("[pincali] Firecrawl failed — skipping source")
            return []

    def _search_urls(self) -> list[str]:
        c = self.config["criteria"]
        urls = []
        for page in range(1, 4):  # 3 pages = up to 60 listings
            urls.append(
                _SEARCH_URL.format(
                    price_min=c["price_min"],
                    price_max=c["price_max"],
                    bedrooms=c.get("bedrooms", 2),
                    page=page,
                )
            )
        return urls

    def _try_firecrawl_pincali(self) -> list[RawListing]:
        """Firecrawl renders Pincali's CSR app and returns full listing HTML."""
        try:
            from firecrawl import Firecrawl  # noqa: PLC0415
        except ImportError:
            raise RuntimeError("firecrawl package not installed")

        from ai_providers import credential
        api_key = credential("firecrawl")
        if not api_key:
            raise RuntimeError("No Firecrawl API key configured")

        app = Firecrawl(api_key=api_key)
        results: list[RawListing] = []

        for url in self._search_urls():
            try:
                doc = app.scrape(url, formats=["html"], only_main_content=False)
                html = (
                    doc.html
                    if hasattr(doc, "html")
                    else doc.get("html", "")
                    if isinstance(doc, dict)
                    else ""
                )
                if not html:
                    print(f"[pincali] Firecrawl returned empty HTML for {url}")
                    continue
                listings = self._parse_property_cards(html)
                if not listings:
                    print(f"[pincali] 0 cards parsed from {url} (len={len(html)})")
                    break  # no more pages
                results.extend(listings)
                print(
                    f"[pincali] page {self._search_urls().index(url) + 1}: {len(listings)} listings"
                )
            except Exception as e:
                self._source_error = True
                print("[pincali] Firecrawl scrape failed")
                break

        return results

    def _parse_property_cards(self, html: str) -> list[RawListing]:
        """Parse <li class='property__component'> cards from rendered HTML."""
        boundaries = list(
            re.finditer(r'<li\b[^>]*class="[^"]*property__component[^"]*"[^>]*>', html)
        )
        if not boundaries:
            return []

        results = []
        for i, m in enumerate(boundaries):
            start = m.start()
            end = boundaries[i + 1].start() if i + 1 < len(boundaries) else len(html)
            card_html = html[start:end]
            listing = self._card_to_listing(card_html)
            if listing:
                results.append(listing)
        return results

    def _card_to_listing(self, card_html: str) -> RawListing | None:
        """Convert one property__component card to RawListing."""
        id_m = re.search(r'data-id="(EB-[A-Z0-9]+)"', card_html)
        eb_id = id_m.group(1) if id_m else None

        url_m = re.search(r'href="(/inmueble/[^"]{5,100})"', card_html)
        if not url_m:
            return None
        rel_url = url_m.group(1)
        url = f"https://www.pincali.com{rel_url}"

        price_m = _PRICE_RE.search(card_html)
        if not price_m:
            return None
        try:
            price = int(price_m.group(1).replace(",", ""))
        except ValueError:
            return None
        if not (5_000 <= price <= 500_000):
            return None

        beds_m = _BEDS_RE.search(card_html)
        bedrooms = int(beds_m.group(1)) if beds_m else 0

        baths_m = _BATHS_RE.search(card_html)
        bathrooms = int(baths_m.group(1)) if baths_m else 0

        sqm_m = _SQM_RE.search(card_html)
        sqm = float(sqm_m.group(1)) if sqm_m else None

        floor_m = _FLOOR_RE.search(card_html)
        floor = int(floor_m.group(1)) if floor_m else None

        neigh = _slug_to_neighborhood(rel_url)

        photos = re.findall(
            r'(?:src|data-src)="(https://assets\.easybroker\.com/property_images/[^"]+\.jpg[^"]*)"',
            card_html,
        )
        photos = list(dict.fromkeys(p.split("?")[0] for p in photos))

        furnished = bool(_FURNISHED_RE.search(card_html))
        has_washer = bool(_WASHER_RE.search(card_html))
        has_fridge = bool(_FRIDGE_RE.search(card_html))

        listing_id = f"pincali:{eb_id or hashlib.md5(url.encode()).hexdigest()[:10]}"
        return RawListing(
            id=listing_id,
            source="pincali",
            url=url,
            price=price,
            bedrooms=bedrooms,
            bathrooms=bathrooms,
            floor=floor,
            neighborhood=neigh,
            address="",
            photos=photos,
            sqm=sqm,
            nice_to_haves={
                "furnished": furnished,
                "washer_dryer": has_washer,
                "fridge": has_fridge,
            },
        )


def _slug_to_neighborhood(slug: str) -> str:
    """Best-effort neighborhood from listing URL slug."""
    _MAP = {
        "polanco": "Polanco",
        "lomas": "Lomas de Chapultepec",
        "condesa": "Condesa",
        "roma-norte": "Roma Norte",
        "roma-sur": "Roma Sur",
        "juarez": "Juárez",
        "ju-rez": "Juárez",
        "cuauhtemoc": "Cuauhtémoc",
        "hipodromo": "Hipódromo",
        "granada": "Amp Granada",
    }
    slug_lower = slug.lower()
    for key, name in _MAP.items():
        if key in slug_lower:
            return name
    return "CDMX"
