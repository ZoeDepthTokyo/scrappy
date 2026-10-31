"""Propiedades.com apartment scraper.

T1 JSON API endpoint not yet discovered (_try_json_api is a stub against a
placeholder URL). Route through the native self-hosted adapter.
"""
import httpx
from .base import BaseScraper, RawListing
from .propiedades_native import PropiedadesNative
from .propiedades_base import PropiedadesMixin

# T1 JSON endpoint — discover via DevTools → Network → XHR on propiedades.com
API_ENDPOINT = "https://DISCOVER_VIA_XHR"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


class PropiedadesScraper(PropiedadesMixin, BaseScraper):
    def fetch_listings(self) -> list[RawListing]:
        # T1: native adapter (httpx + curl_cffi)
        try:
            results = PropiedadesNative(self.config).fetch_listings()
            if results:
                return results
            print("[propiedades] T1 native returned 0 — falling back to Firecrawl")
        except Exception as e:
            print(f"[propiedades] T1 native failed: {e} — falling back to Firecrawl")

        # T2: Firecrawl (browser rendering)
        try:
            return self._try_firecrawl()
        except Exception as e:
            self._source_error = True
            print("[propiedades] Firecrawl failed — skipping source")
            return []

    def _try_json_api(self) -> list[RawListing]:
        # Preserved stub — wire up when T1 XHR endpoint is discovered.
        c = self.config["criteria"]
        params = {
            "operation": "rent",
            "property_type": "apartment",
            "price_min": c["price_min"],
            "price_max": c["price_max"],
            "bedrooms": c["bedrooms"],
        }
        resp = httpx.get(API_ENDPOINT, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return self._parse(resp.json())

    def _parse(self, data: dict) -> list[RawListing]:
        # Stub — implement when T1 XHR schema is confirmed live
        return []
