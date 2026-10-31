"""Trovit native scraper — replaces Firecrawl extract for this source.

Last-run note (2026-04-14): legacy ``index.php/cod.search_homes/...`` URL
returned HTTP 404. Trovit now serves SEO-friendly slug paths under
``casas.trovit.com.mx``, e.g.::

    https://casas.trovit.com.mx/departamentos-renta-ciudad-de-mexico
    https://casas.trovit.com.mx/departamentos-renta-{slug}          # per colonia

Parse strategy (2026-04-15): Trovit's JSON-LD (SearchResultsPage > about >
SingleFamilyResidence) omits price and URL — it is decoration, not data.
The HTML <article class="snippet-listing" data-id="..."> elements contain all
fields we need: price, URL, bedrooms, bathrooms, neighborhood, photos.
"""
import hashlib
import re
from urllib.parse import quote

from scrapers.base import BaseScraper, RawListing
from scrapers.httpx_adapter import HttpxAdapter

# --- Compiled patterns for article parsing ---
_ARTICLE_RE = re.compile(r'<article[^>]*>(.*?)</article>', re.DOTALL | re.IGNORECASE)
_DATAID_RE = re.compile(r'data-id="([^"]+)"', re.IGNORECASE)
# Matches any trovit.com.mx URL — allow full URL character set (., ?, =, etc.)
_URL_RE = re.compile(r'href=["\']?(https://casas\.trovit\.com\.mx/[^"\'>\s]{5,300})', re.IGNORECASE)
# MXN suffix is optional — listings often show just "$35,000"
_PRICE_RE = re.compile(r'\$\s*([\d,]+)(?:\s*MXN)?\b', re.IGNORECASE)
_IMG_RE = re.compile(r'src="(https://img\.trovit\.com/[^"]+)"', re.IGNORECASE)
_TITLE_RE = re.compile(r'title="([^"]+)"', re.IGNORECASE)
# Use \w+ wildcard to tolerate charset corruption of accented vowels (á→\ufffd etc.)
_BED_RE = re.compile(r'(\d+)\s+rec\w+mara', re.IGNORECASE)
_BATH_RE = re.compile(r'(\d+)\s+ba\w+o\b', re.IGNORECASE)
# "Departamento en Benito Juárez, Ciudad de México"
_HOOD_RE = re.compile(r'\ben\s+([^,\n]{3,40}),\s*Ciudad de M', re.IGNORECASE)


def _slugify(hood: str) -> str:
    s = hood.lower().strip()
    trans = str.maketrans("áéíóúñü", "aeiounu")
    s = s.translate(trans)
    return quote("-".join(s.split()))


def _parse_price(raw: str) -> int:
    try:
        return int(raw.replace(",", ""))
    except ValueError:
        return 0


class TrovitNative(BaseScraper):
    SOURCE_NAME = "trovit"
    BASE = "https://casas.trovit.com.mx"
    CITY_PATH = "departamentos-renta-ciudad-de-mexico"

    def __init__(self, config: dict):
        super().__init__(config)
        self._adapter = HttpxAdapter(
            timeout=30.0,
            source_name=self.SOURCE_NAME,
            rotate_ua=True,
            use_curl_cffi_on_403=True,
        )

    def _search_urls(self) -> list[str]:
        crit = self.config.get("criteria", {})
        lo = crit.get("price_min", 30000)
        hi = crit.get("price_max", 45000)
        # City-wide query only — per-hood slugs return same results + extra noise
        return [f"{self.BASE}/{self.CITY_PATH}?price_min={lo}&price_max={hi}&rooms_min=2"]

    def fetch_listings(self) -> list[RawListing]:
        out: list[RawListing] = []
        try:
            for url in self._search_urls():
                html = self._adapter.fetch_html(url)
                if not html:
                    continue
                parsed = self._parse_html(html)
                out.extend(parsed)
                print(f"[{self.SOURCE_NAME}] {url}: {len(parsed)} listings")
        finally:
            self._adapter.close()
        return out

    def _parse_html(self, html: str) -> list[RawListing]:
        results: list[RawListing] = []
        articles = list(_ARTICLE_RE.finditer(html))
        skipped_no_url = skipped_no_price = 0
        for article_m in articles:
            # Need the full article tag to extract data-id
            start = article_m.start()
            tag_end = html.find('>', start)
            article_tag = html[start:tag_end + 1]
            body = article_m.group(1)

            did_m = _DATAID_RE.search(article_tag)
            url_m = _URL_RE.search(body)
            price_m = _PRICE_RE.search(body)

            if not url_m:
                skipped_no_url += 1
                continue
            if not price_m:
                skipped_no_price += 1
                continue

            price = _parse_price(price_m.group(1))
            if not (5_000 <= price <= 200_000):
                continue

            url = url_m.group(1)
            raw_id = did_m.group(1) if did_m else url
            listing_id = f"trovit:{hashlib.md5(raw_id.encode()).hexdigest()[:8]}"

            # Strip tags for text extraction
            text = re.sub(r'<[^>]+>', ' ', body)
            text = re.sub(r'\s+', ' ', text).strip()

            beds_m = _BED_RE.search(text)
            baths_m = _BATH_RE.search(text)
            hood_m = _HOOD_RE.search(text)
            title_m = _TITLE_RE.search(body)

            bedrooms = int(beds_m.group(1)) if beds_m else 0
            bathrooms = int(baths_m.group(1)) if baths_m else 0
            neighborhood = hood_m.group(1).strip() if hood_m else ""
            address = title_m.group(1).strip() if title_m else ""

            photos = _IMG_RE.findall(body)

            results.append(
                RawListing(
                    id=listing_id,
                    source=self.SOURCE_NAME,
                    url=url,
                    price=price,
                    bedrooms=bedrooms,
                    bathrooms=bathrooms,
                    floor=None,
                    neighborhood=neighborhood,
                    address=address,
                    photos=photos,
                )
            )
        print(f"[trovit] parse: {len(articles)} articles, "
              f"{skipped_no_url} skipped (no URL), {skipped_no_price} skipped (no price), "
              f"{len(results)} parsed")
        return results
