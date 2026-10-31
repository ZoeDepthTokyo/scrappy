import json
import sqlite3
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Task 1: kanban_stage column migration
# ---------------------------------------------------------------------------

def test_kanban_stage_column_exists(tmp_path):
    """DB must have kanban_stage column after migration."""
    import db
    db_path = tmp_path / "test.db"
    conn = db.connect(str(db_path))
    conn.execute(
        "INSERT INTO pipeline_status (listing_id, status, kanban_stage, updated_at) "
        "VALUES ('test:1', 'active', 'shortlisted', '2026-01-01T00:00:00+00:00')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT kanban_stage FROM pipeline_status WHERE listing_id='test:1'"
    ).fetchone()
    assert row[0] == "shortlisted"
    conn.close()


# ---------------------------------------------------------------------------
# Task 2: /api/stage endpoint
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    import feedback_server
    import kanban as _kanban_mod
    import db as _db

    monkeypatch.setattr(feedback_server, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(_kanban_mod, "DB_PATH", tmp_path / "test.db")

    conn = _db.connect(str(tmp_path / "test.db"))
    conn.execute(
        "INSERT INTO listings (id, source, url, price, photos_json, first_seen, last_seen) "
        "VALUES ('i24:123', 'inmuebles24', 'https://example.com', 35000, '[]', "
        "'2026-01-01', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    feedback_server.app.config["TESTING"] = True
    return feedback_server.app.test_client()


def test_stage_move_persists(client, tmp_path, monkeypatch):
    """POST /api/stage must write kanban_stage to pipeline_status."""
    import feedback_server, kanban as _kanban_mod
    monkeypatch.setattr(feedback_server, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(_kanban_mod, "DB_PATH", tmp_path / "test.db")

    resp = client.post(
        "/api/stage",
        data=json.dumps({"listing_id": "i24:123", "stage": "shortlisted"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    import db as _db
    conn = _db.connect(str(tmp_path / "test.db"))
    row = conn.execute(
        "SELECT kanban_stage FROM pipeline_status WHERE listing_id='i24:123'"
    ).fetchone()
    conn.close()
    assert row is not None and row[0] == "shortlisted"


def test_stage_move_invalid_stage(client):
    """POST /api/stage with unknown stage must return 400."""
    resp = client.post(
        "/api/stage",
        data=json.dumps({"listing_id": "i24:123", "stage": "invented"}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_stage_move_unknown_listing(client):
    """POST /api/stage for non-existent listing must return 404."""
    resp = client.post(
        "/api/stage",
        data=json.dumps({"listing_id": "i24:NOTEXIST", "stage": "inbox"}),
        content_type="application/json",
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Task 3: Kanban HTML
# ---------------------------------------------------------------------------

def test_kanban_page_renders_columns(client):
    """GET /kanban must return HTML with all 4 stage column headers."""
    resp = client.get("/kanban")
    assert resp.status_code == 200
    html = resp.data.decode()
    for label in ["Inbox", "Me interesa", "Contactado", "Visita agendada"]:
        assert label in html, f"Column '{label}' missing from Kanban HTML"


def test_kanban_listing_appears_in_inbox(client):
    """A listing with no feedback/stage must appear in the Inbox column."""
    resp = client.get("/kanban")
    html = resp.data.decode()
    assert "i24:123" in html or "example.com" in html


# ---------------------------------------------------------------------------
# Task 4: favorites header link
# ---------------------------------------------------------------------------

def test_favorites_has_kanban_link(tmp_path):
    """favorites.html must contain a link to /kanban."""
    import db as _db
    from favorites import generate_and_save
    db_path = tmp_path / "test.db"
    conn = _db.connect(str(db_path))
    conn.close()
    html_path = generate_and_save(str(db_path), output_dir=tmp_path)
    html = html_path.read_text(encoding="utf-8")
    assert "/kanban" in html, "Link to /kanban missing from favorites.html"

def test_kanban_shows_price_without_clipping(client):
    response = client.get('/kanban')
    html = response.get_data(as_text=True)
    assert '$35,000 MXN' in html
    assert 'flex-shrink:0' in html
    assert 'https://tile.openstreetmap.org/{z}/{x}/{y}.png' in html
