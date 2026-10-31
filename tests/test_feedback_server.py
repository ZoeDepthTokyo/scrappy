import json
import pytest
import paths
import feedback_server


def _seed_listing(db_path, listing_id: str = "i24:1") -> None:
    import db as db_module

    conn = db_module.connect(str(db_path))
    db_module.upsert_listing(
        conn,
        {
            "id": listing_id,
            "source": "inmuebles24",
            "url": f"https://example.com/{listing_id}",
            "price": 35000,
            "bedrooms": 2,
            "bathrooms": 2,
            "floor": 4,
            "neighborhood": "Polanco",
            "address": "Masaryk 10",
            "photos_json": "[]",
            "first_seen": "2026-04-17",
            "last_seen": "2026-04-17",
        },
    )
    conn.close()


@pytest.fixture
def client(tmp_path):
    feedback_server.DB_PATH = str(tmp_path / "test.db")
    feedback_server.CONFIG_PATH = str(tmp_path / "defaults.json")
    # write a minimal config for cadence tests
    with open(feedback_server.CONFIG_PATH, "w") as f:
        json.dump({"cadence_days": 1}, f)
    feedback_server.app.config["TESTING"] = True
    with feedback_server.app.test_client() as c:
        yield c


def test_feedback_endpoint_stores_signal(client):
    _seed_listing(feedback_server.DB_PATH, "i24:1")
    resp = client.post(
        "/feedback",
        data=json.dumps(
            {"listing_id": "i24:1", "signal": "up", "reason": "great light"}
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200


def test_feedback_endpoint_rejects_missing_listing(client):
    resp = client.post(
        "/feedback",
        json={"listing_id": "missing:1", "signal": "up", "reason": "great light"},
    )
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "listing not found"


def test_root_redirects_to_favorites(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/favorites")


def test_favorites_route_serves_generated_shell(client, tmp_path, monkeypatch):
    import db as db_module

    db_path = str(tmp_path / "favorites-route.db")
    monkeypatch.setattr("feedback_server.DB_PATH", db_path)
    conn = db_module.connect(db_path)
    db_module.upsert_listing(
        conn,
        {
            "id": "fav:1",
            "source": "inmuebles24",
            "url": "https://example.com/fav:1",
            "price": 33000,
            "bedrooms": 2,
            "bathrooms": 2,
            "floor": 4,
            "neighborhood": "Polanco",
            "address": "Masaryk 10",
            "photos_json": "[]",
            "first_seen": "2026-04-17",
            "last_seen": "2026-04-17",
        },
    )
    conn.close()

    resp = client.get("/favorites")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "fetch('/listings')" in html
    assert "control-bar" in html


def test_feedback_accepts_tags(client, tmp_path, monkeypatch):
    import db as db_module

    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr("feedback_server.DB_PATH", db_path)
    _seed_listing(db_path, "i24:1")
    resp = client.post(
        "/feedback",
        json={
            "listing_id": "i24:1",
            "signal": "down",
            "reason": "too dark",
            "tags": ["dark", "no_view"],
            "parse_model": "claude-haiku-4-5",
        },
    )
    assert resp.status_code == 200
    test_conn = db_module.connect(db_path)  # open after POST so server write is visible
    row = test_conn.execute(
        "SELECT tags_json, parse_model FROM feedback WHERE listing_id='i24:1'"
    ).fetchone()
    import json

    assert json.loads(row[0]) == ["dark", "no_view"]
    assert row[1] == "claude-haiku-4-5"


def test_cadence_endpoint_updates_config(client, tmp_path):
    resp = client.post(
        "/cadence", data=json.dumps({"days": 3}), content_type="application/json"
    )
    assert resp.status_code == 200
    data = json.loads(paths.user_config_path().read_text(encoding="utf-8"))
    assert data["cadence_days"] == 3


def test_cadence_endpoint_rejects_missing_days(client):
    resp = client.post("/cadence", data=json.dumps({}), content_type="application/json")
    assert resp.status_code == 400


def test_cadence_endpoint_rejects_non_integer_days(client):
    resp = client.post(
        "/cadence",
        data=json.dumps({"days": "soon"}),
        content_type="application/json",
    )
    assert resp.status_code == 400


@pytest.fixture
def criteria_client(tmp_path):
    feedback_server.DB_PATH = str(tmp_path / "test.db")
    feedback_server.CONFIG_PATH = str(tmp_path / "defaults.json")
    base = {
        "cadence_days": 1,
        "criteria": {
            "neighborhoods": ["Hip\u00f3dromo", "Ju\u00e1rez", "Cuauht\u00e9moc"],
            "price_min": 35000,
            "price_max": 45000,
            "bedrooms": 2,
            "bathrooms": 2,
            "furnished": False,
            "min_floor": 3,
            "min_sqm": 60,
            "require_photos": True,
            "min_vision_score": 5,
        },
        "sources": {"inmuebles24": True},
    }
    with open(feedback_server.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(base, f, ensure_ascii=False)
    feedback_server.app.config["TESTING"] = True
    with feedback_server.app.test_client() as c:
        yield c


def test_get_criteria_returns_current_block(criteria_client):
    resp = criteria_client.get("/criteria")
    assert resp.status_code == 200
    data = resp.get_json()
    # GET /criteria returns the flat criteria dict (no "criteria" wrapper)
    assert data["price_min"] == 35000
    assert "Hip\u00f3dromo" in data["neighborhoods"]


def test_get_criteria_prefers_local_override(tmp_path):
    feedback_server.DB_PATH = str(tmp_path / "test.db")
    feedback_server.CONFIG_PATH = str(tmp_path / "defaults.json")
    base = {
        "cadence_days": 1,
        "criteria": {"price_min": 35000, "price_max": 45000},
    }
    local = {
        "criteria": {"price_min": 30000},
    }
    (tmp_path / "defaults.json").write_text(json.dumps(base), encoding="utf-8")
    paths.user_config_path().write_text(json.dumps(local), encoding="utf-8")
    feedback_server.app.config["TESTING"] = True

    with feedback_server.app.test_client() as client:
        resp = client.get("/criteria")

    assert resp.status_code == 200
    assert resp.get_json()["price_min"] == 30000


def test_post_criteria_happy_path(criteria_client):
    resp = criteria_client.post(
        "/criteria",
        json={
            "price_min": 30000,
            "price_max": 50000,
            "bedrooms": 3,
            "bathrooms": 2,
            "furnished": True,
            "min_floor": 1,
            "min_sqm": 70,
            "require_photos": False,
            "min_vision_score": 7,
            "neighborhoods": ["Hip\u00f3dromo", "Roma Norte"],
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["criteria"]["price_min"] == 30000
    assert data["criteria"]["bedrooms"] == 3
    # round-trip UTF-8 from disk
    raw = paths.user_config_path().read_text(encoding="utf-8")
    on_disk = json.loads(raw)
    assert "Hip\u00f3dromo" in on_disk["criteria"]["neighborhoods"]
    # other top-level keys preserved
    assert on_disk["cadence_days"] == 1
    assert on_disk["sources"] == {"inmuebles24": True}


def test_post_criteria_updates_local_override_when_present(tmp_path):
    feedback_server.DB_PATH = str(tmp_path / "test.db")
    feedback_server.CONFIG_PATH = str(tmp_path / "defaults.json")
    base = {
        "cadence_days": 1,
        "criteria": {"price_min": 35000, "price_max": 45000},
    }
    local = {
        "criteria": {"price_min": 30000, "price_max": 45000},
    }
    (tmp_path / "defaults.json").write_text(json.dumps(base), encoding="utf-8")
    local_path = paths.user_config_path()
    local_path.write_text(json.dumps(local), encoding="utf-8")
    feedback_server.app.config["TESTING"] = True

    with feedback_server.app.test_client() as client:
        resp = client.post("/criteria", json={"price_min": 28000})

    assert resp.status_code == 200
    assert json.loads((tmp_path / "defaults.json").read_text(encoding="utf-8"))["criteria"]["price_min"] == 35000
    assert json.loads(local_path.read_text(encoding="utf-8"))["criteria"]["price_min"] == 28000


def test_post_criteria_partial_update(criteria_client):
    resp = criteria_client.post("/criteria", json={"price_max": 48000})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["criteria"]["price_max"] == 48000
    # untouched fields kept
    assert data["criteria"]["price_min"] == 35000
    assert data["criteria"]["bedrooms"] == 2


def test_post_criteria_rejects_unknown_key(criteria_client):
    resp = criteria_client.post("/criteria", json={"foo": "bar"})
    assert resp.status_code == 400
    assert "unknown key" in resp.get_json()["error"]
    # config not mutated
    on_disk = json.loads(open(feedback_server.CONFIG_PATH, encoding="utf-8").read())
    assert "foo" not in on_disk["criteria"]


def test_post_criteria_rejects_invalid_type(criteria_client):
    resp = criteria_client.post("/criteria", json={"price_min": "cheap"})
    assert resp.status_code == 400
    # also: price_min > price_max
    resp2 = criteria_client.post(
        "/criteria", json={"price_min": 99000, "price_max": 1000}
    )
    assert resp2.status_code == 400
    # min_vision_score out of range
    resp3 = criteria_client.post("/criteria", json={"min_vision_score": 99})
    assert resp3.status_code == 400
    # boolean expected
    resp4 = criteria_client.post("/criteria", json={"furnished": "yes"})
    assert resp4.status_code == 400
    # neighborhoods list[str]
    resp5 = criteria_client.post("/criteria", json={"neighborhoods": [1, 2]})
    assert resp5.status_code == 400
    # config not mutated
    on_disk = json.loads(open(feedback_server.CONFIG_PATH, encoding="utf-8").read())
    assert on_disk["criteria"]["price_min"] == 35000
    assert on_disk["criteria"]["price_max"] == 45000


def test_feedback_endpoint_returns_500_on_save_failure(client, monkeypatch):
    _seed_listing(feedback_server.DB_PATH, "i24:1")
    monkeypatch.setattr("feedback_server.db.save_feedback", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db down")))
    resp = client.post(
        "/feedback",
        json={"listing_id": "i24:1", "signal": "up", "reason": "great light"},
    )
    assert resp.status_code == 500
    assert resp.get_json()["ok"] is False


# ── Wave 2: new endpoints ─────────────────────────────────────────────────────

_LISTING_ROW = {
    "id": "i24:1", "source": "inmuebles24", "url": "https://example.com",
    "price": 35000, "bedrooms": 2, "bathrooms": 2, "floor": 5,
    "neighborhood": "Polanco", "address_hash": "abc", "photos_json": "[]",
    "first_seen": "2026-04-15", "last_seen": "2026-04-15",
}


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """Client with real tmp DB + writable DATA_DIR."""
    import feedback_server as fs
    monkeypatch.setattr(fs, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(fs, "CONFIG_PATH", tmp_path / "defaults.json")
    monkeypatch.setattr(fs, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(fs, "_RUN_PROCESS", None)
    monkeypatch.setattr(fs, "_RUN_LOG_HANDLE", None)
    (tmp_path / "data").mkdir()
    with open(tmp_path / "defaults.json", "w") as f:
        json.dump({"cadence_days": 1, "criteria": {}}, f)
    fs.app.config["TESTING"] = True
    with fs.app.test_client() as c:
        yield c, tmp_path


def test_listings_empty_db(api_client):
    c, _ = api_client
    resp = c.get("/listings")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["counts"]["total"] == 0
    assert data["approved"] == []
    assert data["inbox"] == []


def test_listings_groups_by_feedback(api_client):
    import db as db_module, feedback_server as fs
    c, tmp_path = api_client
    conn = db_module.connect(str(tmp_path / "test.db"))
    # Distinct address_hashes — these represent three different properties.
    # /listings collapses duplicates by address_hash, so shared-hash rows
    # would (correctly) merge into one. See test_listings_collapses_duplicates.
    db_module.upsert_listing(conn, {**_LISTING_ROW, "id": "i24:1", "address_hash": "h1"})
    db_module.upsert_listing(conn, {**_LISTING_ROW, "id": "i24:2", "address_hash": "h2"})
    db_module.upsert_listing(conn, {**_LISTING_ROW, "id": "i24:3", "address_hash": "h3"})
    db_module.save_feedback(conn, "i24:1", "up")
    db_module.save_feedback(conn, "i24:2", "down")
    conn.close()

    resp = c.get("/listings")
    data = resp.get_json()
    assert data["counts"]["approved"] == 1
    assert data["counts"]["rejected"] == 1
    assert data["counts"]["inbox"] == 1
    assert data["approved"][0]["id"] == "i24:1"


def test_listings_collapses_duplicates_across_tabs(api_client):
    """Tab-coherence guard: rows sharing an address_hash must land in exactly
    one tab — the row that has the most recent feedback wins.

    Production bug (2026-04-16): cross-run duplicate rows (different scraper
    ids, same address_hash) appeared in multiple tabs simultaneously — user
    saw the same property as both 'approved' and 'inbox'.
    """
    import db as db_module
    c, tmp_path = api_client
    conn = db_module.connect(str(tmp_path / "test.db"))
    # Two rows, SAME address_hash — represent the same property under
    # different scraper-assigned ids (the real production scenario).
    db_module.upsert_listing(conn, {**_LISTING_ROW, "id": "ml:aaa", "address_hash": "dup"})
    db_module.upsert_listing(conn, {**_LISTING_ROW, "id": "ml:bbb", "address_hash": "dup"})
    # Only one row has feedback — the other must NOT also appear in inbox.
    db_module.save_feedback(conn, "ml:aaa", "up")
    conn.close()

    resp = c.get("/listings")
    data = resp.get_json()
    assert data["counts"]["total"] == 1, "Duplicates must collapse to one row"
    assert data["counts"]["approved"] == 1
    assert data["counts"]["inbox"] == 0, "The unrated duplicate must not appear in inbox"
    assert data["approved"][0]["id"] == "ml:aaa"


def test_listings_merges_pipeline_status(api_client):
    import db as db_module
    c, tmp_path = api_client
    conn = db_module.connect(str(tmp_path / "test.db"))
    db_module.upsert_listing(conn, _LISTING_ROW)
    db_module.upsert_pipeline_status(conn, "i24:1", "starred", note="visit soon", stars=5)
    conn.close()

    resp = c.get("/listings")
    data = resp.get_json()
    row = data["inbox"][0]
    assert row["pipeline_status"] == "starred"
    assert row["pipeline_note"] == "visit soon"
    assert row["pipeline_stars"] == 5


def test_run_meta_missing_files(api_client):
    c, _ = api_client
    resp = c.get("/run_meta")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["log_tail"] == []
    assert data["sources"] == {}


def test_run_meta_reads_files(api_client):
    """The /run_meta endpoint returns aggregated per-source health, not raw rows.

    Integrated shape (post external-agent refactor, 2026-04-16):
      sources[<name>] = { source, checks, success, success_rate, avg_bytes,
                          errors, last_ts, last_transport }
    """
    c, tmp_path = api_client
    data_dir = tmp_path / "data"
    (data_dir / "last_run.log").write_text("line1\nline2\nline3", encoding="utf-8")
    health = [
        {"source": "trovit", "status": 200, "bytes": 939878, "error": "", "ts": 1.0, "transport": "httpx"},
        {"source": "trovit", "status": 200, "bytes": 939569, "error": "", "ts": 2.0, "transport": "httpx"},
    ]
    (data_dir / "scraper_health.json").write_text(json.dumps(health), encoding="utf-8")

    resp = c.get("/run_meta")
    data = resp.get_json()
    assert "line3" in data["log_tail"]
    assert "trovit" in data["sources"]
    trovit = data["sources"]["trovit"]
    assert trovit["checks"] == 2
    assert trovit["success"] == 2
    assert trovit["success_rate"] == "100.0%"
    assert trovit["last_transport"] == "httpx"


def test_run_endpoint_rejects_when_process_active(api_client, monkeypatch):
    import feedback_server as fs

    c, _ = api_client

    class _BusyProc:
        def poll(self):
            return None

    monkeypatch.setattr(fs, "_RUN_PROCESS", _BusyProc())
    resp = c.post("/run")
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "run already in progress"


def test_taste_empty_db(api_client):
    c, _ = api_client
    resp = c.get("/taste")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["has_signal"] is False
    assert data["weights"] == {}


def test_pipeline_status_post_happy_path(api_client):
    import db as db_module
    c, tmp_path = api_client
    conn = db_module.connect(str(tmp_path / "test.db"))
    db_module.upsert_listing(conn, _LISTING_ROW)
    conn.close()

    resp = c.post("/pipeline_status", json={"listing_id": "i24:1", "status": "scheduled", "stars": 3})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_pipeline_status_post_rejects_missing_listing(api_client):
    c, _ = api_client
    resp = c.post("/pipeline_status", json={"listing_id": "missing:1", "status": "scheduled"})
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "listing not found"


def test_pipeline_status_post_missing_fields(api_client):
    c, _ = api_client
    resp = c.post("/pipeline_status", json={"listing_id": "i24:1"})
    assert resp.status_code == 400
    resp2 = c.post("/pipeline_status", json={"status": "starred"})
    assert resp2.status_code == 400


def test_pipeline_status_post_invalid_stars(api_client):
    c, _ = api_client
    resp = c.post("/pipeline_status", json={"listing_id": "i24:1", "status": "starred", "stars": 99})
    assert resp.status_code == 400


def test_pipeline_status_partial_update_preserves_existing_fields(api_client):
    import db as db_module
    c, tmp_path = api_client
    db_path = str(tmp_path / "test.db")
    conn = db_module.connect(db_path)
    db_module.upsert_listing(conn, _LISTING_ROW)
    db_module.upsert_pipeline_status(conn, "i24:1", status="scheduled", stars=4, note="keep me")
    conn.close()

    resp = c.post("/pipeline_status", json={"listing_id": "i24:1", "order_rank": 2})
    assert resp.status_code == 200
    resp2 = c.post("/pipeline_status", json={"listing_id": "i24:1", "stars": 3})
    assert resp2.status_code == 200

    conn = db_module.connect(db_path)
    row = conn.execute(
        "SELECT status, note, stars, order_rank FROM pipeline_status WHERE listing_id='i24:1'"
    ).fetchone()
    conn.close()
    assert row == ("scheduled", "keep me", 3, 2)


# ── Wave 4: wrong-city auto-tag ───────────────────────────────────────────────

@pytest.mark.parametrize("reason", [
    "wrong city — shows Polanco but it's Guadalajara",
    "Wrong City",
    "this is a wrong location",
    "location mismatch",
    "LOCATION MISMATCH, reject",
])
def test_wrong_city_reason_auto_injects_tag(client, reason):
    """Feedback with wrong-city keywords must auto-add wrong_location tag."""
    import db as db_module
    _seed_listing(feedback_server.DB_PATH, "i24:tag")
    resp = client.post(
        "/feedback",
        json={"listing_id": "i24:tag", "signal": "down", "reason": reason},
    )
    assert resp.status_code == 200
    conn = db_module.connect(feedback_server.DB_PATH)
    row = conn.execute(
        "SELECT tags_json FROM feedback WHERE listing_id='i24:tag'"
    ).fetchone()
    conn.close()
    tags = json.loads(row[0]) if row and row[0] else []
    assert "wrong_location" in tags, f"Expected wrong_location in tags for reason: {reason!r}"


def test_normal_reason_does_not_inject_wrong_location(client):
    """Feedback without wrong-city keywords must not auto-inject wrong_location."""
    import db as db_module
    _seed_listing(feedback_server.DB_PATH, "i24:normal")
    resp = client.post(
        "/feedback",
        json={"listing_id": "i24:normal", "signal": "down", "reason": "too dark, no view"},
    )
    assert resp.status_code == 200
    conn = db_module.connect(feedback_server.DB_PATH)
    row = conn.execute(
        "SELECT tags_json FROM feedback WHERE listing_id='i24:normal'"
    ).fetchone()
    conn.close()
    tags = json.loads(row[0]) if row and row[0] else []
    assert "wrong_location" not in tags


def test_listings_includes_alerted_field(client, tmp_path, monkeypatch):
    """GET /listings must return alerted field on every listing."""
    import db as db_module
    db_path = str(tmp_path / "alerted.db")
    monkeypatch.setattr("feedback_server.DB_PATH", db_path)
    conn = db_module.connect(db_path)
    conn.execute(
        "INSERT INTO listings "
        "(id,source,url,price,bedrooms,bathrooms,neighborhood,address,"
        "first_seen,last_seen,alerted) VALUES "
        "('al:1','s','http://x.com/a',35000,2,2,'Polanco','Masaryk 2',"
        "'2026-04-16','2026-04-16',1)"
    )
    conn.commit()
    conn.close()
    data = client.get("/listings").get_json()
    all_rows = data["inbox"] + data["approved"] + data["rejected"]
    assert len(all_rows) > 0
    for row in all_rows:
        assert "alerted" in row
    row = next(r for r in all_rows if r["id"] == "al:1")
    assert row["alerted"] == 1


def test_reset_signal_accepted(client):
    """POST /feedback with signal='reset' must return 200."""
    _seed_listing(feedback_server.DB_PATH, "flip:test")
    resp = client.post(
        "/feedback",
        json={"listing_id": "flip:test", "signal": "reset", "reason": ""},
    )
    assert resp.status_code == 200, resp.get_json()


def test_reset_routes_to_inbox(client, tmp_path, monkeypatch):
    """After up then reset, /listings must place the listing in inbox."""
    import db as db_module
    db_path = str(tmp_path / "flip.db")
    monkeypatch.setattr("feedback_server.DB_PATH", db_path)
    conn = db_module.connect(db_path)
    conn.execute(
        "INSERT INTO listings "
        "(id, source, url, price, bedrooms, bathrooms, neighborhood, address, "
        "first_seen, last_seen) VALUES "
        "('flip:1','s','http://x.com/1',35000,2,2,'Polanco','Masaryk 1',"
        "'2026-04-16','2026-04-16')"
    )
    conn.commit()
    conn.close()
    client.post("/feedback", json={"listing_id": "flip:1", "signal": "up"})
    client.post("/feedback", json={"listing_id": "flip:1", "signal": "reset"})
    data = client.get("/listings").get_json()
    assert any(l["id"] == "flip:1" for l in data["inbox"]), \
        "reset must route listing to inbox"
    assert all(l["id"] != "flip:1" for l in data["approved"]), \
        "must not remain in approved after reset"


def test_wrong_city_does_not_duplicate_existing_tag(client):
    """If caller already passed wrong_location, it must not appear twice."""
    import db as db_module
    _seed_listing(feedback_server.DB_PATH, "i24:dup")
    resp = client.post(
        "/feedback",
        json={
            "listing_id": "i24:dup",
            "signal": "down",
            "reason": "wrong city",
            "tags": ["wrong_location", "bad_vibes"],
        },
    )
    assert resp.status_code == 200
    conn = db_module.connect(feedback_server.DB_PATH)
    row = conn.execute(
        "SELECT tags_json FROM feedback WHERE listing_id='i24:dup'"
    ).fetchone()
    conn.close()
    tags = json.loads(row[0]) if row and row[0] else []
    assert tags.count("wrong_location") == 1


def test_run_meta_returns_shape(client, tmp_path, monkeypatch):
    monkeypatch.setattr("feedback_server.DATA_DIR", tmp_path)
    resp = client.get("/run_meta")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "log_tail" in data and isinstance(data["log_tail"], list)
    assert "sources" in data and isinstance(data["sources"], dict)


def test_pipeline_status_stores_order_rank(client, tmp_path, monkeypatch):
    import db as db_module
    db_path = str(tmp_path / "order.db")
    monkeypatch.setattr("feedback_server.DB_PATH", db_path)
    _seed_listing(db_path, "ord:1")
    resp = client.post(
        "/pipeline_status",
        json={"listing_id": "ord:1", "status": "liked",
              "stars": 3, "order_rank": 2},
    )
    assert resp.status_code == 200
    conn = db_module.connect(db_path)
    row = conn.execute(
        "SELECT order_rank FROM pipeline_status WHERE listing_id='ord:1'"
    ).fetchone()
    assert row is not None and row[0] == 2


def test_new_listing_not_masked_by_old_rejected(client, tmp_path, monkeypatch):
    """New listing with same address_hash as an old rejected one must appear in Inbox."""
    import db as db_module
    db_path = str(tmp_path / "mask.db")
    monkeypatch.setattr("feedback_server.DB_PATH", db_path)
    conn = db_module.connect(db_path)
    conn.execute(
        "INSERT INTO listings "
        "(id,source,url,price,bedrooms,bathrooms,neighborhood,address,"
        "address_hash,first_seen,last_seen) VALUES "
        "('old:1','s','http://x.com/old',35000,2,2,'Polanco','Masaryk 1',"
        "'aabbcc','2026-04-01','2026-04-01')"
    )
    conn.execute(
        "INSERT INTO feedback (listing_id,signal,reason,created_at) VALUES "
        "('old:1','down','too dark','2026-04-01T10:00:00')"
    )
    conn.execute(
        "INSERT INTO listings "
        "(id,source,url,price,bedrooms,bathrooms,neighborhood,address,"
        "address_hash,first_seen,last_seen) VALUES "
        "('new:1','s','http://x.com/new',35000,2,2,'Polanco','Masaryk 1',"
        "'aabbcc','2026-04-16','2026-04-16')"
    )
    conn.commit()
    conn.close()
    data = client.get("/listings").get_json()
    inbox_ids = [l["id"] for l in data["inbox"]]
    assert "new:1" in inbox_ids


def test_note_endpoint_persists_body(client, tmp_path, monkeypatch):
    import db as db_module
    db_path = str(tmp_path / "notes.db")
    monkeypatch.setattr("feedback_server.DB_PATH", db_path)
    _seed_listing(db_path, "n:1")
    resp = client.post("/note", json={"listing_id": "n:1",
                                      "body": "great light, high floor"})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    conn = db_module.connect(db_path)
    row = conn.execute(
        "SELECT body FROM notes WHERE listing_id='n:1'"
    ).fetchone()
    assert row is not None and row[0] == "great light, high floor"


def test_note_endpoint_does_not_create_feedback(client, tmp_path, monkeypatch):
    import db as db_module

    db_path = str(tmp_path / "note_feedback.db")
    monkeypatch.setattr("feedback_server.DB_PATH", db_path)
    _seed_listing(db_path, "n:2")

    resp = client.post("/note", json={"listing_id": "n:2", "body": "great light, high floor"})

    assert resp.status_code == 200
    conn = db_module.connect(db_path)
    row = conn.execute(
        "SELECT COUNT(*) FROM feedback WHERE listing_id='n:2'"
    ).fetchone()
    conn.close()
    assert row[0] == 0


def test_note_endpoint_rejects_missing_listing(client):
    resp = client.post("/note", json={"listing_id": "missing:1", "body": "hello"})
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "listing not found"
