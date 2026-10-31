"""Parametrized tests for native httpx-based scrapers (Wave 3 Task 5)."""
from unittest.mock import patch

import pytest

from scrapers.casa_mx_native import CasaMxNative
from scrapers.propiedades_native import PropiedadesNative
from scrapers.trovit_native import TrovitNative
from scrapers.vivanuncios_native import VivanunciosNative

SAMPLE_HTML = """<html><head>
<script type="application/ld+json">
[{"@type":"Apartment","url":"https://x.com/1",
 "address":{"streetAddress":"Av X 1","addressLocality":"Polanco"},
 "numberOfBedrooms":2,"numberOfBathroomsTotal":2,
 "offers":{"price":"40000","priceCurrency":"MXN"},
 "image":["https://x.com/1.jpg"]}]
</script></head><body></body></html>"""

# Trovit uses HTML article parsing (not JSON-LD/microdata) — excluded from
# shared parametrize groups and tested separately below.
_JSONLD_SCRAPERS = [VivanunciosNative, PropiedadesNative, CasaMxNative]


@pytest.mark.parametrize("Cls", _JSONLD_SCRAPERS)
def test_native_scraper_parses_jsonld(Cls):
    config = {
        "criteria": {
            "neighborhoods": ["Polanco"],
            "price_min": 30000,
            "price_max": 45000,
        }
    }
    s = Cls(config)
    with patch.object(s._adapter, "fetch_html", return_value=SAMPLE_HTML):
        listings = s.fetch_listings()
    assert listings, "should yield at least one listing"
    assert listings[0].neighborhood == "Polanco"
    assert listings[0].price == 40000


# ---- microdata fallback (JSON-LD empty) ----
MICRODATA_HTML = """<html><body>
  <div itemscope itemtype="https://schema.org/Apartment">
    <a itemprop="url" href="https://x.com/2">apt 2</a>
    <span itemprop="numberOfBedrooms">2</span>
    <span itemprop="numberOfBathroomsTotal">1</span>
    <div itemprop="address" itemscope itemtype="https://schema.org/PostalAddress">
      <span itemprop="streetAddress">Av Y 2</span>
      <span itemprop="addressLocality">Polanco</span>
    </div>
    <div itemprop="offers" itemscope itemtype="https://schema.org/Offer">
      <meta itemprop="price" content="42000">
    </div>
  </div>
</body></html>"""


@pytest.mark.parametrize("Cls", _JSONLD_SCRAPERS)
def test_native_scraper_microdata_fallback(Cls):
    """When JSON-LD is missing, scrapers must parse schema.org microdata."""
    config = {
        "criteria": {
            "neighborhoods": ["Polanco"],
            "price_min": 30000,
            "price_max": 45000,
        }
    }
    s = Cls(config)
    with patch.object(s._adapter, "fetch_html", return_value=MICRODATA_HTML):
        listings = s.fetch_listings()
    assert listings, f"{Cls.__name__} should fall back to microdata"
    assert listings[0].neighborhood == "Polanco"
    assert listings[0].price == 42000


# ---- Trovit HTML article parser ----

TROVIT_HTML = """<html><body>
<article class="snippet-listing" data-id="abc-123-def-456">
  <a href="https://casas.trovit.com.mx/detail/019a1234-abcd-ef01-2345-678901234567"
     title="Departamento en Renta en Polanco" class="js-listing">
    <img src="https://img.trovit.com/photo1.jpg" loading="eager">
    <img src="https://img.trovit.com/photo2.jpg" loading="eager">
  </a>
  <span class="price">$ 38,000 MXN/mes</span>
  <span class="price">$ 38,000 MXN/mes</span>
  <div>2 recamaras 2 baños</div>
  <div>Departamento en Polanco, Ciudad de Mexico · Av. Presidente</div>
</article>
<article class="snippet-listing" data-id="xyz-999">
  <a href="https://casas.trovit.com.mx/detail/019b5678-abcd-ef01-2345-999901234567"
     title="Depto Roma Norte" class="js-listing">
    <img src="https://img.trovit.com/photo3.jpg">
  </a>
  <span class="price">$ 42,500 MXN/mes</span>
  <div>3 recamaras 2 baños</div>
  <div>Departamento en Roma Norte, Ciudad de Mexico</div>
</article>
</body></html>"""


def test_trovit_parses_html_articles():
    config = {"criteria": {"price_min": 30000, "price_max": 45000, "neighborhoods": []}}
    s = TrovitNative(config)
    with patch.object(s._adapter, "fetch_html", return_value=TROVIT_HTML):
        listings = s.fetch_listings()
    assert len(listings) == 2
    l0, l1 = listings
    assert l0.price == 38000
    assert l0.bedrooms == 2
    assert l0.neighborhood == "Polanco"
    assert len(l0.photos) == 2
    assert "detail/" in l0.url
    assert l1.price == 42500
    assert l1.bedrooms == 3
    assert l1.neighborhood == "Roma Norte"


def test_trovit_skips_out_of_range_price():
    """Articles with prices outside 5k-200k MXN are silently dropped."""
    html = TROVIT_HTML.replace("$ 38,000 MXN/mes", "$ 200 MXN/mes")
    config = {"criteria": {"price_min": 30000, "price_max": 45000, "neighborhoods": []}}
    s = TrovitNative(config)
    with patch.object(s._adapter, "fetch_html", return_value=html):
        listings = s.fetch_listings()
    assert all(l.price != 200 for l in listings)


def test_trovit_empty_html_returns_empty():
    config = {"criteria": {"price_min": 30000, "price_max": 45000, "neighborhoods": []}}
    s = TrovitNative(config)
    with patch.object(s._adapter, "fetch_html", return_value=""):
        listings = s.fetch_listings()
    assert listings == []


# ---- VivanunciosLightpanda ----

from scrapers.vivanuncios_lightpanda import VivanunciosLightpanda  # noqa: E402

_LP_CONFIG = {
    "criteria": {
        "neighborhoods": ["Polanco"],
        "price_min": 30000,
        "price_max": 45000,
    }
}


def test_lightpanda_parses_jsonld():
    """VivanunciosLightpanda parses JSON-LD from Lightpanda-rendered HTML."""
    scraper = VivanunciosLightpanda(_LP_CONFIG)
    with patch.object(scraper, "_fetch_html", return_value=SAMPLE_HTML):
        listings = scraper.fetch_listings()
    assert listings, "should yield at least one listing"
    assert listings[0].price == 40000
    assert listings[0].neighborhood == "Polanco"


def test_lightpanda_microdata_fallback():
    """VivanunciosLightpanda falls back to microdata when JSON-LD is absent."""
    scraper = VivanunciosLightpanda(_LP_CONFIG)
    with patch.object(scraper, "_fetch_html", return_value=MICRODATA_HTML):
        listings = scraper.fetch_listings()
    assert listings, "should fall back to microdata"
    assert listings[0].price == 42000


def test_lightpanda_empty_html_returns_empty():
    """VivanunciosLightpanda returns [] when Lightpanda yields no HTML."""
    scraper = VivanunciosLightpanda(_LP_CONFIG)
    with patch.object(scraper, "_fetch_html", return_value=""):
        listings = scraper.fetch_listings()
    assert listings == []


def test_lightpanda_subprocess_timeout_returns_empty():
    """subprocess.TimeoutExpired is caught — returns [] without raising."""
    import subprocess
    scraper = VivanunciosLightpanda(_LP_CONFIG)
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("wsl", 45)):
        listings = scraper.fetch_listings()
    assert listings == []


def test_lightpanda_subprocess_wsl_missing_returns_empty():
    """FileNotFoundError (wsl not found) is caught — returns []."""
    scraper = VivanunciosLightpanda(_LP_CONFIG)
    with patch("subprocess.run", side_effect=FileNotFoundError("wsl not found")):
        listings = scraper.fetch_listings()
    assert listings == []


def test_vivanuncios_scraper_falls_back_to_lightpanda():
    """VivanunciosScraper T3: when native returns [], Lightpanda is invoked.

    Verified by: patching VivanunciosNative to return [] and patching
    VivanunciosLightpanda._fetch_html to return rendered HTML — the final
    listing count proves Lightpanda ran and parsed results.
    """
    from scrapers.vivanuncios import VivanunciosScraper

    scraper = VivanunciosScraper(_LP_CONFIG)
    with (
        patch("scrapers.vivanuncios_native.VivanunciosNative.fetch_listings", return_value=[]),
        patch(
            "scrapers.vivanuncios_lightpanda.VivanunciosLightpanda._fetch_html",
            return_value=SAMPLE_HTML,
        ),
    ):
        result = scraper.fetch_listings()

    # Lightpanda parsed SAMPLE_HTML → 1 listing from Polanco
    assert len(result) == 1
    assert result[0].price == 40000
