import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db


def test_schema_creates_tables(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "listings" in tables
    assert "feedback" in tables


def test_upsert_listing(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    db.upsert_listing(
        conn,
        {
            "id": "i24:123",
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
    row = conn.execute("SELECT id FROM listings WHERE id='i24:123'").fetchone()
    assert row is not None


def test_save_feedback(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    db.save_feedback(conn, listing_id="i24:123", signal="up", reason="great light")
    row = conn.execute(
        "SELECT signal FROM feedback WHERE listing_id='i24:123'"
    ).fetchone()
    assert row[0] == "up"


def test_get_unalerted_excludes_no_vision_score(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    db.upsert_listing(
        conn,
        {
            "id": "i24:1",
            "source": "s",
            "url": "u",
            "price": 30000,
            "bedrooms": 2,
            "bathrooms": 2,
            "floor": 4,
            "neighborhood": "Polanco",
            "address_hash": "abc",
            "photos_json": "[]",
            "first_seen": "2026-04-05",
            "last_seen": "2026-04-05",
        },
    )
    assert db.get_unalerted(conn) == []


def test_get_unalerted_returns_unalerted_only(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    db.upsert_listing(
        conn,
        {
            "id": "i24:1",
            "source": "s",
            "url": "u",
            "price": 30000,
            "bedrooms": 2,
            "bathrooms": 2,
            "floor": 4,
            "neighborhood": "Polanco",
            "address_hash": "abc",
            "photos_json": "[]",
            "vision_score": 8.0,
            "first_seen": "2026-04-05",
            "last_seen": "2026-04-05",
            "alerted": 1,
        },
    )
    db.upsert_listing(
        conn,
        {
            "id": "i24:2",
            "source": "s",
            "url": "u2",
            "price": 32000,
            "bedrooms": 2,
            "bathrooms": 2,
            "floor": 5,
            "neighborhood": "Polanco",
            "address_hash": "def",
            "photos_json": "[]",
            "vision_score": 7.0,
            "first_seen": "2026-04-05",
            "last_seen": "2026-04-05",
        },
    )
    result = db.get_unalerted(conn)
    assert len(result) == 1
    assert result[0]["id"] == "i24:2"


def test_mark_alerted_flips_flag(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    db.upsert_listing(
        conn,
        {
            "id": "i24:1",
            "source": "s",
            "url": "u",
            "price": 30000,
            "bedrooms": 2,
            "bathrooms": 2,
            "floor": 4,
            "neighborhood": "Polanco",
            "address_hash": "abc",
            "photos_json": "[]",
            "vision_score": 8.0,
            "first_seen": "2026-04-05",
            "last_seen": "2026-04-05",
        },
    )
    db.mark_alerted(conn, "i24:1")
    row = conn.execute("SELECT alerted FROM listings WHERE id='i24:1'").fetchone()
    assert row[0] == 1


def test_get_feedback_examples_returns_up_down(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    db.save_feedback(conn, "i24:1", "up", "great light")
    db.save_feedback(conn, "i24:2", "up", "nice floor")
    db.save_feedback(conn, "i24:3", "down", "too dark")
    up, down, _, _ = db.get_feedback_examples(conn)
    assert "i24:1" in up
    assert "i24:2" in up
    assert "i24:3" in down


def test_upsert_idempotent_preserves_first_seen(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    db.upsert_listing(
        conn,
        {
            "id": "i24:1",
            "source": "s",
            "url": "u",
            "price": 30000,
            "bedrooms": 2,
            "bathrooms": 2,
            "floor": 4,
            "neighborhood": "Polanco",
            "address_hash": "abc",
            "photos_json": "[]",
            "first_seen": "2026-04-05",
            "last_seen": "2026-04-05",
        },
    )
    # Second upsert with updated last_seen — first_seen must not change
    db.upsert_listing(
        conn,
        {
            "id": "i24:1",
            "source": "s",
            "url": "u",
            "price": 30000,
            "bedrooms": 2,
            "bathrooms": 2,
            "floor": 4,
            "neighborhood": "Polanco",
            "address_hash": "abc",
            "photos_json": "[]",
            "first_seen": "2026-04-06",
            "last_seen": "2026-04-06",
        },
    )
    row = conn.execute(
        "SELECT first_seen, last_seen FROM listings WHERE id='i24:1'"
    ).fetchone()
    assert row[0] == "2026-04-05"  # first_seen preserved
    assert row[1] == "2026-04-06"  # last_seen updated


def test_feedback_has_tags_json_column(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(feedback)").fetchall()]
    assert "tags_json" in cols


def test_feedback_has_parse_model_column(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(feedback)").fetchall()]
    assert "parse_model" in cols


def test_listings_has_dead_checks_column(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(listings)").fetchall()]
    assert "dead_checks" in cols


def test_save_feedback_stores_tags(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    db.save_feedback(
        conn,
        "i24:1",
        "up",
        "great light",
        tags=["great_light"],
        parse_model="claude-haiku-4-5",
    )
    row = conn.execute(
        "SELECT tags_json, parse_model FROM feedback WHERE listing_id='i24:1'"
    ).fetchone()
    import json

    assert json.loads(row[0]) == ["great_light"]
    assert row[1] == "claude-haiku-4-5"


def test_get_feedback_examples_returns_four_values(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    result = db.get_feedback_examples(conn)
    assert len(result) == 4  # pos_ids, neg_ids, pos_tags, neg_tags


def test_get_feedback_examples_includes_tags(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    db.save_feedback(conn, "i24:1", "up", "great", tags=["great_light"])
    db.save_feedback(conn, "i24:2", "down", "dark", tags=["dark"])
    _, _, pos_tags, neg_tags = db.get_feedback_examples(conn)
    assert "great_light" in pos_tags
    assert "dark" in neg_tags


def test_get_shortlist_uses_latest_feedback_state(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    db.upsert_listing(
        conn,
        {
            "id": "i24:1",
            "source": "s",
            "url": "u",
            "price": 30000,
            "bedrooms": 2,
            "bathrooms": 2,
            "floor": 4,
            "sqm": 80,
            "neighborhood": "Polanco",
            "address_hash": "abc",
            "photos_json": "[]",
            "first_seen": "2026-04-05",
            "last_seen": "2026-04-05",
        },
    )
    db.save_feedback(conn, "i24:1", "up", "liked it")
    db.save_feedback(conn, "i24:1", "down", "changed my mind")
    assert db.get_shortlist(conn) == []


def test_schema_creates_pipeline_status_table(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "pipeline_status" in tables


def test_upsert_pipeline_status_creates_and_updates(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    db.upsert_pipeline_status(conn, "i24:1", "starred", note="visit Monday", stars=5)
    row = db.get_pipeline_status(conn, "i24:1")
    assert row is not None
    assert row["status"] == "starred"
    assert row["note"] == "visit Monday"
    assert row["stars"] == 5

    # Update: status changes, note updates
    db.upsert_pipeline_status(conn, "i24:1", "scheduled", note="confirmed", stars=4)
    row = db.get_pipeline_status(conn, "i24:1")
    assert row["status"] == "scheduled"
    assert row["stars"] == 4


def test_get_pipeline_status_returns_none_for_unknown(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    assert db.get_pipeline_status(conn, "nonexistent") is None


def test_get_all_pipeline_statuses(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    db.upsert_pipeline_status(conn, "i24:1", "starred")
    db.upsert_pipeline_status(conn, "i24:2", "visited")
    result = db.get_all_pipeline_statuses(conn)
    assert "i24:1" in result
    assert result["i24:1"]["status"] == "starred"
    assert "i24:2" in result
    assert result["i24:2"]["status"] == "visited"
