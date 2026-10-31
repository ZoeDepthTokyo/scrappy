"""Casa.com.mx native scraper — replaces Firecrawl extract for this source.

URL pattern:
  https://www.casa.com.mx/departamentos-en-renta/ciudad-de-mexico
  ?precio_min={lo}&precio_max={hi}&recamaras=2

Fetch chain (each step only runs if the previous returned empty listings):
  1. Fetch listing page; follow JS redirect if response < 2KB
  2. retry_curl_cffi_below_bytes=2048 forces Chrome-TLS retry for skeleton pages
  3. Try JSON-LD extraction (schema.org)
  4. Try schema.org microdata extraction
  5. Try __NEXT_DATA__ extraction (Next.js apps embed all page data in this tag)
"""
import hashlib
import json
import re

from scrapers.base import BaseScraper, RawListing
from scrapers.httpx_adapter import HttpxAdapter, JsonLdExtractor
from scrapers.microdata_extractor import MicrodataExtractor

_JS_REDIRECT_RE = re.compile(r"""window\.location\.href\s*=\s*["']([^"']+)["']""")
_META_REFRESH_RE = re.compile(
    r"""<meta[^>]+http-equiv=["']?refresh["']?[^>]+content=["'][^;]*;\s*url=([^"'>\s]+)""",
    re.IGNORECASE,
)
_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def _extract_next_data(html: str, source_name: str) -> list[RawListing]:
    """Parse listings from Next.js __NEXT_DATA__ JSON blob.

    casa.com.mx embeds all listing data in a <script id="__NEXT_DATA__"> tag.
    Searches common pageProps paths for an array of listing objects.
    """
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return []

    # Walk common paths to find a listings array
    props = data.get("props", {}).get("pageProps", {})
    candidates: list = (
        props.get("listings")
        or props.get("properties")
        or props.get("results")
        or props.get("data", {}).get("listings") if isinstance(props.get("data"), dict) else None
        or []
    )
    if not isinstance(candidates, list):
        return []

    results: list[RawListing] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        try:
            url = (
                item.get("url")
                or item.get("permalink")
                or item.get("link")
                or item.get("slug", "")
            )
            if url and url.startswith("/"):
                url = "https://www.casa.com.mx" + url
            if not url:
                continue
            # Price — try multiple field names
            price_raw = (
                item.get("price")
                or item.get("precio")
                or item.get("rent_price")
                or item.get("monthly_rent")
                or 0
            )
            try:
                price = int(float(str(price_raw).replace(",", "")))
            except (ValueError, TypeError):
                continue
            if not (5_000 <= price <= 200_000):
                continue
            bedrooms = int(item.get("bedrooms") or item.get("rooms") or item.get("recamaras") or 0)
            bathrooms = int(item.get("bathrooms") or item.get("banos") or 0)
            neighborhood = (
                item.get("neighborhood")
                or item.get("colonia")
                or item.get("zone")
                or item.get("location", {}).get("neighborhood", "") if isinstance(item.get("location"), dict) else ""
                or ""
            )
            address = (
                item.get("address")
                or item.get("direccion")
                or item.get("title")
                or ""
            )
            photos_raw = item.get("photos") or item.get("images") or item.get("gallery") or []
            if isinstance(photos_raw, list):
                photos = [
                    (p if isinstance(p, str) else p.get("url", "") if isinstance(p, dict) else "")
                    for p in photos_raw
                ]
                photos = [p for p in photos if p]
            else:
                photos = []
            listing_id = f"{source_name}:{hashlib.md5(url.encode()).hexdigest()[:8]}"
            results.append(RawListing(
                id=listing_id,
                source=source_name,
                url=url,
                price=price,
                bedrooms=bedrooms,
                bathrooms=bathrooms,
                floor=None,
                neighborhood=str(neighborhood),
                address=str(address),
                photos=photos,
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return results


class CasaMxNative(BaseScraper):
    SOURCE_NAME = "casa_mx"
    BASE = "https://www.casa.com.mx/departamentos-en-renta/ciudad-de-mexico"

    def __init__(self, config: dict):
        super().__init__(config)
        # retry_curl_cffi_below_bytes: skeleton page (< 2KB) triggers Chrome-TLS retry
        self._adapter = HttpxAdapter(
            timeout=30.0,
            source_name=self.SOURCE_NAME,
            rotate_ua=True,
            use_curl_cffi_on_403=True,
            retry_curl_cffi_below_bytes=2048,
        )

    def _search_urls(self) -> list[str]:
        crit = self.config.get("criteria", {})
        lo = crit.get("price_min", 30000)
        hi = crit.get("price_max", 45000)
        return [f"{self.BASE}?precio_min={lo}&precio_max={hi}&recamaras=2"]

    def fetch_listings(self) -> list[RawListing]:
        out: list[RawListing] = []
        try:
            for url in self._search_urls():
                html = self._adapter.fetch_html(url)
                if not html:
                    continue
                # Follow JS redirect or meta-refresh if response is a skeleton page
                if len(html) < 2048:
                    nxt = None
                    m = _JS_REDIRECT_RE.search(html)
                    if m:
                        nxt = m.group(1)
                    else:
                        m2 = _META_REFRESH_RE.search(html)
                        if m2:
                            nxt = m2.group(1)
                    if nxt:
                        if nxt.startswith("/"):
                            nxt = "https://www.casa.com.mx" + nxt
                        html = self._adapter.fetch_html(nxt) or html

                # Parse chain: JSON-LD → microdata → __NEXT_DATA__
                items = JsonLdExtractor.extract(html)
                if not items:
                    items = MicrodataExtractor.extract(html)
                parsed = self._parse_extract(items) if items else []
                if not parsed:
                    parsed = _extract_next_data(html, self.SOURCE_NAME)
                out.extend(parsed)
                print(f"[{self.SOURCE_NAME}] {url}: {len(parsed)} listings "
                      f"(html={len(html)}b, jsonld={len(items)} items)")
        finally:
            self._adapter.close()
        return out
