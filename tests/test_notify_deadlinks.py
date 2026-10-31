import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from unittest.mock import patch, MagicMock
import db
from notify import validate_urls

LISTING = {
    "id": "i24:abc",
    "url": "https://www.inmuebles24.com/listing/123",
    "price": 32000,
    "neighborhood": "Polanco",
}


def test_valid_url_passes_through(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("httpx.head", return_value=mock_resp):
        result = validate_urls([LISTING], conn=conn)
    assert len(result) == 1
    assert result[0]["id"] == "i24:abc"


def test_404_drops_listing(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.upsert_listing(
        conn,
        {
            "id": "i24:abc",
            "source": "inmuebles24",
            "url": LISTING["url"],
            "price": 32000,
            "bedrooms": 2,
            "bathrooms": 2,
            "floor": 5,
            "neighborhood": "Polanco",
            "address_hash": "abc",
            "photos_json": "[]",
            "first_seen": "2026-04-09",
            "last_seen": "2026-04-09",
        },
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    with patch("httpx.head", return_value=mock_resp):
        result = validate_urls([LISTING], conn=conn)
    assert len(result) == 0


def test_404_increments_dead_checks(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.upsert_listing(
        conn,
        {
            "id": "i24:abc",
            "source": "inmuebles24",
            "url": LISTING["url"],
            "price": 32000,
            "bedrooms": 2,
            "bathrooms": 2,
            "floor": 5,
            "neighborhood": "Polanco",
            "address_hash": "abc",
            "photos_json": "[]",
            "first_seen": "2026-04-09",
            "last_seen": "2026-04-09",
        },
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    with patch("httpx.head", return_value=mock_resp):
        validate_urls([LISTING], conn=conn)
    row = conn.execute("SELECT dead_checks FROM listings WHERE id='i24:abc'").fetchone()
    assert row[0] == 1


def test_three_dead_checks_skips_network(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.upsert_listing(
        conn,
        {
            "id": "i24:abc",
            "source": "inmuebles24",
            "url": LISTING["url"],
            "price": 32000,
            "bedrooms": 2,
            "bathrooms": 2,
            "floor": 5,
            "neighborhood": "Polanco",
            "address_hash": "abc",
            "photos_json": "[]",
            "dead_checks": 3,
            "first_seen": "2026-04-09",
            "last_seen": "2026-04-09",
        },
    )
    with patch("httpx.head") as mock_head:
        result = validate_urls([LISTING], conn=conn)
    mock_head.assert_not_called()
    assert len(result) == 0


def test_validate_urls_403_head_200_get_is_alive(tmp_path):
    """403 HEAD + 200 GET (with browser UA) = page alive."""
    conn = db.connect(str(tmp_path / "t.db"))
    head_resp = MagicMock(); head_resp.status_code = 403
    get_resp = MagicMock(); get_resp.status_code = 200
    with patch("httpx.head", return_value=head_resp), \
         patch("httpx.get", return_value=get_resp):
        result = validate_urls([LISTING], conn=conn)
    assert len(result) == 1


def test_validate_urls_403_head_403_get_is_dead(tmp_path):
    """403 HEAD + 403 GET (with browser UA) = page dead."""
    conn = db.connect(str(tmp_path / "t.db"))
    db.upsert_listing(
        conn,
        {
            "id": "i24:abc",
            "source": "inmuebles24",
            "url": LISTING["url"],
            "price": 32000,
            "bedrooms": 2,
            "bathrooms": 2,
            "floor": 5,
            "neighborhood": "Polanco",
            "address_hash": "abc",
            "photos_json": "[]",
            "first_seen": "2026-04-09",
            "last_seen": "2026-04-09",
        },
    )
    head_resp = MagicMock(); head_resp.status_code = 403
    get_resp = MagicMock(); get_resp.status_code = 403
    with patch("httpx.head", return_value=head_resp), \
         patch("httpx.get", return_value=get_resp):
        result = validate_urls([LISTING], conn=conn)
    assert len(result) == 0


def test_validate_urls_405_head_200_get_is_alive(tmp_path):
    """405 HEAD + 200 GET (with browser UA) = page alive."""
    conn = db.connect(str(tmp_path / "t.db"))
    head_resp = MagicMock(); head_resp.status_code = 405
    get_resp = MagicMock(); get_resp.status_code = 200
    with patch("httpx.head", return_value=head_resp), \
         patch("httpx.get", return_value=get_resp):
        result = validate_urls([LISTING], conn=conn)
    assert len(result) == 1


def test_connection_error_drops_listing(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.upsert_listing(
        conn,
        {
            "id": "i24:abc",
            "source": "inmuebles24",
            "url": LISTING["url"],
            "price": 32000,
            "bedrooms": 2,
            "bathrooms": 2,
            "floor": 5,
            "neighborhood": "Polanco",
            "address_hash": "abc",
            "photos_json": "[]",
            "first_seen": "2026-04-09",
            "last_seen": "2026-04-09",
        },
    )
    with patch("httpx.head", side_effect=Exception("connection refused")):
        result = validate_urls([LISTING], conn=conn)
    assert len(result) == 0
