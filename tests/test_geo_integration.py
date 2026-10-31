"""Integration tests for the geocoding / title trace pipeline.

Covers the full contract:
1. RawListing carries ``title``, ``latitude``, ``longitude`` through ``to_db_dict``.
2. ``db.upsert_listing`` persists them.
3. ``favorites._resolved_coords`` prefers real coords over centroid+jitter.
4. ``MercadoLibreScraper._item_to_listing`` extracts geo + name from JSON-LD.
"""

import sqlite3

import db
from favorites import _resolved_coords
from scrapers.base import RawListing
from scrapers.mercadolibre import MercadoLibreScraper


def test_rawlisting_carries_geo_and_title(tmp_path):
    l = RawListing(
        id="t:1", source="t", url="https://x.com/1", price=32000,
        bedrooms=2, bathrooms=2, floor=5, neighborhood="Polanco",
        address="Horacio 10",
        title="Depto 2br en Polanco con balcón",
        latitude=19.4312, longitude=-99.2001,
    )
    d = l.to_db_dict()
    assert d["title"] == "Depto 2br en Polanco con balcón"
    assert d["latitude"] == 19.4312
    assert d["longitude"] == -99.2001
    assert d["address"] == "Horacio 10"
    # Persistence path accepts the new columns.
    conn = db.connect(str(tmp_path / "t.db"))
    db.upsert_listing(conn, d)
    row = conn.execute(
        "SELECT title, latitude, longitude, address FROM listings WHERE id=?", ("t:1",)
    ).fetchone()
    conn.close()
    assert row == ("Depto 2br en Polanco con balcón", 19.4312, -99.2001, "Horacio 10")


def test_resolved_coords_prefers_real_over_centroid():
    # Real coords win.
    coords, precision = _resolved_coords("ml:abc", "Polanco", 19.42, -99.19)
    assert coords == [19.42, -99.19]
    assert precision == "exact"

    # No real coords → centroid + deterministic jitter; precision marked approx.
    coords2, precision2 = _resolved_coords("ml:abc", "Polanco", None, None)
    assert precision2 == "approx"
    # Jitter is seeded by id → stable across calls.
    coords3, _ = _resolved_coords("ml:abc", "Polanco", None, None)
    assert coords2 == coords3

    # Different id → different jittered spot so markers don't stack.
    coords4, _ = _resolved_coords("ml:xyz", "Polanco", None, None)
    assert coords4 != coords2


def test_mercadolibre_item_to_listing_extracts_geo_and_name():
    scraper = MercadoLibreScraper(
        config={"criteria": {"price_min": 30000, "price_max": 45000, "bedrooms": 2}, "sources": {}}
    )
    item = {
        "@type": "RealEstateListing",
        "name": "Depto con balcón Amsterdam",
        "offers": {"price": 41000, "url": "https://ml.com/MLM-9-condesa-amsterdam"},
        "image": "https://img.com/a-C.jpg",
        "description": "Amsterdam, Condesa, 2 recamaras, 80 m², piso 3",
        "geo": {"latitude": 19.4116, "longitude": -99.1783},
    }
    listing = scraper._item_to_listing(item, "Roma Norte")  # query bucket says Roma
    assert listing is not None
    # URL slug contains 'condesa' so retag kicks in
    assert listing.neighborhood == "Condesa"
    assert listing.title.startswith("Depto con balcón")
    assert listing.latitude == 19.4116
    assert listing.longitude == -99.1783


def test_mercadolibre_rejects_out_of_region_geo():
    """Coords outside CDMX bbox are treated as unknown — caller sees None."""
    scraper = MercadoLibreScraper(
        config={"criteria": {"price_min": 30000, "price_max": 45000, "bedrooms": 2}, "sources": {}}
    )
    item = {
        "@type": "RealEstateListing",
        "name": "Suspicious listing",
        "offers": {"price": 41000, "url": "https://ml.com/MLM-9-polanco"},
        "image": "https://img.com/a-C.jpg",
        "description": "2 recamaras",
        "geo": {"latitude": 40.7128, "longitude": -74.0060},
    }
    listing = scraper._item_to_listing(item, "Polanco")
    assert listing.latitude is None
    assert listing.longitude is None
