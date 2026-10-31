"""Trovit apartment scraper.

T1 JSON API endpoint not yet discovered (_try_json_api is a stub against a
placeholder URL). Route through the native self-hosted adapter.
"""
import httpx
from .base import BaseScraper, RawListing
from .trovit_native import TrovitNative

# T1 JSON endpoint — discover via DevTools → Network → XHR on trovit.com.mx
API_ENDPOINT = "https://DISCOVER_VIA_XHR"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


class TrovitScraper(BaseScraper):
    SOURCE_NAME = "trovit"

    def fetch_listings(self) -> list[RawListing]:
        # T1 JSON endpoint not yet discovered — go straight to native adapter.
        try:
            return TrovitNative(self.config).fetch_listings()
        except Exception as e:
            print(f"[trovit] native scrape failed: {e} — skipping source")
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
