"""Vivanuncios (OLX Mexico) apartment scraper.

Two-tier fetch strategy:
  T1: VivanunciosNative — httpx
  T3: VivanunciosLightpanda — headless browser via WSL2 (Lightpanda)

T3 activates when T1 returns 0 results or raises, except after an HTTP 403.
"""
from .base import BaseScraper, RawListing
from .vivanuncios_native import VivanunciosNative


class VivanunciosScraper(BaseScraper):
    SOURCE_NAME = "vivanuncios"

    def fetch_listings(self) -> list[RawListing]:
        self._source_error = False
        # T1: native HTTP; do not attempt a browser fallback after a block.
        try:
            native = VivanunciosNative(self.config)
            results = native.fetch_listings()
            if native._blocked_by_site:
                self._source_error = True
                return results
            if results:
                return results
            print("[vivanuncios] T1 native returned 0 — falling back to Lightpanda")
        except Exception as e:
            print(f"[vivanuncios] T1 native failed: {e} — falling back to Lightpanda")
        # T3: Lightpanda headless browser via WSL2 Ubuntu
        try:
            from .vivanuncios_lightpanda import VivanunciosLightpanda
            return VivanunciosLightpanda(self.config).fetch_listings()
        except Exception as e:
            print(f"[vivanuncios] T3 Lightpanda failed: {e} — skipping source")
            return []
