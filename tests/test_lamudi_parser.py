"""Lamudi JSON-LD field-extraction tests.

Targets the gap that lets Lamudi listings ship with sqm/floor/baths/geo/furnished
all unknown — passing every filter that treats None as "ok".
"""
from scrapers.lamudi import LamudiScraper

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


def _scraper():
    return LamudiScraper(CONFIG)


def test_jsonld_to_listing_extracts_floor_size_quantitative_value():
    """Lamudi JSON-LD uses schema.org floorSize as QuantitativeValue with value+unitCode."""
    item = {
        "@type": "Apartment",
        "url": "https://www.lamudi.com.mx/detalle/test-1",
        "numberOfBedrooms": 2,
        "address": {"streetAddress": "Test 1", "addressLocality": "Polanco"},
        "floorSize": {"@type": "QuantitativeValue", "value": 85, "unitCode": "MTK"},
    }
    listing = _scraper()._jsonld_to_listing(item, "Polanco")
    assert listing is not None
    assert listing.sqm == 85.0


def test_jsonld_to_listing_extracts_floor_size_scalar():
    """Some pages emit floorSize as a bare number — must still parse."""
    item = {
        "@type": "Apartment",
        "url": "https://www.lamudi.com.mx/detalle/test-2",
        "numberOfBedrooms": 2,
        "address": {"streetAddress": "Test 2", "addressLocality": "Polanco"},
        "floorSize": 92,
    }
    listing = _scraper()._jsonld_to_listing(item, "Polanco")
    assert listing.sqm == 92.0


def test_jsonld_to_listing_extracts_bathrooms():
    item = {
        "@type": "Apartment",
        "url": "https://www.lamudi.com.mx/detalle/test-3",
        "numberOfBedrooms": 2,
        "numberOfBathroomsTotal": 2,
        "address": {"streetAddress": "Test 3", "addressLocality": "Polanco"},
    }
    listing = _scraper()._jsonld_to_listing(item, "Polanco")
    assert listing.bathrooms == 2


def test_jsonld_to_listing_extracts_floor_level():
    item = {
        "@type": "Apartment",
        "url": "https://www.lamudi.com.mx/detalle/test-4",
        "numberOfBedrooms": 2,
        "address": {"streetAddress": "Test 4", "addressLocality": "Polanco"},
        "floorLevel": "5",
    }
    listing = _scraper()._jsonld_to_listing(item, "Polanco")
    assert listing.floor == 5


def test_jsonld_to_listing_extracts_geo_coordinates():
    item = {
        "@type": "Apartment",
        "url": "https://www.lamudi.com.mx/detalle/test-5",
        "numberOfBedrooms": 2,
        "address": {"streetAddress": "Test 5", "addressLocality": "Polanco"},
        "geo": {"@type": "GeoCoordinates", "latitude": 19.4326, "longitude": -99.1332},
    }
    listing = _scraper()._jsonld_to_listing(item, "Polanco")
    assert listing.latitude == 19.4326
    assert listing.longitude == -99.1332


def test_jsonld_to_listing_rejects_geo_outside_cdmx_bbox():
    """Geo gate must drop coords outside CDMX+suburbs bounding box (e.g. Cancun)."""
    item = {
        "@type": "Apartment",
        "url": "https://www.lamudi.com.mx/detalle/test-6",
        "numberOfBedrooms": 2,
        "address": {"streetAddress": "Test 6", "addressLocality": "Polanco"},
        "geo": {"@type": "GeoCoordinates", "latitude": 21.16, "longitude": -86.85},
    }
    listing = _scraper()._jsonld_to_listing(item, "Polanco")
    assert listing.latitude is None
    assert listing.longitude is None


def test_jsonld_to_listing_marks_furnished_from_amenity_feature():
    """schema.org amenityFeature with name 'Furnished' (or 'Amueblado') and value True."""
    item = {
        "@type": "Apartment",
        "url": "https://www.lamudi.com.mx/detalle/test-7",
        "numberOfBedrooms": 2,
        "address": {"streetAddress": "Test 7", "addressLocality": "Polanco"},
        "amenityFeature": [
            {"@type": "LocationFeatureSpecification", "name": "Furnished", "value": True}
        ],
    }
    listing = _scraper()._jsonld_to_listing(item, "Polanco")
    assert listing.nice_to_haves.get("furnished") is True


def test_jsonld_to_listing_marks_furnished_from_spanish_amenity():
    item = {
        "@type": "Apartment",
        "url": "https://www.lamudi.com.mx/detalle/test-8",
        "numberOfBedrooms": 2,
        "address": {"streetAddress": "Test 8", "addressLocality": "Polanco"},
        "amenityFeature": [
            {"@type": "LocationFeatureSpecification", "name": "Amueblado", "value": True}
        ],
    }
    listing = _scraper()._jsonld_to_listing(item, "Polanco")
    assert listing.nice_to_haves.get("furnished") is True


def test_jsonld_to_listing_unfurnished_when_amenity_absent():
    item = {
        "@type": "Apartment",
        "url": "https://www.lamudi.com.mx/detalle/test-9",
        "numberOfBedrooms": 2,
        "address": {"streetAddress": "Test 9", "addressLocality": "Polanco"},
    }
    listing = _scraper()._jsonld_to_listing(item, "Polanco")
    # Field present but False — distinguishable from "unknown"
    assert listing.nice_to_haves.get("furnished") is False


def test_jsonld_to_listing_handles_missing_optional_fields_gracefully():
    """Bare-minimum item (just url + bedrooms) must still produce a listing without raising."""
    item = {
        "@type": "Apartment",
        "url": "https://www.lamudi.com.mx/detalle/test-10",
        "numberOfBedrooms": 2,
        "address": {"streetAddress": "Test 10", "addressLocality": "Polanco"},
    }
    listing = _scraper()._jsonld_to_listing(item, "Polanco")
    assert listing is not None
    assert listing.sqm is None
    assert listing.floor is None
    assert listing.bathrooms == 0
    assert listing.latitude is None
    assert listing.longitude is None
