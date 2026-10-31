import json
import sqlite3

import db
import favorites


def _seed_listing(conn, listing_id: str = "i24:1") -> None:
    db.upsert_listing(
        conn,
        {
            "id": listing_id,
            "source": "inmuebles24",
            "url": "http://x.com",
            "price": 32000,
            "bedrooms": 2,
            "bathrooms": 2,
            "floor": 5,
            "neighborhood": "Polanco",
            "address_hash": "abc",
            "photos_json": "[]",
            "first_seen": "2026-04-05",
            "last_seen": "2026-04-05",
        },
    )


def test_fetch_rated_uses_latest_feedback_state(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    conn.row_factory = sqlite3.Row
    _seed_listing(conn)
    db.save_feedback(conn, "i24:1", "up", "great light")
    db.save_feedback(conn, "i24:1", "down", "too dark")

    approved = favorites._fetch_rated(conn, "up")
    rejected = favorites._fetch_rated(conn, "down")

    assert approved == []
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "too dark"


def test_inbox_tab_lists_unrated(tmp_path):
    p = tmp_path / "test.db"
    conn = db.connect(str(p))
    conn.row_factory = sqlite3.Row
    _seed_listing(conn, listing_id="t:1")
    conn.close()
    out = favorites.generate_and_save(p, output_dir=tmp_path)
    html = out.read_text(encoding="utf-8")
    assert "panel-inbox" in html
    assert "cnt-inbox" in html
    assert "control-bar" in html


def test_generate_and_save_writes_html_and_json_snapshot(tmp_path):
    p = tmp_path / "test.db"
    conn = db.connect(str(p))
    conn.row_factory = sqlite3.Row
    _seed_listing(conn, listing_id="t:artifact")
    conn.close()

    out = favorites.generate_and_save(p, output_dir=tmp_path)
    snapshot_path = tmp_path / "listings_data.json"

    assert out == tmp_path / "favorites.html"
    assert snapshot_path.exists()

    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert payload["generated"]
    assert len(payload["inbox"]) == 1
    assert payload["inbox"][0]["id"] == "t:artifact"


def test_criteria_panel_present_in_html(tmp_path):
    p = tmp_path / "test.db"
    conn = db.connect(str(p))
    conn.row_factory = sqlite3.Row
    _seed_listing(conn, listing_id="t:cp")
    conn.close()
    html = favorites.generate_and_save(p, output_dir=tmp_path).read_text(encoding="utf-8")
    # Panel + endpoints + key inputs
    assert "criteria-panel" in html
    assert "/criteria" in html
    assert "loadCriteria" in html
    assert "onCriteriaChange" in html
    assert "applyAndRunCriteria" in html
    assert "cp-neighborhoods" in html
    assert "cp-price-min" in html
    assert "cp-price-max" in html
    assert "cp-furnished" in html
    assert "cp-min-vision" in html


def test_inbox_thumbs_present_on_inbox_cards(tmp_path):
    p = tmp_path / "test.db"
    conn = db.connect(str(p))
    conn.row_factory = sqlite3.Row
    _seed_listing(conn, listing_id="t:thumb")
    conn.close()
    html = favorites.generate_and_save(p, output_dir=tmp_path).read_text(encoding="utf-8")
    assert "sendInboxFeedback" in html
    assert "inbox-thumb" in html
    assert "inbox-actions" in html
    assert "moveInboxCard" in html


def test_distinct_criteria_vs_display_filters(tmp_path):
    """Criteria panel must be visually labelled as affecting next scrape."""
    p = tmp_path / "test.db"
    conn = db.connect(str(p))
    conn.row_factory = sqlite3.Row
    _seed_listing(conn, listing_id="t:dist")
    conn.close()
    html = favorites.generate_and_save(p, output_dir=tmp_path).read_text(encoding="utf-8")
    assert "affects next scrape" in html
    # Display filters (existing) still present
    assert "min-score" in html
    assert "max-price" in html


# ── Wave 3 tests ──────────────────────────────────────────────────────────────

def _html(tmp_path):
    """Helper: generate favorites HTML into tmp_path and return its text.

    output_dir=tmp_path avoids concurrent-write collisions on the shared
    REPORTS_DIR/favorites.html when multiple pytest processes run in parallel.
    """
    p = tmp_path / "test.db"
    conn = db.connect(str(p))
    conn.row_factory = sqlite3.Row
    _seed_listing(conn, listing_id="t:w3")
    conn.close()
    return favorites.generate_and_save(p, output_dir=tmp_path).read_text(encoding="utf-8")


def test_polling_replaces_embedded_json(tmp_path):
    """HTML must poll /listings — no embedded JSON data script tag."""
    html = _html(tmp_path)
    assert "fetch('/listings')" in html
    assert "adaptApiResponse" in html
    # No embedded JSON script tag
    assert 'id="listings-data"' not in html
    assert 'type="application/json"' not in html


def test_taste_tab_present(tmp_path):
    """Taste tab, panel, and loadTaste function must exist."""
    html = _html(tmp_path)
    assert "panel-taste" in html
    assert "tab-taste" in html
    assert "loadTaste" in html
    assert "taste-content" in html


def test_leaflet_draw_present(tmp_path):
    """Leaflet.draw CDN and draw event handlers must be in HTML."""
    html = _html(tmp_path)
    assert "leaflet-draw" in html
    assert "L.Draw.Event.CREATED" in html
    assert "L.Control.Draw" in html


def test_pipeline_posts_to_server(tmp_path):
    """Pipeline toggle must POST to /pipeline_status."""
    html = _html(tmp_path)
    assert "/pipeline_status" in html
    assert "togglePL" in html


def test_stars_post_to_server(tmp_path):
    """Stars setter must POST stars to /pipeline_status."""
    html = _html(tmp_path)
    # setStars calls fetch('/pipeline_status')
    assert "setStars" in html
    assert "stars: n" in html
    assert "status: 'rated'" not in html


def test_criteria_null_filter(tmp_path):
    """applyAndRunCriteria must filter null values before posting."""
    html = _html(tmp_path)
    assert "applyAndRunCriteria" in html
    # The payload filtering pattern must be present
    assert "current[k] !== null" in html


def test_draw_area_button_present(tmp_path):
    """Draw Area + Clear Area buttons must exist in control bar."""
    html = _html(tmp_path)
    assert "draw-area-btn" in html
    assert "clearGeoFilter" in html


def test_persistent_state_is_not_browser_storage_backed(tmp_path):
    html = _html(tmp_path)
    assert "localStorage" not in html
    assert "sessionStorage" not in html


def test_drag_order_posts_only_rank(tmp_path):
    html = _html(tmp_path)
    assert "order_rank: idx" in html
    assert "stars: 0, order_rank: idx" not in html



def test_neighborhood_coords_injected(tmp_path):
    """Python coord table must be injected into JS (spot-check Polanco)."""
    html = _html(tmp_path)
    assert "Polanco" in html
    assert "19.431" in html  # lat


def _inbox_row(**kwargs):
    base = {
        "id": "vs:1", "source": "s", "url": "http://x.com", "price": 36000,
        "bedrooms": 2, "bathrooms": 2, "floor": 4, "sqm": 72.0,
        "neighborhood": "Polanco", "address": "Horacio 10",
        "address_hash": "abc", "title": "", "latitude": None, "longitude": None,
        "photos_json": "[]", "vision_score": None, "vision_flags": "[]",
        "nice_to_haves": "{}", "first_seen": "2026-04-16",
        "last_seen": "2026-04-16", "alerted": 0, "dead_checks": 0,
    }
    return {**base, **kwargs}


def test_value_per_sqm_computed():
    from favorites import _build_data_dict
    data = _build_data_dict([], [], [_inbox_row(price=36000, sqm=72.0)])
    assert data["inbox"][0]["value_per_sqm"] == 500


def test_value_per_sqm_none_when_sqm_unknown():
    from favorites import _build_data_dict
    data = _build_data_dict([], [], [_inbox_row(id="vs:2", sqm=None,
                                                address_hash="def")])
    assert data["inbox"][0]["value_per_sqm"] is None


def test_neighborhood_in_data_dict():
    """Neighborhood must propagate through _build_data_dict (filter-pill proxy)."""
    from favorites import _build_data_dict
    row = {
        "id": "nb:1", "source": "s", "url": "http://x.com", "price": 35000,
        "bedrooms": 2, "bathrooms": 2, "floor": 4, "sqm": 70.0,
        "neighborhood": "Condesa", "address": "Amsterdam 10",
        "address_hash": "abc", "title": "", "latitude": None, "longitude": None,
        "photos_json": "[]", "vision_score": None, "vision_flags": "[]",
        "nice_to_haves": "{}", "first_seen": "2026-04-16",
        "last_seen": "2026-04-16", "alerted": 0, "dead_checks": 0,
    }
    data = _build_data_dict([], [], [row])
    assert data["inbox"][0]["neighborhood"] == "Condesa"


def test_alerted_field_in_data_dict(tmp_path):
    """alerted=1 listing must carry alerted=1 through _build_data_dict."""
    import sqlite3
    from favorites import _build_data_dict, _fetch_inbox
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE listings (
            id TEXT PRIMARY KEY, source TEXT, url TEXT, price INTEGER,
            bedrooms INTEGER, bathrooms INTEGER, floor INTEGER, sqm REAL,
            neighborhood TEXT, address TEXT, address_hash TEXT, title TEXT,
            latitude REAL, longitude REAL, photos_json TEXT, vision_score REAL,
            vision_flags TEXT, nice_to_haves TEXT, first_seen TEXT, last_seen TEXT,
            alerted INTEGER DEFAULT 0, dead_checks INTEGER DEFAULT 0
        );
        CREATE TABLE feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT, listing_id TEXT, signal TEXT,
            reason TEXT, tags_json TEXT, parse_model TEXT, created_at TEXT
        );
        INSERT INTO listings
            (id,source,url,price,bedrooms,bathrooms,neighborhood,address,
             first_seen,last_seen,alerted)
        VALUES ('al:1','s','http://x.com',35000,2,2,'Polanco','Masaryk 1',
                '2026-04-16','2026-04-16',1);
    """)
    inbox = _fetch_inbox(conn)
    data = _build_data_dict([], [], inbox)
    assert data["inbox"][0]["alerted"] == 1
