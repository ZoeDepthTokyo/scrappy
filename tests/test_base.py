from scrapers.base import RawListing, BaseScraper  # noqa: F401


def test_rawlisting_address_hash_deterministic():
    l1 = RawListing(
        id="x",
        source="s",
        url="u",
        price=30000,
        bedrooms=2,
        bathrooms=2,
        floor=4,
        neighborhood="Polanco",
        address="Emilio Castelar 145",
        photos=[],
    )
    l2 = RawListing(
        id="y",
        source="s2",
        url="u2",
        price=30000,
        bedrooms=2,
        bathrooms=2,
        floor=4,
        neighborhood="Polanco",
        address="Emilio Castelar 145",
        photos=[],
    )
    assert l1.address_hash == l2.address_hash


def test_rawlisting_to_db_dict_has_required_keys():
    l = RawListing(
        id="i24:1",
        source="inmuebles24",
        url="http://x.com",
        price=32000,
        bedrooms=2,
        bathrooms=2,
        floor=5,
        neighborhood="Polanco",
        address="Presidente Masaryk 10",
        photos=["http://img.com/1.jpg"],
    )
    d = l.to_db_dict()
    for key in [
        "id",
        "source",
        "url",
        "price",
        "bedrooms",
        "bathrooms",
        "floor",
        "neighborhood",
        "address_hash",
        "photos_json",
        "first_seen",
        "last_seen",
    ]:
        assert key in d, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# W3-T5: detail-page coordinate extraction (RC#19)
# ---------------------------------------------------------------------------


def test_extract_coords_jsonld():
    from scrapers.base import _extract_coords_from_detail
    import httpx

    html = (
        '<html><head><script type="application/ld+json">'
        '{"@type":"Apartment","geo":{"latitude":19.4310,"longitude":-99.1993}}'
        '</script></head></html>'
    )

    class FakeClient:
        def get(self, url, **kw):
            return httpx.Response(200, text=html)

    r = _extract_coords_from_detail("https://fake.com/1", FakeClient())
    assert r is not None
    assert abs(r[0] - 19.431) < 0.001 and abs(r[1] - (-99.1993)) < 0.001


def test_extract_coords_regex_latlng():
    from scrapers.base import _extract_coords_from_detail
    import httpx

    html = '<html><body><script>google.maps.LatLng(19.4116, -99.1783);</script></body></html>'

    class FakeClient:
        def get(self, url, **kw):
            return httpx.Response(200, text=html)

    r = _extract_coords_from_detail("https://fake.com/2", FakeClient())
    assert r is not None
    assert abs(r[0] - 19.4116) < 0.001


def test_extract_coords_rejects_non_cdmx():
    from scrapers.base import _extract_coords_from_detail
    import httpx

    html = '<html><body><script>google.maps.LatLng(40.7128, -74.0060);</script></body></html>'

    class FakeClient:
        def get(self, url, **kw):
            return httpx.Response(200, text=html)

    r = _extract_coords_from_detail("https://fake.com/3", FakeClient())
    assert r is None
