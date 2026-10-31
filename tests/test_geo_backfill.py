"""Tests for the JSON-LD geocode / title backfill."""

import base64
import json

from geo_backfill import extract_from_html, extract_i24_base64_geo


def _jsonld_html(payload: dict) -> str:
    return (
        '<html><head>'
        '<script type="application/ld+json">' + json.dumps(payload) + '</script>'
        '</head><body></body></html>'
    )


def test_extract_from_apartment_with_geo():
    html = _jsonld_html({
        "@type": "Apartment",
        "name": "Depto 2 rec en Polanco con balcón",
        "geo": {"latitude": 19.4312, "longitude": -99.2001},
        "address": {"streetAddress": "Horacio 1023, Polanco"},
    })
    out = extract_from_html(html)
    assert out["latitude"] == 19.4312
    assert out["longitude"] == -99.2001
    assert out["title"].startswith("Depto 2 rec")
    assert "Horacio" in out["address"]


def test_extract_from_real_estate_listing_graph():
    html = _jsonld_html({
        "@graph": [
            {"@type": "Organization", "name": "MercadoLibre"},
            {
                "@type": "RealEstateListing",
                "name": "Renta exclusivo departamento en excelente ubicación",
                "geo": {"latitude": 19.4110, "longitude": -99.1790},
                "offers": {"price": 41000},
            },
        ]
    })
    out = extract_from_html(html)
    assert out["latitude"] == 19.4110
    assert "excelente" in out["title"].lower()


def test_extract_rejects_out_of_region_coords():
    """Sanity gate: coords outside CDMX bbox must not be returned."""
    html = _jsonld_html({
        "@type": "Apartment",
        "name": "Spam listing",
        "geo": {"latitude": 40.7128, "longitude": -74.0060},  # New York
    })
    out = extract_from_html(html)
    assert out.get("latitude") is None
    # Title still extracted though — only geo is rejected.
    assert out.get("title") == "Spam listing"


def test_extract_returns_empty_on_no_jsonld():
    out = extract_from_html("<html><body>No structured data here.</body></html>")
    assert out == {}


def test_extract_nested_address_dict():
    html = _jsonld_html({
        "@type": "Apartment",
        "name": "Condesa 2br",
        "address": {"@type": "PostalAddress", "streetAddress": "Av. Amsterdam 200"},
    })
    out = extract_from_html(html)
    assert out["address"] == "Av. Amsterdam 200"


def _b64(v: str) -> str:
    return base64.b64encode(v.encode("ascii")).decode("ascii")


def test_i24_base64_geo_extraction():
    """Inmuebles24 detail pages carry base64-encoded mapLatOf/mapLngOf vars.
    Confirmed live 2026-04-16 against a Parques Plaza listing."""
    html = (
        "<html><body><script>\n"
        f'    const mapLatOf =  "{_b64("19.438471699999997")}";\n'
        f'    const mapLngOf =  "{_b64("-99.178385800000000")}";\n'
        "    \'address\': {\"name\":\"Lago Alberto 442\",\"visibility\":\"EXACT\"}\n"
        "</script>"
        '<meta property="og:title" content="Departamento en Renta en Parques Plaza">'
        "</body></html>"
    )
    out = extract_i24_base64_geo(html)
    assert out["latitude"] == 19.438471699999997
    assert out["longitude"] == -99.178385800000000
    assert out["address"] == "Lago Alberto 442"
    assert "Parques Plaza" in out["title"]


def test_i24_base64_rejects_out_of_region():
    """Coords decoded outside CDMX bbox get dropped."""
    html = (
        "<script>\n"
        f'const mapLatOf =  "{_b64("40.7128")}";\n'
        f'const mapLngOf =  "{_b64("-74.0060")}";\n'
        "</script>"
    )
    out = extract_i24_base64_geo(html)
    assert out.get("latitude") is None
    assert out.get("longitude") is None


def test_i24_base64_returns_empty_when_vars_absent():
    """Plain i24 page with no map vars → empty dict, no crash."""
    out = extract_i24_base64_geo("<html><body>nothing to see</body></html>")
    assert out == {}
