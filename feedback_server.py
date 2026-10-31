import json
import os
import subprocess
import sys
from pathlib import Path
from flask import Flask, request, jsonify, send_file, redirect
from config_loader import load_file_config, resolve_local_config_path
import db
import paths
from db import VALID_SIGNALS
from favorites import generate_and_save as _regen_favorites, generate_and_save

app = Flask(__name__)
APP_DIR = Path(__file__).resolve().parent
DB_PATH = paths.db_path()

import kanban as _kanban_mod  # noqa: E402
_kanban_mod.register(app, DB_PATH)
CONFIG_PATH = paths.bundled_path("config.json")
DATA_DIR = paths.data_dir()
_RUN_PROCESS: subprocess.Popen | None = None
_RUN_LOG_HANDLE = None
_SERVER = None


def _config_write_path() -> Path:
    config_path = Path(CONFIG_PATH)
    local_path = resolve_local_config_path(config_path)
    return local_path


def _load_runtime_config() -> dict:
    return load_file_config(CONFIG_PATH)


def _write_runtime_config(config: dict) -> Path:
    target = _config_write_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target


def _listing_exists(conn, listing_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM listings WHERE id=? LIMIT 1",
        (listing_id,),
    ).fetchone()
    return row is not None


def _run_lock_path() -> Path:
    return Path(DATA_DIR) / "run.lock"


def _run_log_path() -> Path:
    return Path(DATA_DIR) / "last_run.log"


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def _active_run_lock_pid() -> int | None:
    lock_path = _run_lock_path()
    if not lock_path.exists():
        return None
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        pid = int(payload.get("pid") or 0)
    except Exception:
        return None
    return pid if _pid_is_running(pid) else None


def _reap_run_process() -> None:
    global _RUN_PROCESS, _RUN_LOG_HANDLE
    if _RUN_PROCESS is not None and _RUN_PROCESS.poll() is not None:
        if _RUN_LOG_HANDLE is not None and not _RUN_LOG_HANDLE.closed:
            _RUN_LOG_HANDLE.close()
        _RUN_PROCESS = None
        _RUN_LOG_HANDLE = None


def _run_in_progress() -> bool:
    _reap_run_process()
    return _RUN_PROCESS is not None or _active_run_lock_pid() is not None


@app.before_request
def protect_local_requests():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"} and request.path != "/api/export":
        return None
    port = request.environ.get("SERVER_PORT", "")
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if request.host not in hosts or (request.headers.get("Origin") is not None and request.headers["Origin"] not in {f"http://{host}" for host in hosts}):
        return jsonify({"error": "Invalid Host or Origin"}), 403


@app.route("/health")
def health():
    return jsonify({"ok": True, "app": "scrappy", "version": "0.5.0"})


@app.route("/api/setup", methods=["GET", "POST"])
def setup_state():
    config = _load_runtime_config()
    if request.method == "POST":
        config["setup_complete"] = True
        _write_runtime_config(config)
    return jsonify({"complete": bool(config.get("setup_complete"))})


@app.route("/api/quit", methods=["POST"])
def quit_server():
    import threading
    if _SERVER is None:
        return jsonify({"error": "Server shutdown unavailable"}), 503
    threading.Thread(target=_SERVER.shutdown, daemon=True).start()
    # Backstop: if shutdown stalls, exit the process so the exe is never left running.
    threading.Timer(2.0, os._exit, args=(0,)).start()
    return jsonify({"ok": True})


@app.route("/FIRECRAWL.md")
def firecrawl_help():
    path = paths.bundled_path("FIRECRAWL.md")
    if not path.exists():
        return "Help unavailable", 404
    return send_file(path, mimetype="text/plain")


@app.route("/api/ai", methods=["GET", "POST", "DELETE"])
def ai_settings():
    import ai_providers
    import keyring
    config = _load_runtime_config()
    settings = config.get("ai", {})
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        provider = body.get("provider")
        if provider not in ai_providers.PROVIDERS:
            return jsonify({"error": "Unknown provider"}), 400
        model = body.get("model") or ai_providers.PROVIDERS[provider][1]
        if not isinstance(model, str) or len(model) > 200:
            return jsonify({"error": "Invalid model"}), 400
        key = body.get("key", "")
        if not isinstance(key, str):
            return jsonify({"error": "Invalid key"}), 400
        key = key.strip()
        if key and provider != "none":
            try:
                keyring.set_password("Scrappy", provider, key)
            except Exception:
                return jsonify({"error": "Credential store unavailable"}), 503
        settings = {"provider": provider, "model": model}
        config["ai"] = settings
        _write_runtime_config(config)
    elif request.method == "DELETE":
        provider = settings.get("provider", "none")
        if provider != "none":
            try:
                keyring.delete_password("Scrappy", provider)
            except keyring.errors.PasswordDeleteError:
                pass
            except Exception:
                return jsonify({"error": "Credential store unavailable"}), 503
        settings = {"provider": "none", "model": ""}
        config["ai"] = settings
        _write_runtime_config(config)
    provider = settings.get("provider", "none")
    return jsonify({"provider": provider, "model": settings.get("model", ""),
                    "connected": ai_providers.connected(provider)})


@app.route("/api/firecrawl", methods=["GET", "POST", "DELETE"])
def firecrawl_settings():
    import ai_providers
    import keyring
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        if not isinstance(body.get("key"), str) or not body["key"].strip():
            return jsonify({"error": "Key required"}), 400
        try:
            keyring.set_password("Scrappy", "firecrawl", body["key"].strip())
        except Exception:
            return jsonify({"error": "Credential store unavailable"}), 503
    elif request.method == "DELETE":
        try:
            keyring.delete_password("Scrappy", "firecrawl")
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception:
            return jsonify({"error": "Credential store unavailable"}), 503
    return jsonify({"connected": bool(ai_providers.credential("firecrawl"))})


@app.route("/api/export")
def export_data():
    conn = db.connect(str(DB_PATH))
    try:
        result = {}
        for table in ("listings", "feedback", "notes", "pipeline_status"):
            cursor = conn.execute("SELECT * FROM " + table)
            columns = [col[0] for col in cursor.description]
            result[table] = [dict(zip(columns, row)) for row in cursor.fetchall()]
        result["criteria"] = _load_runtime_config().get("criteria", {})
        return jsonify(result)
    finally:
        conn.close()


@app.route("/api/import", methods=["POST"])
def import_data():
    payload = request.get_json(silent=True)
    tables = ("listings", "feedback", "notes", "pipeline_status")
    if not isinstance(payload, dict) or any(not isinstance(payload.get(t, []), list) for t in tables):
        return jsonify({"error": "Invalid backup"}), 400
    if not isinstance(payload.get("criteria", {}), dict):
        return jsonify({"error": "Invalid criteria"}), 400
    conn = db.connect(str(DB_PATH))
    try:
        with conn:
            for table in tables:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(" + table + ")")}
                for record in payload.get(table, []):
                    if not isinstance(record, dict) or not set(record).issubset(columns):
                        raise ValueError("Invalid record")
                    record = dict(record)
                    if table == "feedback":
                        record.pop("id", None)
                        names = list(record)
                        if not names:
                            raise ValueError("Empty record")
                        where = " AND ".join('"' + col + '" IS ?' for col in names)
                        if conn.execute("SELECT 1 FROM feedback WHERE " + where, [record[n] for n in names]).fetchone():
                            continue
                    names = list(record)
                    if not names:
                        raise ValueError("Empty record")
                    names_sql = ",".join('"' + col + '"' for col in names)
                    placeholders = ",".join("?" for _ in names)
                    if table != "feedback":
                        key = "id" if table == "listings" else "listing_id"
                        if key not in record:
                            raise ValueError("Missing identifier")
                        updates = ",".join('"' + col + '"=excluded."' + col + '"' for col in names if col != key)
                        conflict = " ON CONFLICT(" + key + ") DO " + ("UPDATE SET " + updates if updates else "NOTHING")
                    else:
                        conflict = ""
                    conn.execute("INSERT INTO " + table + " (" + names_sql + ") VALUES (" + placeholders + ")" + conflict,
                                 [record[n] for n in names])
        if "criteria" in payload:
            config = _load_runtime_config()
            allowed = config.get("criteria", {})
            config["criteria"] = {**allowed, **payload["criteria"]}
            _write_runtime_config(config)
        return jsonify({"ok": True})
    except (ValueError, TypeError, __import__("sqlite3").Error):
        return jsonify({"error": "Invalid backup"}), 400
    finally:
        conn.close()


_OAUTH_PENDING = {}


@app.route("/auth/openrouter/start")
def openrouter_start():
    import base64
    import hashlib
    import secrets
    import time
    from urllib.parse import urlencode
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(24)
    for expired in [key for key, value in _OAUTH_PENDING.items() if value[1] < time.monotonic()]:
        del _OAUTH_PENDING[expired]
    _OAUTH_PENDING[state] = (verifier, time.monotonic() + 600)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    port = request.environ.get("SERVER_PORT", "5055")
    callback = f"http://127.0.0.1:{int(port)}/auth/openrouter/callback/{state}"
    return redirect("https://openrouter.ai/auth?" + urlencode({
        "callback_url": callback, "code_challenge": challenge, "code_challenge_method": "S256"}))


@app.route("/auth/openrouter/callback/<state>")
def openrouter_callback(state):
    import time
    import httpx
    import keyring
    pending = _OAUTH_PENDING.pop(state, None)
    code = request.args.get("code")
    if not pending or pending[1] < time.monotonic() or not code:
        return "Sign-in expired or invalid", 400
    try:
        response = httpx.post("https://openrouter.ai/api/v1/auth/keys", json={
            "code": code, "code_verifier": pending[0], "code_challenge_method": "S256"},
            timeout=30, trust_env=False)
        response.raise_for_status()
        keyring.set_password("Scrappy", "openrouter", response.json()["key"])
        config = _load_runtime_config()
        config["ai"] = {"provider": "openrouter", "model": "google/gemini-2.5-flash"}
        _write_runtime_config(config)
    except Exception:
        return "Sign-in failed", 502
    return redirect("/favorites?ai=connected")


@app.route("/feedback", methods=["POST"])
def feedback():
    body = request.get_json(silent=True) or {}
    if not body.get("listing_id") or not body.get("signal"):
        return jsonify(
            {"ok": False, "error": "listing_id and signal are required"}
        ), 400
    if body.get("signal") not in VALID_SIGNALS:
        return jsonify(
            {"ok": False, "error": f"signal must be one of {sorted(VALID_SIGNALS)}"}
        ), 400
    # Parse structured tags from reason text; caller-supplied tags take priority
    from taste import parse_reason_to_tags  # noqa: PLC0415
    reason_str = body.get("reason", "")
    signal_str = body.get("signal", "")
    tags = list(body.get("tags") or [])
    if not tags and reason_str:
        tags = parse_reason_to_tags(reason_str, signal=signal_str)
    # Auto-inject wrong_location tag when reason signals a bad city match
    if _has_wrong_city(reason_str) and "wrong_location" not in tags:
        tags.append("wrong_location")

    try:
        conn = db.connect(str(DB_PATH))
        if not _listing_exists(conn, body.get("listing_id")):
            return jsonify({"ok": False, "error": "listing not found"}), 404
        db.save_feedback(
            conn,
            body.get("listing_id"),
            body.get("signal"),
            body.get("reason", ""),
            tags=tags or None,
            parse_model=body.get("parse_model"),
        )
    except Exception as exc:
        app.logger.exception("feedback save failed: %s", exc)
        return jsonify({"ok": False, "error": "failed to save feedback"}), 500
    finally:
        if "conn" in locals():
            conn.close()

    try:
        # Regenerate favorites HTML any time feedback changes
        _regen_favorites(str(DB_PATH))
    except Exception as exc:
        app.logger.warning("favorites regeneration failed after feedback save: %s", exc)
        return jsonify({"ok": True, "warning": "favorites regeneration failed"})

    return jsonify({"ok": True})


@app.route("/")
def root():
    return redirect("/favorites")


@app.route("/favorites")
def favorites():
    """Serve the live favorites HTML page."""
    path = generate_and_save(str(DB_PATH))
    resp = send_file(str(path))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.route("/cadence", methods=["POST"])
def cadence():
    body = request.get_json(silent=True) or {}
    if "days" not in body:
        return jsonify({"ok": False, "error": "days is required"}), 400

    try:
        cadence_days = int(body["days"])
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "days must be an integer"}), 400

    config = _load_runtime_config()
    config["cadence_days"] = cadence_days
    _write_runtime_config(config)
    return jsonify({"ok": True, "cadence_days": cadence_days})


_WRONG_CITY_PHRASES = frozenset({"wrong city", "wrong location", "location mismatch"})


def _has_wrong_city(reason: str) -> bool:
    r = reason.lower()
    return any(phrase in r for phrase in _WRONG_CITY_PHRASES)


CRITERIA_KEYS = {
    "neighborhoods",
    "price_min",
    "price_max",
    "bedrooms",
    "bathrooms",
    "furnished",
    "min_floor",
    "min_sqm",
    "require_photos",
    "min_vision_score",
    "geo_filter",
}


def _validate_criteria_updates(updates, current):
    if not isinstance(updates, dict):
        return "body must be a JSON object"
    for k in updates:
        if k not in CRITERIA_KEYS:
            return f"unknown key: {k}"
    merged = {**current, **updates}
    # bool first (bool is subclass of int)
    for bk in ("furnished", "require_photos"):
        if bk in updates and not isinstance(updates[bk], bool):
            return f"{bk} must be boolean"
    for ik in ("price_min", "price_max", "bedrooms", "bathrooms", "min_floor"):
        if ik in updates:
            v = updates[ik]
            if isinstance(v, bool) or not isinstance(v, int):
                return f"{ik} must be int"
            if ik in ("bedrooms", "bathrooms", "min_floor") and v < 0:
                return f"{ik} must be non-negative"
            if ik in ("price_min", "price_max") and v < 0:
                return f"{ik} must be non-negative"
    if "min_sqm" in updates:
        v = updates["min_sqm"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            return "min_sqm must be a non-negative number"
    if "min_vision_score" in updates:
        v = updates["min_vision_score"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or v > 10:
            return "min_vision_score must be between 0 and 10"
    if "neighborhoods" in updates:
        v = updates["neighborhoods"]
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            return "neighborhoods must be list[str]"
    if "geo_filter" in updates:
        v = updates["geo_filter"]
        if v is not None:
            if not isinstance(v, dict) or v.get("type") != "polygon":
                return "geo_filter must be null or {type: 'polygon', coords: [[lat,lng],...]}"
            coords = v.get("coords")
            if not isinstance(coords, list) or len(coords) < 3:
                return "geo_filter.coords must be a list of at least 3 [lat,lng] pairs"
    pmin = merged.get("price_min")
    pmax = merged.get("price_max")
    if isinstance(pmin, int) and isinstance(pmax, int) and pmin > pmax:
        return "price_min must be <= price_max"
    return None


@app.route("/criteria", methods=["GET"])
def get_criteria():
    try:
        cfg = _load_runtime_config()
    except Exception as exc:
        app.logger.exception("config read failed: %s", exc)
        return jsonify({"error": "failed to read config"}), 500
    return jsonify(cfg.get("criteria", {}))


@app.route("/criteria", methods=["POST"])
def post_criteria():
    body = request.get_json(silent=True)
    if body is None:
        return jsonify({"error": "body must be JSON"}), 400
    try:
        cfg = _load_runtime_config()
    except Exception as exc:
        app.logger.exception("config read failed: %s", exc)
        return jsonify({"error": "failed to read config"}), 500
    current = cfg.get("criteria", {})
    err = _validate_criteria_updates(body, current)
    if err:
        return jsonify({"error": err}), 400
    new_criteria = {**current, **body}
    cfg["criteria"] = new_criteria
    try:
        _write_runtime_config(cfg)
    except Exception as exc:
        app.logger.exception("config write failed: %s", exc)
        return jsonify({"error": "failed to write config"}), 500
    return jsonify({"criteria": new_criteria})


@app.route("/run", methods=["POST"])
def run_now():
    global _RUN_PROCESS, _RUN_LOG_HANDLE
    if _run_in_progress():
        return jsonify({"ok": False, "error": "run already in progress"}), 409
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    log_path = _run_log_path()
    if _RUN_LOG_HANDLE is not None and not _RUN_LOG_HANDLE.closed:
        _RUN_LOG_HANDLE.close()
    _RUN_LOG_HANDLE = log_path.open("w", encoding="utf-8")
    _RUN_LOG_HANDLE.write("[server] Run triggered\n")
    _RUN_LOG_HANDLE.flush()
    _RUN_PROCESS = subprocess.Popen(
        [sys.executable, "--run"] if getattr(sys, "frozen", False) else [sys.executable, "-u", str(APP_DIR / "launcher.py"), "--run"],
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        cwd=APP_DIR,
        stdout=_RUN_LOG_HANDLE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return jsonify({"ok": True, "message": "run triggered"})


@app.route("/listings")
def listings():
    """Return all listings grouped by feedback state, with pipeline_status merged."""
    try:
        conn = db.connect(str(DB_PATH))
        # Tab-coherence fix (2026-04-16): cross-run duplicates (different id,
        # same address_hash) used to land in different tabs based on each
        # row's own feedback — causing the same property to appear as both
        # approved and inbox. Collapse by address_hash, keeping the row with
        # the most recent feedback activity (falls back to highest id).
        cur = conn.execute("""
            WITH ranked AS (
                SELECT
                    l.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY COALESCE(l.address_hash, l.id)
                        ORDER BY
                            CASE WHEN EXISTS(
                                SELECT 1 FROM feedback f
                                WHERE f.listing_id = l.id
                                  AND f.signal IN ('up','reset')
                                  AND f.id = (
                                      SELECT MAX(f2.id) FROM feedback f2 WHERE f2.listing_id = l.id
                                  )
                            ) THEN 0 ELSE 1 END ASC,
                            l.first_seen DESC,
                            l.id DESC
                    ) AS rn
                FROM listings l
            )
            SELECT
                r.id, r.source, r.url, r.price, r.bedrooms, r.bathrooms,
                r.floor, r.sqm, r.neighborhood, r.address, r.title,
                r.latitude, r.longitude,
                r.photos_json, r.vision_score, r.vision_flags, r.nice_to_haves,
                r.first_seen, r.last_seen, r.alerted, r.dead_checks,
                COALESCE(
                    (SELECT f.signal FROM feedback f
                     WHERE f.listing_id = r.id ORDER BY f.id DESC LIMIT 1),
                    'inbox'
                ) AS latest_signal,
                ps.status AS pipeline_status,
                ps.note AS pipeline_note,
                ps.stars AS pipeline_stars,
                ps.order_rank AS pipeline_order,
                n.body AS note_body
            FROM ranked r
            LEFT JOIN pipeline_status ps ON ps.listing_id = r.id
            LEFT JOIN notes n ON n.listing_id = r.id
            WHERE r.rn = 1
            ORDER BY r.vision_score DESC, r.price ASC
        """)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        conn.close()
    except Exception as exc:
        app.logger.exception("listings query failed: %s", exc)
        return jsonify({"error": "failed to query listings"}), 500

    approved, rejected, inbox = [], [], []
    for row in rows:
        # Deserialize JSON fields
        for field in ("photos_json", "vision_flags", "nice_to_haves"):
            try:
                row[field] = json.loads(row[field]) if row[field] else []
            except (json.JSONDecodeError, TypeError):
                row[field] = []
        # RC#18: $/m² value badge — computed server-side for consistency
        sqm = row.get("sqm")
        price = row.get("price") or 0
        row["value_per_sqm"] = round(price / sqm) if sqm and sqm > 0 else None
        signal = row.pop("latest_signal")
        if signal == "up":
            approved.append(row)
        elif signal == "down":
            rejected.append(row)
        else:
            inbox.append(row)

    # Approved tab respects user-set drag order (nulls last, then by SQL order)
    approved.sort(key=lambda r: (r.get("pipeline_order") is None,
                                 r.get("pipeline_order") or 0))

    return jsonify({
        "approved": approved,
        "rejected": rejected,
        "inbox": inbox,
        "counts": {
            "approved": len(approved),
            "rejected": len(rejected),
            "inbox": len(inbox),
            "total": len(rows),
        },
    })


@app.route("/run_meta")
def run_meta():
    """Return last run log tail + summarized scraper source health."""
    result: dict = {"log_tail": [], "sources": {}, "running": _run_in_progress()}

    log_path = DATA_DIR / "last_run.log"
    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            result["log_tail"] = lines[-50:]
        except Exception as exc:
            app.logger.warning("log read failed: %s", exc)

    health_path = DATA_DIR / "scraper_health.json"
    if health_path.exists():
        try:
            entries = json.loads(health_path.read_text(encoding="utf-8"))
            summary: dict = {}
            for entry in entries:
                source = entry.get("source") or "unknown"
                if source not in summary:
                    summary[source] = {
                        "source": source,
                        "checks": 0,
                        "success": 0,
                        "avg_bytes": 0,
                        "errors": [],
                        "last_ts": 0,
                        "last_transport": "",
                    }
                s = summary[source]
                s["checks"] += 1
                if entry.get("status") == 200:
                    s["success"] += 1
                s["avg_bytes"] = (s["avg_bytes"] * (s["checks"] - 1) + entry.get("bytes", 0)) / s["checks"]
                if entry.get("error"):
                    s["errors"].append(entry["error"])
                if entry.get("ts", 0) > s["last_ts"]:
                    s["last_ts"] = entry["ts"]
                    s["last_transport"] = entry.get("transport", "")

            # Format for response
            for k in summary:
                summary[k]["avg_bytes"] = int(summary[k]["avg_bytes"])
                summary[k]["errors"] = list(set(summary[k]["errors"]))[:5]
                # Success rate percentage
                rate = (summary[k]["success"] / summary[k]["checks"]) * 100 if summary[k]["checks"] > 0 else 0
                summary[k]["success_rate"] = f"{rate:.1f}%"

            result["sources"] = summary
        except Exception as exc:
            app.logger.warning("scraper_health read failed: %s", exc)

    meta_path = DATA_DIR / "run_meta.json"
    if meta_path.exists():
        try:
            result.update(json.loads(meta_path.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            pass
    return jsonify(result)


@app.route("/taste")
def taste():
    """Return TasteProfile weights, signal counts, and hard-filter candidates."""
    try:
        from taste import TasteProfile  # noqa: PLC0415
        conn = db.connect(str(DB_PATH))
        profile = TasteProfile.from_db(conn)
        conn.close()
    except Exception as exc:
        app.logger.exception("taste profile failed: %s", exc)
        return jsonify({"error": "failed to build taste profile"}), 500

    return jsonify({
        "has_signal": profile.has_signal(),
        "total_feedback": profile._total,
        "weights": profile.ranker_weights(),
        "hard_filter_candidates": profile.hard_filter_candidates(),
        "top_positive": sorted(profile._pos.items(), key=lambda x: x[1], reverse=True)[:5],
        "top_negative": sorted(profile._neg.items(), key=lambda x: x[1], reverse=True)[:5],
    })


@app.route("/note", methods=["POST"])
def post_note():
    """Persist a free-text note for a listing without mutating review state."""
    body = request.get_json(silent=True) or {}
    listing_id = body.get("listing_id")
    text = (body.get("body") or "").strip()
    if not listing_id:
        return jsonify({"ok": False, "error": "listing_id is required"}), 400
    from taste import parse_reason_to_tags  # noqa: PLC0415
    # Use signal="" so both positive and negative tags are extracted from notes
    tags = parse_reason_to_tags(text, signal="") if text else []
    try:
        conn = db.connect(str(DB_PATH))
        if not _listing_exists(conn, listing_id):
            return jsonify({"ok": False, "error": "listing not found"}), 404
        db.upsert_note(conn, listing_id, text)
    except Exception as exc:
        app.logger.exception("note save failed: %s", exc)
        return jsonify({"ok": False, "error": "failed to save note"}), 500
    finally:
        if "conn" in locals():
            conn.close()
    return jsonify({"ok": True, "tags": tags})


@app.route("/pipeline_status", methods=["POST"])
def post_pipeline_status():
    """Upsert pipeline status for a listing (stars, stage, note)."""
    body = request.get_json(silent=True) or {}
    listing_id = body.get("listing_id")
    if not listing_id:
        return jsonify({"ok": False, "error": "listing_id is required"}), 400
    status_present = "status" in body
    note_present = "note" in body
    stars_present = "stars" in body
    order_rank_present = "order_rank" in body
    if not any((status_present, note_present, stars_present, order_rank_present)):
        return jsonify(
            {
                "ok": False,
                "error": "at least one of status, note, stars, or order_rank is required",
            }
        ), 400
    status = body.get("status") if status_present else None
    if status_present and (not isinstance(status, str) or not status.strip()):
        return jsonify({"ok": False, "error": "status must be a non-empty string"}), 400
    note = body.get("note") if note_present else None
    if note_present and not isinstance(note, str):
        return jsonify({"ok": False, "error": "note must be a string"}), 400
    stars = body.get("stars") if stars_present else None
    if stars_present and (not isinstance(stars, int) or stars < 0 or stars > 5):
        return jsonify({"ok": False, "error": "stars must be integer 0–5"}), 400
    order_rank = body.get("order_rank") if order_rank_present else None
    if order_rank_present and (not isinstance(order_rank, int) or order_rank < 0):
        return jsonify({"ok": False,
                        "error": "order_rank must be non-negative int"}), 400
    try:
        conn = db.connect(str(DB_PATH))
        if not _listing_exists(conn, listing_id):
            return jsonify({"ok": False, "error": "listing not found"}), 404
        db.upsert_pipeline_status(
            conn,
            listing_id,
            status=status,
            note=note,
            stars=stars,
            order_rank=order_rank,
        )
        saved = conn.execute(
            "SELECT status, note, stars, order_rank FROM pipeline_status WHERE listing_id=?",
            (listing_id,),
        ).fetchone()
    except Exception as exc:
        app.logger.exception("pipeline_status upsert failed: %s", exc)
        return jsonify({"ok": False, "error": "failed to save pipeline status"}), 500
    finally:
        if "conn" in locals():
            conn.close()
    return jsonify(
        {
            "ok": True,
            "listing_id": listing_id,
            "status": saved[0],
            "note": saved[1],
            "stars": saved[2],
            "order_rank": saved[3],
        }
    )


def serve():
    global _SERVER
    from werkzeug.serving import make_server
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    db.connect(str(DB_PATH)).close()
    port = int(os.getenv("SCRAPPY_PORT", "5055"))
    while port <= 65535:
        try:
            server = make_server("127.0.0.1", port, app)
            break
        except (OSError, SystemExit):
            port += 1
    else:
        raise RuntimeError("No available port")
    _SERVER = server
    print(f"[server] Listening on 127.0.0.1:{port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        _SERVER = None


if __name__ == "__main__":
    serve()
