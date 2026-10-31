import json
import time
import os
import subprocess
import sys
import urllib.request
from datetime import date
from pathlib import Path

import db
from config_loader import (
    load_config as load_scrapy_config,
    resolve_local_config_path,
    update_base_config,
)
from scrapers.inmuebles24 import Inmuebles24Scraper
from scrapers.mercadolibre import MercadoLibreScraper
from scrapers.vivanuncios import VivanunciosScraper
from scrapers.lamudi import LamudiScraper
from scrapers.propiedades import PropiedadesScraper
from scrapers.trovit import TrovitScraper
from scrapers.casa_mx import CasaMxScraper
from scrapers.pincali import PincaliScraper
from scrapers.httpx_adapter import HttpxAdapter
from pipeline.dedup import deduplicate
from prefilter import PreFilter
from pipeline.filter import hard_filter
from pipeline.ranker import rank
from vision import VisionScorer
from report import generate_html
from notify import validate_urls, filter_cdmx
import paths
from taste import TasteProfile

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = paths.bundled_path("config.json")
DB_PATH = paths.db_path()
REPORTS_DIR = paths.reports_dir()
RUN_LOCK_PATH = paths.data_dir() / "run.lock"


def _ensure_feedback_server() -> None:
    """Start feedback_server.py on port 5055 if not already running."""
    try:
        urllib.request.urlopen("http://localhost:5055/health", timeout=1)
        return  # already up
    except Exception:
        pass
    script = APP_DIR / "feedback_server.py"
    subprocess.Popen(
        [sys.executable, str(script)],
        cwd=APP_DIR,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    print("[run] Feedback server started on :5055")


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def _acquire_run_lock() -> bool:
    RUN_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if RUN_LOCK_PATH.exists():
        try:
            payload = json.loads(RUN_LOCK_PATH.read_text(encoding="utf-8"))
            pid = int(payload.get("pid") or 0)
        except Exception:
            pid = 0
        if _pid_is_running(pid):
            return False
        RUN_LOCK_PATH.unlink(missing_ok=True)
    RUN_LOCK_PATH.write_text(
        json.dumps({"pid": os.getpid(), "started_at": date.today().isoformat()}),
        encoding="utf-8",
    )
    return True


def _release_run_lock() -> None:
    try:
        if not RUN_LOCK_PATH.exists():
            return
        payload = json.loads(RUN_LOCK_PATH.read_text(encoding="utf-8"))
        if int(payload.get("pid") or 0) == os.getpid():
            RUN_LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        RUN_LOCK_PATH.unlink(missing_ok=True)


def load_config() -> dict:
    return load_scrapy_config(CONFIG_PATH)


def save_last_run(_config: dict) -> None:
    update_base_config({"last_run": date.today().isoformat()})


def _source_status(name, results, source_error, firecrawl_available):
    needs_key = name in {"inmuebles24", "mercadolibre", "propiedades"} and source_error and not firecrawl_available
    status = "needs_firecrawl" if needs_key else "error" if source_error else "ok" if results else "empty"
    detail = "Firecrawl fallback unavailable" if needs_key else "Source failed" if source_error else ""
    return {"status": status, "count": len(results), "detail": detail}


def main(start_server=True):
    if not _acquire_run_lock():
        print("[run] Another run is already in progress; exiting.")
        return
    config = None
    conn = None
    try:
        if start_server and not getattr(sys, "frozen", False):
            _ensure_feedback_server()
        config = load_config()
        print(f"[run] Scrappy starting — {date.today().isoformat()}")

        adapters = [
            cls(config)
            for cls, key in [
                (Inmuebles24Scraper, "inmuebles24"),
                (MercadoLibreScraper, "mercadolibre"),
                (VivanunciosScraper, "vivanuncios"),
                (LamudiScraper, "lamudi"),
                (PropiedadesScraper, "propiedades"),
                (TrovitScraper, "trovit"),
                (CasaMxScraper, "casa_mx"),
                (PincaliScraper, "pincali"),
            ]
            if config["sources"].get(key)
        ]

        from ai_providers import credential
        source_status = {}
        raw = []
        started_at = time.time()

        def _write_progress(stage):
            # Live progress for GET /run_meta while the run works (replaces the previous run's file).
            (paths.data_dir() / "run_meta.json").write_text(json.dumps({
                "stage": stage, "started_at": started_at, "elapsed_s": round(time.time() - started_at),
                "ai_status": "pending", "sources": source_status}), encoding="utf-8")

        _write_progress("scraping")
        for adapter in adapters:
            name = adapter.SOURCE_NAME
            if name == "pincali" and not credential("firecrawl"):
                source_status[name] = {"status": "needs_firecrawl", "count": 0, "detail": "Firecrawl is not configured"}
                continue
            try:
                results = adapter.fetch_listings()
                source_error = getattr(adapter, "_source_error", False)
                source_status[name] = _source_status(name, results, source_error, bool(credential("firecrawl")))
                raw.extend(results)
                print(f"[run] {name}: {len(results)} raw listings")
            except Exception:
                source_status[name] = {"status": "error", "count": 0, "detail": "Source failed"}
                print(f"[run] {name}: source failed")
            _write_progress("scraping")
        _write_progress("scoring")

        # Flush health log so feedback_server can show source stats
        HttpxAdapter.write_health_log()

        prefilter = PreFilter(config, db_path=DB_PATH)
        filtered_pre = prefilter.filter(raw)
        print(f"[run] Pre-filter: {len(raw)} -> {len(filtered_pre)}")

        # Cross-run dedup: seed dedup gates with URLs + address_hashes already in the
        # DB so a listing that resurfaces with a new scraper-side id cannot be scored
        # or alerted a second time. Root-fix for recurring "Duplicate listing" feedback.
        conn = db.connect(str(DB_PATH))
        known_urls = {r[0] for r in conn.execute("SELECT url FROM listings").fetchall()}
        known_hashes = {
            r[0]
            for r in conn.execute(
                "SELECT address_hash FROM listings WHERE address_hash IS NOT NULL"
            ).fetchall()
        }
        deduped = deduplicate(
            filtered_pre, known_urls=known_urls, known_hashes=known_hashes
        )
        cross_run_blocked = len(filtered_pre) - len(deduped)
        filtered = hard_filter(deduped, config)
        print(
            f"[run] After dedup+filter: {len(filtered)} listings "
            f"(cross-run duplicates blocked: {cross_run_blocked})"
        )
        pos_ids, neg_ids, _pos_tags, _neg_tags = db.get_feedback_examples(conn)
        profile = TasteProfile.from_db(conn)
        scorer = VisionScorer(config, taste_profile=profile)
        sdk_probe = scorer.probe_and_report()
        if not sdk_probe["ok"]:
            print(f"[run] Vision SDK unavailable: {sdk_probe['stderr']!r}")
        if profile.has_signal():
            candidates = profile.hard_filter_candidates()
            if candidates:
                print(f"[run] Taste -- hard filter candidates (not enforced): {candidates}")

        scored = []
        with conn:
            for listing in filtered:
                result = scorer.score_listing(listing, pos_ids, neg_ids)
                if result is None:
                    # Hard-rejected (floor_risk or light_score < 4) — drop entirely
                    continue
                d = listing.to_db_dict()
                d["vision_score"] = result["overall_score"]
                d["vision_flags"] = json.dumps(result.get("flags", []))
                # Merge nice-to-haves from vision with what scraper already found
                scraper_nth = listing.nice_to_haves or {}
                d["nice_to_haves"] = json.dumps(
                    {
                        "washer_dryer": result.get("has_washer_dryer", False)
                        or scraper_nth.get("washer_dryer", False),
                        "fridge": result.get("has_fridge", False)
                        or scraper_nth.get("fridge", False),
                    }
                )
                db.upsert_listing(conn, d, commit=False)
                scored.append({**d, "vision_score": result["overall_score"]})

        paths.data_dir().mkdir(parents=True, exist_ok=True)
        (paths.data_dir() / "run_meta.json").write_text(
            json.dumps({"stage": "done", "started_at": started_at, "elapsed_s": round(time.time() - started_at), "ai_status": scorer.ai_status, "sources": source_status}), encoding="utf-8")

        ranked = rank(scored, taste_profile=profile)
        print(f"[run] Ranked: {len(ranked)} listings")

        html = generate_html(ranked, cadence_days=config.get("cadence_days", 1))
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        report_path = REPORTS_DIR / f"report_{date.today().isoformat()}.html"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(html, encoding="utf-8")
        print(f"[run] Report: {report_path}")

        # Regenerate the Airbnb-style favorites cockpit so it reflects the latest scrape.
        import favorites  # local import to keep module-level import graph clean
        favorites.generate_and_save(DB_PATH)
        print(f"[run] Favorites cockpit regenerated: {favorites.REPORTS_DIR / 'favorites.html'}")

        # Read alerted IDs from DB — scored dicts don't carry the alerted field
        alerted_ids = {
            r[0] for r in conn.execute("SELECT id FROM listings WHERE alerted=1").fetchall()
        }
        # Exclude listings with negative feedback
        dismissed_ids = {
            r[0]
            for r in conn.execute(
                """
                SELECT f.listing_id
                FROM feedback f
                WHERE f.signal='down'
                  AND f.id = (
                      SELECT MAX(f2.id)
                      FROM feedback f2
                      WHERE f2.listing_id = f.listing_id
                  )
                """
            ).fetchall()
        }
        notify_candidates = filter_cdmx([
            listing
            for listing in ranked
            if listing["id"] not in alerted_ids and listing["id"] not in dismissed_ids
        ])[:10]
        valid = validate_urls(notify_candidates, conn=conn)
        print(f"[run] Valid listings: {len(valid)}")

    finally:
        if conn is not None:
            conn.close()
        if config is not None:
            try:
                save_last_run(config)
            except Exception as exc:
                print(f"[run] Failed to record last_run: {exc}")
        _release_run_lock()

    print("[run] Done.")


if __name__ == "__main__":
    main()
