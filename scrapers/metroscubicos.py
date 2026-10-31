import httpx
from .base import BaseScraper, RawListing

# T1 JSON endpoint — discover via DevTools → Network → XHR on metroscubicos.com
API_ENDPOINT = "https://DISCOVER_VIA_XHR"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# T2 search pages — Firecrawl extracts structured data from these rendered pages
_SEARCH_URL = (
    "https://www.metroscubicos.com/renta/departamento/ciudad-de-mexico/"
    "?precio_min={price_min}&precio_max={price_max}&recamaras={bedrooms}"
)


class MetrosCubicosScraper(BaseScraper):
    SOURCE_NAME = "metroscubicos"

    def fetch_listings(self) -> list[RawListing]:
        try:
            return self._try_json_api()
        except Exception as e:
            print(f"[metroscubicos] T1 failed: {e} — trying Firecrawl")
            try:
                return self._try_firecrawl()
            except Exception as e2:
                print(f"[metroscubicos] T2 failed: {e2} — skipping source")
                return []

    def _search_urls(self) -> list[str]:
        c = self.config["criteria"]
        return [
            _SEARCH_URL.format(
                price_min=c["price_min"],
                price_max=c["price_max"],
                bedrooms=c["bedrooms"],
            )
        ]

    def _try_json_api(self) -> list[RawListing]:
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
