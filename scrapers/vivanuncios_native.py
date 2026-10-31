"""Vivanuncios native scraper — replaces Firecrawl extract for this source.

URL pattern:
  https://www.vivanuncios.com.mx/s-departamentos-en-renta/ciudad-de-mexico/
  v1c1097l10601p1?ne_price={hi}&ps_price={lo}&q={hood}

Vivanuncios (OLX Mexico) is a Next.js app. Listings are embedded in
__NEXT_DATA__ JSON, not schema.org JSON-LD. Extract chain:
  1. JSON-LD (kept as fast-path in case OLX adds structured markup later)
  2. __NEXT_DATA__ — primary OLX data source
  3. microdata fallback
"""
import hashlib
import json
import re
from typing import Optional
from urllib.parse import quote_plus

from scrapers.base import BaseScraper, RawListing
from scrapers.httpx_adapter import HttpxAdapter, JsonLdExtractor
from scrapers.microdata_extractor import MicrodataExtractor
from scrapers.vivanuncios_base import VivanunciosMixin
from .constants import BROAD_LOCATION_NAMES

_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

_OLX_BASE_URL = "https://www.vivanuncios.com.mx"


def _parse_olx_next_data(
    html: str, source_name: str, fallback_hood: Optional[str] = None
) -> list[RawListing]:
    """Extract listings from OLX/Vivanuncios __NEXT_DATA__ JSON blob.

    OLX stores listings under props.pageProps.listingProps.listingAds
    or similar paths. Walks the tree to find arrays of ad objects.
    """
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return []

    # Walk known OLX Next.js paths for listing arrays
    props = data.get("props", {}).get("pageProps", {})
    ads: list = (
        props.get("listingProps", {}).get("listingAds")
        or props.get("ads")
        or props.get("listings")
        or props.get("results")
        or []
    )
    if not isinstance(ads, list) or not ads:
        # Diagnostic: log top-level pageProps keys to identify correct path
        print(f"[vivanuncios] __NEXT_DATA__ pageProps keys: {list(props.keys())[:15]}")
        # Deep search: find first list with dicts that have a "url" or "link" key.
        # Recurses into both dict values and list elements so nested structures
        # like dehydratedState.queries[0].state.data.ads are reachable.
        def _find_ad_list(obj, depth=0):
            if depth > 8:
                return None
            if isinstance(obj, list) and obj and isinstance(obj[0], dict) and ("url" in obj[0] or "link" in obj[0]):
                return obj
            if isinstance(obj, dict):
                for v in obj.values():
                    r = _find_ad_list(v, depth + 1)
                    if r:
                        return r
            elif isinstance(obj, list):
                for item in obj:
                    r = _find_ad_list(item, depth + 1)
                    if r:
                        return r
            return None
        ads = _find_ad_list(data) or []

    results: list[RawListing] = []
    for ad in ads:
        if not isinstance(ad, dict):
            continue
        try:
            url = ad.get("url", "") or ad.get("link", "")
            if url and not url.startswith("http"):
                url = _OLX_BASE_URL + url
            if not url:
                continue

            # Price — OLX nests price under "price" dict or flat field
            price = 0
            p = ad.get("price")
            if isinstance(p, dict):
                price = int(float(str(p.get("value", 0)).replace(",", "")))
            elif p is not None:
                try:
                    price = int(float(str(p).replace(",", "")))
                except (ValueError, TypeError):
                    pass
            if not (5_000 <= price <= 200_000):
                continue

            # Params — OLX stores bedrooms/bathrooms in a "params" list
            params_list = ad.get("params") or []
            bedrooms = bathrooms = 0
            for param in params_list:
                if not isinstance(param, dict):
                    continue
                key = param.get("key", "")
                val = param.get("value", {})
                num = int(float(str(val.get("key", 0) if isinstance(val, dict) else val or 0)))
                if key in ("rooms", "bedrooms", "recamaras"):
                    bedrooms = num
                elif key in ("bathrooms", "banos"):
                    bathrooms = num

            # Location
            loc = ad.get("location") or {}
            neighborhood = (
                loc.get("city", {}).get("name", "") if isinstance(loc, dict) else ""
            ) or ad.get("category", {}).get("name", "") or ""

            if fallback_hood and (
                not neighborhood or str(neighborhood).lower() in BROAD_LOCATION_NAMES
            ):
                neighborhood = fallback_hood

            # Photos
            photos_raw = ad.get("photos") or ad.get("images") or []
            photos = []
            for p in photos_raw:
                if isinstance(p, str):
                    photos.append(p)
                elif isinstance(p, dict):
                    photos.append(p.get("link") or p.get("url") or "")
            photos = [ph for ph in photos if ph]

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
                address=ad.get("title", ""),
                photos=photos,
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return results


class VivanunciosNative(VivanunciosMixin, BaseScraper):
    def __init__(self, config: dict):
        super().__init__(config)
        self._adapter = HttpxAdapter(
            timeout=30.0,
            source_name=self.SOURCE_NAME,
            rotate_ua=True,
            use_curl_cffi_on_403=False,
        )
        self._blocked_by_site = False

    def fetch_listings(self) -> list[RawListing]:
        out: list[RawListing] = []
        seen_urls: set[str] = set()
        crit = self.config.get("criteria", {})
        hoods = crit.get("neighborhoods", [])
        try:
            for i, url in enumerate(self._search_urls()):
                hood_name = hoods[i] if i < len(hoods) else None
                row_count = len(HttpxAdapter._health_rows)
                html = self._adapter.fetch_html(url)
                rows = HttpxAdapter._health_rows[row_count:]
                if rows and rows[-1].get("status") == 403:
                    self._blocked_by_site = True
                    print("[vivanuncios] Blocked by site (HTTP 403)")
                    break
                if not html:
                    continue
                # Parse chain: JSON-LD → __NEXT_DATA__ (primary for OLX) → microdata
                items = JsonLdExtractor.extract(html)
                parsed = self._parse_extract(items, fallback_hood=hood_name) if items else []
                if not parsed:
                    parsed = _parse_olx_next_data(html, self.SOURCE_NAME, fallback_hood=hood_name)
                if not parsed:
                    items = MicrodataExtractor.extract(html)
                    parsed = self._parse_extract(items, fallback_hood=hood_name) if items else []
                # Dedup across neighborhood queries (same listing appears in multiple)
                unique = [p for p in parsed if p.url not in seen_urls]
                seen_urls.update(p.url for p in unique)
                out.extend(unique)
                print(f"[{self.SOURCE_NAME}] {url.split('q=')[-1]}: {len(unique)} listings")
        finally:
            self._adapter.close()
        return out
