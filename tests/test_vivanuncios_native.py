"""Unit tests for vivanuncios_native._parse_olx_next_data.

Covers the two structural patterns fixed in 2026-04-18:
  1. Nested ad list (dehydratedState path) — exercises list recursion +
     "link" key fallback in _find_ad_list.
  2. Fast path (props.pageProps.listingProps.listingAds) — confirms the
     known-path shortcut still works with "url" key ads.
"""
import json

import pytest

from scrapers.vivanuncios_native import _parse_olx_next_data


def _wrap_next_data(payload: dict) -> str:
    blob = json.dumps({"props": {"pageProps": payload}})
    return f'<script id="__NEXT_DATA__" type="application/json">{blob}</script>'


def _make_ad_link(price: int, hood: str, link: str) -> dict:
    return {"link": link, "price": price, "title": f"Depto en {hood}", "images": []}


def _make_ad_url(price: int, hood: str, url: str) -> dict:
    return {"url": url, "price": price, "title": f"Depto en {hood}", "images": []}


# ---------------------------------------------------------------------------
# Test 1 — nested path with "link" key (list recursion branch)
# ---------------------------------------------------------------------------

def test_parse_nested_dehydrated_state_link_key():
    """Ads nested under dehydratedState.queries[N].state.data.ads using 'link'."""
    ad = _make_ad_link(42000, "Polanco", "/anuncio/depto-polanco/123")
    payload = {
        "dehydratedState": {
            "queries": [
                {"state": {"data": {"ads": [ad]}}},
            ]
        }
    }
    html = _wrap_next_data(payload)
    results = _parse_olx_next_data(html, "vivanuncios", fallback_hood="Polanco")
    assert len(results) >= 1, "Expected at least 1 listing from nested dehydratedState path"
    r = results[0]
    assert "vivanuncios.com.mx" in r.url, f"Expected absolute URL, got: {r.url}"
    assert r.price == 42000


# ---------------------------------------------------------------------------
# Test 2 — fast path with "url" key (props.pageProps.listingProps.listingAds)
# ---------------------------------------------------------------------------

def test_parse_fast_path_url_key():
    """Ads at props.pageProps.listingProps.listingAds using 'url' key."""
    ad = _make_ad_url(38500, "Condesa", "https://www.vivanuncios.com.mx/anuncio/condesa/456")
    payload = {"listingProps": {"listingAds": [ad]}}
    html = _wrap_next_data(payload)
    results = _parse_olx_next_data(html, "vivanuncios", fallback_hood="Condesa")
    assert len(results) >= 1, "Expected at least 1 listing from fast-path listingAds"
    r = results[0]
    assert r.url == "https://www.vivanuncios.com.mx/anuncio/condesa/456"
    assert r.price == 38500


# ---------------------------------------------------------------------------
# Test 3 — no __NEXT_DATA__ returns empty list (guard)
# ---------------------------------------------------------------------------

def test_parse_no_next_data_returns_empty():
    results = _parse_olx_next_data("<html><body>No data here</body></html>", "vivanuncios")
    assert results == []


# ---------------------------------------------------------------------------
# Test 4 — price outside range is filtered out
# ---------------------------------------------------------------------------

def test_parse_price_out_of_range_filtered():
    """Ads with price < 5000 or > 200000 are dropped by the extractor."""
    ad_cheap = _make_ad_url(100, "Roma Norte", "https://www.vivanuncios.com.mx/a/1")
    ad_exp = _make_ad_url(999999, "Roma Norte", "https://www.vivanuncios.com.mx/a/2")
    payload = {"listingProps": {"listingAds": [ad_cheap, ad_exp]}}
    html = _wrap_next_data(payload)
    results = _parse_olx_next_data(html, "vivanuncios", fallback_hood="Roma Norte")
    assert results == [], f"Expected empty after price filter, got {results}"
