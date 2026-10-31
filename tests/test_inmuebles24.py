from unittest.mock import patch, MagicMock
from scrapers.inmuebles24 import Inmuebles24Scraper
from scrapers.base import RawListing

CONFIG = {
    "criteria": {
        "neighborhoods": ["Polanco"],
        "price_min": 30000,
        "price_max": 45000,
        "bedrooms": 2,
        "bathrooms": 2,
    },
    "firecrawl_api_key": "",
}

# Minimal HTML snippet mimicking one Inmuebles24 PROPERTY card
_CARD_HTML = (
    'data-qa="posting PROPERTY" data-id="99001" data-posting-type="PROPERTY"'
    ' data-to-posting="/propiedades/depto-polanco-99001.html"'
    ' <span class="postingPrices-module__price">MN 38,500</span>'
    ' <span class="postingLocations-module__location-address">Emilio Castelar 145</span>'
    " 2 amb "
    ' <img src="https://img10.naventcdn.com/avisos/photo-360x266.jpg"/>'
    ' data-flickity-lazyload="https://img10.naventcdn.com/avisos/photo2-360x266.jpg"'
    " amueblado"
)


def test_parse_property_cards_basic():
    scraper = Inmuebles24Scraper(CONFIG)
    listings = scraper._parse_property_cards(_CARD_HTML, "Polanco")
    assert len(listings) == 1
    l = listings[0]
    assert isinstance(l, RawListing)
    assert l.price == 38500
    assert l.bedrooms == 2
    assert l.address == "Emilio Castelar 145"
    assert l.neighborhood == "Polanco"
    assert l.url == "https://www.inmuebles24.com/propiedades/depto-polanco-99001.html"


def test_parse_property_cards_photo_resolution_upgrade():
    scraper = Inmuebles24Scraper(CONFIG)
    listings = scraper._parse_property_cards(_CARD_HTML, "Polanco")
    assert len(listings) == 1
    # Photos should be upgraded from 360x266 to 720x532
    for photo in listings[0].photos:
        assert "360x266" not in photo
        assert "720x532" in photo


def test_parse_property_cards_furnished_flag():
    scraper = Inmuebles24Scraper(CONFIG)
    listings = scraper._parse_property_cards(_CARD_HTML, "Polanco")
    assert listings[0].nice_to_haves.get("furnished") is True


def test_parse_property_cards_skips_no_price():
    scraper = Inmuebles24Scraper(CONFIG)
    no_price_html = (
        'data-qa="posting PROPERTY" data-id="99002" data-posting-type="PROPERTY"'
        ' data-to-posting="/propiedades/depto-99002.html"'
        ' <span class="postingPrices-module__price">Departamentos desde</span>'
    )
    listings = scraper._parse_property_cards(no_price_html, "Polanco")
    assert listings == []


def test_parse_property_cards_rejects_non_cdmx_city():
    """Promoted listings from Guadalajara (or any non-CDMX city) must be silently dropped.

    Reproduces the bug observed 2026-04-12: I24 search results for Polanco
    included cards with 'guadalajara' in data-to-posting, which were labeled
    'Polanco' and passed the neighborhood filter.
    """
    scraper = Inmuebles24Scraper(CONFIG)
    # Mimics a real Guadalajara promoted card found in Polanco search results
    gdl_html = (
        'data-qa="posting PROPERTY" data-id="148900659" data-posting-type="PROPERTY"'
        ' data-to-posting="/propiedades/clasificado/alclapin-departamento-en-renta-vallarta-norte-guadalajara-148900659.html"'
        ' <span class="postingPrices-module__price">MN 34,000</span>'
        ' <span class="postingLocations-module__location-address">Av Vallarta 1234</span>'
        " 3 amb "
        ' <img src="https://img10.naventcdn.com/avisos/gdl-360x266.jpg"/>'
    )
    listings = scraper._parse_property_cards(gdl_html, "Polanco")
    assert listings == [], "Non-CDMX city cards must be rejected before entering the pipeline"


def test_parse_property_cards_keeps_cdmx_listing():
    """Listings with CDMX slugs must still be returned normally."""
    scraper = Inmuebles24Scraper(CONFIG)
    cdmx_html = (
        'data-qa="posting PROPERTY" data-id="149000001" data-posting-type="PROPERTY"'
        ' data-to-posting="/propiedades/clasificado/polmih-departamento-en-renta-polanco-miguel-hidalgo-149000001.html"'
        ' <span class="postingPrices-module__price">MN 38,000</span>'
        ' <span class="postingLocations-module__location-address">Presidente Masaryk 101</span>'
        " 2 amb "
        ' <img src="https://img10.naventcdn.com/avisos/pol-360x266.jpg"/>'
    )
    listings = scraper._parse_property_cards(cdmx_html, "Polanco")
    assert len(listings) == 1
    assert listings[0].neighborhood == "Polanco"
    assert listings[0].price == 38000


def test_parse_property_cards_keeps_naucalpan_listing():
    """Listings with Naucalpan (Estado de México) slugs must NOT be rejected.

    Verifies fix for 'estado-de-mexico' filtering reported 2026-04-15.
    """
    scraper = Inmuebles24Scraper(CONFIG)
    # Mimics a real Naucalpan card
    nau_html = (
        'data-qa="posting PROPERTY" data-id="150000001" data-posting-type="PROPERTY"'
        ' data-to-posting="/propiedades/clasificado/alclapin-departamento-en-renta-naucalpan-de-juarez-estado-de-mexico-150000001.html"'
        ' <span class="postingPrices-module__price">MN 35,000</span>'
        ' <span class="postingLocations-module__location-address">Circuito Arquitectos 10</span>'
        " 2 amb "
        ' <img src="https://img10.naventcdn.com/avisos/nau-360x266.jpg"/>'
    )
    listings = scraper._parse_property_cards(nau_html, "Naucalpan")
    assert (
        len(listings) == 1
    ), "Naucalpan listings should be preserved despite containing 'estado-de-mexico'"
    assert listings[0].neighborhood == "Naucalpan"


def test_fetch_listings_returns_empty_on_firecrawl_failure():
    scraper = Inmuebles24Scraper(CONFIG)
    with patch.object(scraper, "_try_native_httpx", return_value=[]):
        with patch.object(
            scraper, "_try_firecrawl_html", side_effect=Exception("api error")
        ):
            result = scraper.fetch_listings()
    assert isinstance(result, list)
    assert result == []


# ── Wave 4 tests ──────────────────────────────────────────────────────────────

def test_fetch_listings_uses_native_first():
    """fetch_listings returns native results without calling Firecrawl."""
    scraper = Inmuebles24Scraper(CONFIG)
    native_result = [MagicMock()]
    with patch.object(scraper, "_try_native_httpx", return_value=native_result) as mock_native:
        with patch.object(scraper, "_try_firecrawl_html") as mock_fc:
            result = scraper.fetch_listings()
    assert result == native_result
    mock_native.assert_called_once()
    mock_fc.assert_not_called()


def test_fetch_listings_falls_back_when_native_empty():
    """When native returns [], Firecrawl is called."""
    scraper = Inmuebles24Scraper(CONFIG)
    fc_result = [MagicMock()]
    with patch.object(scraper, "_try_native_httpx", return_value=[]):
        with patch.object(scraper, "_try_firecrawl_html", return_value=fc_result) as mock_fc:
            result = scraper.fetch_listings()
    assert result == fc_result
    mock_fc.assert_called_once()


def test_fetch_listings_falls_back_on_native_exception():
    """When native raises, Firecrawl is called."""
    scraper = Inmuebles24Scraper(CONFIG)
    fc_result = [MagicMock()]
    with patch.object(scraper, "_try_native_httpx", side_effect=RuntimeError("403")):
        with patch.object(scraper, "_try_firecrawl_html", return_value=fc_result) as mock_fc:
            result = scraper.fetch_listings()
    assert result == fc_result
    mock_fc.assert_called_once()


def test_native_httpx_parses_cards_from_adapter():
    """_try_native_httpx calls fetch_html per neighborhood and parses cards."""
    from scrapers.httpx_adapter import HttpxAdapter
    scraper = Inmuebles24Scraper(CONFIG)
    with patch.object(HttpxAdapter, "fetch_html", return_value=_CARD_HTML):
        results = scraper._try_native_httpx()
    # 14 neighborhoods × 1 card each (mocked same HTML for all; Naucalpan added 2026-04-15)
    assert len(results) == 14
    assert all(r.source == "inmuebles24" for r in results)
