import pytest
from unittest.mock import patch

CONFIG = {
    "criteria": {
        "neighborhoods": ["Polanco"],
        "price_min": 30000,
        "price_max": 45000,
        "bedrooms": 2,
        "bathrooms": 2,
    },
    "firecrawl_api_key": "",
    "anthropic_api_key": "",
    "easybroker_api_key": "",
}

# Scrapers that still use module-level httpx.get directly
_HTTPX_GET_SCRAPERS = [
    ("MetrosCubicosScraper", "scrapers.metroscubicos"),
    ("VivanunciosScraper", "scrapers.vivanuncios"),
    ("PropiedadesScraper", "scrapers.propiedades"),
    ("TrovitScraper", "scrapers.trovit"),
    ("CasaMxScraper", "scrapers.casa_mx"),
]


@pytest.mark.parametrize("scraper_class,module", _HTTPX_GET_SCRAPERS)
def test_adapter_returns_list_on_api_failure(scraper_class, module):
    """When T1 and T2 both fail, adapter returns [] not an exception."""
    import importlib

    mod = importlib.import_module(module)
    cls = getattr(mod, scraper_class)
    scraper = cls(CONFIG)
    with patch("httpx.get", side_effect=Exception("network error")):
        result = scraper.fetch_listings()
    assert isinstance(result, list)


def test_lamudi_returns_list_on_http_failure():
    """LamudiScraper returns [] when HttpxAdapter returns empty (network failure)."""
    from scrapers.lamudi import LamudiScraper
    from scrapers.httpx_adapter import HttpxAdapter

    scraper = LamudiScraper(CONFIG)
    with patch.object(HttpxAdapter, "fetch_html", return_value=""):
        result = scraper.fetch_listings()
    assert isinstance(result, list)
    assert result == []
