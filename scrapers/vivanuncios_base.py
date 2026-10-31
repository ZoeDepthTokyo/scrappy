"""Shared logic for Vivanuncios (OLX Mexico) scrapers.

Contains shared URL patterns and search logic used by both the native
(httpx) and browser-based (Lightpanda) implementations.
"""
from urllib.parse import quote_plus


class VivanunciosMixin:
    SOURCE_NAME = "vivanuncios"
    BASE = (
        "https://www.vivanuncios.com.mx/s-departamentos-en-renta/"
        "ciudad-de-mexico/v1c1097l10601p1"
    )

    def _search_urls(self) -> list[str]:
        crit = self.config.get("criteria", {})
        lo = crit.get("price_min", 30000)
        hi = crit.get("price_max", 45000)
        urls = []
        for hood in crit.get("neighborhoods", []):
            urls.append(
                f"{self.BASE}?ne_price={hi}&ps_price={lo}&q={quote_plus(hood)}"
            )
        return urls
