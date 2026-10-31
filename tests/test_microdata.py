"""Tests for MicrodataExtractor — parses schema.org itemprop/itemtype HTML.

TDD: these tests specify the extractor's contract before implementation.
"""
from scrapers.microdata_extractor import MicrodataExtractor


APT_HTML = """
<html><body>
  <div itemscope itemtype="https://schema.org/Apartment">
    <a itemprop="url" href="https://casa.example/listing/1">apt 1</a>
    <span itemprop="numberOfBedrooms">2</span>
    <span itemprop="numberOfBathroomsTotal">1</span>
    <div itemprop="address" itemscope itemtype="https://schema.org/PostalAddress">
      <span itemprop="streetAddress">Av Reforma 100</span>
      <span itemprop="addressLocality">Polanco</span>
    </div>
    <div itemprop="offers" itemscope itemtype="https://schema.org/Offer">
      <meta itemprop="price" content="40000">
      <meta itemprop="priceCurrency" content="MXN">
    </div>
    <img itemprop="image" src="https://casa.example/1.jpg">
  </div>
  <div itemscope itemtype="http://schema.org/House">
    <a itemprop="url" href="https://casa.example/listing/2"></a>
    <meta itemprop="price" content="35000">
    <span itemprop="addressLocality">Condesa</span>
  </div>
  <div itemscope itemtype="https://schema.org/Person">
    <span itemprop="name">Ignore me</span>
  </div>
</body></html>
"""


def test_microdata_extracts_apartment_and_house():
    items = MicrodataExtractor.extract(APT_HTML)
    # Apartment + House should be extracted; Person ignored
    assert len(items) == 2
    by_url = {i.get("url"): i for i in items}
    apt = by_url["https://casa.example/listing/1"]
    assert apt["numberOfBedrooms"] == "2"
    assert apt["numberOfBathroomsTotal"] == "1"
    addr = apt["address"]
    assert isinstance(addr, dict)
    assert addr["streetAddress"] == "Av Reforma 100"
    assert addr["addressLocality"] == "Polanco"
    offers = apt["offers"]
    assert offers["price"] == "40000"
    # image resolved to a string URL
    img = apt["image"]
    assert img == "https://casa.example/1.jpg" or img == ["https://casa.example/1.jpg"]


def test_microdata_empty_html_returns_empty():
    assert MicrodataExtractor.extract("") == []
    assert MicrodataExtractor.extract("<html><body>nothing</body></html>") == []


def test_microdata_ignores_non_listing_types():
    html = (
        '<div itemscope itemtype="https://schema.org/Article">'
        '<span itemprop="headline">News</span></div>'
    )
    assert MicrodataExtractor.extract(html) == []
