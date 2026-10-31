from scrapers.base import RawListing
from pipeline.dedup import deduplicate
from pipeline.filter import hard_filter
from pipeline.ranker import rank


def _listing(**kwargs):
    defaults = dict(
        id="x",
        source="s",
        url="u",
        price=32000,
        bedrooms=2,
        bathrooms=2,
        floor=5,
        neighborhood="Polanco",
        address="Masaryk 10",
        photos=["http://img.com/a.jpg"],
    )
    return RawListing(**{**defaults, **kwargs})


def test_dedup_removes_same_address_hash():
    l1 = _listing(id="i24:1", source="inmuebles24")
    l2 = _listing(id="mc:1", source="metroscubicos")  # same price+address
    result = deduplicate([l1, l2])
    assert len(result) == 1


def test_dedup_keeps_different_listings():
    l1 = _listing(id="i24:1", address="Masaryk 10", url="https://a.com/1")
    l2 = _listing(id="i24:2", address="Horacio 20", url="https://a.com/2")
    result = deduplicate([l1, l2])
    assert len(result) == 2


def test_dedup_url_gate_catches_same_url_different_address():
    """Same URL from two Lamudi queries (e.g. neighborhood + delegation-level) deduped via URL gate."""
    l1 = _listing(id="lm:1", url="https://lamudi.com/a", address="Masaryk 123")
    l2 = _listing(id="lm:1b", url="https://lamudi.com/a", address="Av. Masaryk 123, Polanco")
    result = deduplicate([l1, l2])
    assert len(result) == 1


def test_dedup_cross_run_blocks_known_url():
    """A listing whose URL is already in the DB must not resurface on a later run."""
    l1 = _listing(id="new:1", url="https://a.com/same", address="Masaryk 5")
    result = deduplicate([l1], known_urls={"https://a.com/same"})
    assert result == [], "Known URL should be rejected by cross-run gate"


def test_dedup_cross_run_normalizes_known_url_variants():
    """Tracking params, fragments, trailing slashes, and www. must not bypass cross-run dedup."""
    l1 = _listing(
        id="new:1",
        url="https://example.com/listing/?utm_source=tg#top",
        address="Masaryk 5",
    )
    result = deduplicate([l1], known_urls={"https://www.example.com/listing"})
    assert result == [], "Equivalent URL variants should hit the same cross-run gate"


def test_dedup_cross_run_blocks_known_hash():
    """A listing whose address_hash already exists in DB must be rejected even if URL is new.

    Mirrors the production bug where ml:3cae... and ml:737c... were inserted as
    separate rows with identical address_hash across different runs, producing
    duplicate Telegram alerts for the same property."""
    l1 = _listing(id="new:1", url="https://a.com/new", address="Masaryk 10")
    # Pre-compute the hash the same way RawListing does
    prior_hash = _listing(address="Masaryk 10").address_hash
    result = deduplicate([l1], known_hashes={prior_hash})
    assert result == [], "Known address_hash should be rejected by cross-run gate"


def test_dedup_cross_run_allows_genuinely_new_listing():
    """Cross-run seeding must not over-reject — truly new listings still flow through."""
    l1 = _listing(id="new:1", url="https://a.com/new", address="Horacio 99")
    result = deduplicate(
        [l1],
        known_urls={"https://a.com/old"},
        known_hashes={"deadbeef" * 4},
    )
    assert len(result) == 1


def test_dedup_url_gate_normalizes_same_listing_variants():
    """Same-run dedup should treat host/tracking variants as the same listing URL."""
    l1 = _listing(id="lm:1", url="https://www.lamudi.com/a/?utm_source=alerts")
    l2 = _listing(id="lm:1b", url="https://lamudi.com/a", address="Av. Masaryk 123, Polanco")
    result = deduplicate([l1, l2])
    assert len(result) == 1


def test_dedup_geo_gate_catches_same_building():
    """Same price + same rounded lat/lng from different sources must dedup."""
    l1 = _listing(id="i24:g1", source="inmuebles24", url="https://a.com/1",
                  address="Masaryk 123, Polanco")
    l1.latitude = 19.43101; l1.longitude = -99.19931
    l2 = _listing(id="lm:g1", source="lamudi", url="https://b.com/1",
                  address="Av. Masaryk 123")
    l2.latitude = 19.43104; l2.longitude = -99.19934  # rounds to same 4dp
    result = deduplicate([l1, l2])
    assert len(result) == 1


def test_dedup_geo_gate_keeps_different_price():
    """Same coords but different price must not dedup."""
    l1 = _listing(id="i24:g2", url="https://a.com/2", price=35000)
    l1.latitude = 19.4310; l1.longitude = -99.1993
    l2 = _listing(id="i24:g3", url="https://a.com/3", price=38000)
    l2.latitude = 19.4310; l2.longitude = -99.1993
    result = deduplicate([l1, l2])
    assert len(result) == 2


def test_filter_rejects_floor_1():
    l = _listing(floor=1)
    assert (
        hard_filter(
            [l],
            {
                "criteria": {
                    "min_floor": 3,
                    "price_min": 30000,
                    "price_max": 45000,
                    "bedrooms": 2,
                    "bathrooms": 2,
                    "require_photos": True,
                }
            },
        )
        == []
    )


def test_filter_rejects_floor_2():
    l = _listing(floor=2)
    assert (
        hard_filter(
            [l],
            {
                "criteria": {
                    "min_floor": 3,
                    "price_min": 30000,
                    "price_max": 45000,
                    "bedrooms": 2,
                    "bathrooms": 2,
                    "require_photos": True,
                }
            },
        )
        == []
    )


def test_filter_rejects_no_photos():
    l = _listing(photos=[])
    assert (
        hard_filter(
            [l],
            {
                "criteria": {
                    "min_floor": 3,
                    "price_min": 30000,
                    "price_max": 45000,
                    "bedrooms": 2,
                    "bathrooms": 2,
                    "require_photos": True,
                }
            },
        )
        == []
    )


def test_filter_passes_valid_listing():
    l = _listing(floor=5, price=35000, photos=["http://img.com/1.jpg"])
    result = hard_filter(
        [l],
        {
            "criteria": {
                "min_floor": 3,
                "price_min": 30000,
                "price_max": 45000,
                "bedrooms": 2,
                "bathrooms": 2,
                "require_photos": True,
            }
        },
    )
    assert len(result) == 1


def test_filter_passes_none_floor():
    """Listings with unknown floor (None) must not be rejected by floor check."""
    l = _listing(floor=None, price=35000, photos=["http://img.com/1.jpg"])
    result = hard_filter(
        [l],
        {
            "criteria": {
                "min_floor": 3,
                "price_min": 30000,
                "price_max": 45000,
                "bedrooms": 2,
                "bathrooms": 2,
                "require_photos": True,
            }
        },
    )
    assert len(result) == 1


def test_filter_passes_unknown_bedrooms():
    """0 bedrooms means unknown and should not be rejected by the bedroom gate."""
    l = _listing(bedrooms=0)
    result = hard_filter([l], _HOOD_CONFIG)
    assert len(result) == 1


_HOOD_CONFIG = {
    "criteria": {
        "neighborhoods": ["Polanco", "Condesa", "Roma Norte"],
        "min_floor": 3,
        "price_min": 30000,
        "price_max": 45000,
        "bedrooms": 2,
        "bathrooms": 0,
        "require_photos": True,
    }
}


def test_filter_rejects_wrong_neighborhood():
    """Miguel Hidalgo is not in the allowed list — must be rejected."""
    l = _listing(neighborhood="Miguel Hidalgo")
    assert hard_filter([l], _HOOD_CONFIG) == []


def test_filter_rejects_cuauhtemoc():
    l = _listing(neighborhood="Cuauhtémoc")
    assert hard_filter([l], _HOOD_CONFIG) == []


def test_filter_passes_allowed_neighborhood():
    l = _listing(neighborhood="Polanco")
    assert len(hard_filter([l], _HOOD_CONFIG)) == 1


def test_filter_rejects_none_neighborhood():
    """Unknown neighborhood (None) rejects when a neighborhood list is configured — precision over recall."""
    l = _listing(neighborhood=None)
    assert len(hard_filter([l], _HOOD_CONFIG)) == 0


def test_filter_neighborhood_case_insensitive():
    """'polanco' lowercase must match 'Polanco' in config."""
    l = _listing(neighborhood="polanco")
    assert len(hard_filter([l], _HOOD_CONFIG)) == 1


def test_filter_neighborhood_trims_whitespace():
    l = _listing(neighborhood="  Polanco  ")
    assert len(hard_filter([l], _HOOD_CONFIG)) == 1


def test_filter_neighborhood_matches_unicode_variant():
    """NFD/decomposed accents should match the NFC config entry seen by users."""
    l = _listing(neighborhood="Cuauhte\u0301moc")
    cfg = {"criteria": {**_HOOD_CONFIG["criteria"], "neighborhoods": ["Cuauhtémoc"]}}
    assert len(hard_filter([l], cfg)) == 1


def test_filter_empty_neighborhoods_allows_all():
    """Empty neighborhoods list = no restriction."""
    cfg = {"criteria": {**_HOOD_CONFIG["criteria"], "neighborhoods": []}}
    l = _listing(neighborhood="Anywhere")
    assert len(hard_filter([l], cfg)) == 1


_GEO_CFG = {
    "criteria": {
        "price_min": 30000, "price_max": 50000, "bedrooms": 2,
        "bathrooms": 0, "min_floor": 0, "min_sqm": 0,
        "require_photos": False, "furnished": None, "neighborhoods": [],
        "geo_filter": {
            "type": "polygon",
            "coords": [
                [19.430, -99.200], [19.440, -99.200],
                [19.440, -99.190], [19.430, -99.190],
                [19.430, -99.200],
            ],
        },
    }
}


def test_geo_filter_rejects_outside():
    inside = _listing(id="g:in")
    inside.latitude = 19.435
    inside.longitude = -99.195
    outside = _listing(id="g:out")
    outside.latitude = 19.410
    outside.longitude = -99.180
    result = hard_filter([inside, outside], _GEO_CFG)
    ids = [l.id for l in result]
    assert "g:in" in ids
    assert "g:out" not in ids


def test_geo_filter_passes_unknown_coords():
    no_coords = _listing(id="g:nc")
    result = hard_filter([no_coords], _GEO_CFG)
    assert len(result) == 1


def test_rank_sorts_by_score_desc():
    scored = [
        {"id": "a", "vision_score": 6.0, "price": 35000, "nice_to_haves": "{}"},
        {
            "id": "b",
            "vision_score": 9.0,
            "price": 32000,
            "nice_to_haves": '{"washer_dryer": true}',
        },
    ]
    result = rank(scored)
    assert result[0]["id"] == "b"
