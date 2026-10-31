"""Propiedades.com native scraper — replaces Firecrawl extract for this source.

URL patterns (both tried — site uses /df/ and /cdmx/ paths):
  https://propiedades.com/df/departamentos-en-renta?precio_min={lo}&precio_max={hi}&cuartos=2
  https://propiedades.com/cdmx/departamentos-en-renta?...

Last-run note (2026-04-14): 60s timeout → ReadTimeout consistently. Server
hangs at TCP level before sending headers — classic Cloudflare Chrome-TLS gate.
Fix: short httpx timeout (10s) + curl_cffi Chrome impersonation on any error.
"""
from scrapers.base import BaseScraper, RawListing
from scrapers.httpx_adapter import HttpxAdapter, JsonLdExtractor
from scrapers.microdata_extractor import MicrodataExtractor
from scrapers.propiedades_base import PropiedadesMixin


class PropiedadesNative(PropiedadesMixin, BaseScraper):
    def __init__(self, config: dict):
        super().__init__(config)
        # Short timeout: fail fast so curl_cffi retry kicks in quickly.
        # retry_curl_cffi_on_error: fires on TCP hang/timeout (not just 403).
        self._adapter = HttpxAdapter(
            timeout=10.0,
            source_name=self.SOURCE_NAME,
            rotate_ua=True,
            use_curl_cffi_on_403=True,
            retry_curl_cffi_on_error=True,
        )

    def fetch_listings(self) -> list[RawListing]:
        out: list[RawListing] = []
        try:
            for url in self._search_urls():
                html = self._adapter.fetch_html(url)
                if not html:
                    continue
                items = JsonLdExtractor.extract(html)
                if not items:
                    items = MicrodataExtractor.extract(html)
                parsed = self._parse_extract(items)
                out.extend(parsed)
                print(f"[{self.SOURCE_NAME}] {url}: {len(parsed)} listings")
        finally:
            self._adapter.close()
        return out
