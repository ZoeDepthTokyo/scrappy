import db as db_module


def test_alerted_listings_not_returned_by_get_unalerted(tmp_path):
    """Listings already alerted=1 must not reappear in notify output."""
    conn = db_module.connect(str(tmp_path / "test.db"))
    db_module.upsert_listing(
        conn,
        {
            "id": "i24:1",
            "source": "inmuebles24",
            "url": "http://x.com",
            "price": 32000,
            "bedrooms": 2,
            "bathrooms": 2,
            "floor": 5,
            "neighborhood": "Polanco",
            "address_hash": "abc",
            "photos_json": '["http://img.com/1.jpg"]',
            "vision_score": 8.0,
            "first_seen": "2026-04-05",
            "last_seen": "2026-04-05",
            "alerted": 1,
        },
    )
    unalerted = db_module.get_unalerted(conn)
    assert len(unalerted) == 0
