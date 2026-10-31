"""One-shot + incremental geocode / title backfill for the listings table.

For every listing with NULL ``latitude`` (or NULL ``longitude``), fetch the
listing's detail page, parse schema.org JSON-LD for a structured location
and name, and write the result back to the DB.

Runs independently of the scraper so it can be invoked ad-hoc to heal rows
that were ingested before the geocoding fields existed, OR wired into
``run.py`` as a post-scrape step (off by default to keep the hot path lean).

Design notes
------------
* Uses :class:`scrapers.httpx_adapter.HttpxAdapter` so Cloudflare-fronted
  sites (``inmuebles24.com``) come back via curl_cffi Chrome-TLS. We already
  pay for this on the scrape hot path; re-using it here keeps one behaviour.
* Rate-limited to 1 request/sec per host by default — polite and avoids
  triggering anti-bot on any single source.
* Idempotent: rows that already have ``latitude``/``longitude`` are skipped.
* Write path goes through :func:`db.upsert_listing` — no ad-hoc SQL here,
  so the same validation + column allow-list applies.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

import db  # noqa: E402  — triggers ALTER TABLE migrations on connect
from scrapers.base import _extract_geo_from_jsonld  # re-use the exact same gate
from scrapers.httpx_adapter import HttpxAdapter


APP_DIR = Path(__file__).resolve().parent
DEFAULT_DB = APP_DIR / "scrapy.db"
JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
# Inmuebles24 encodes coords as base64 inside script-tag const vars:
#   const mapLatOf =  "MTkuNDM4NDcxNjk5OTk5OTk3";
#   const mapLngOf =  "LTk5LjE3ODM4NTgwMDAw";
# Decoded these are plain ASCII decimal strings (lat + lng). Confirmed live
# 2026-04-16 by probing a Parques Plaza listing → 19.4385, -99.1784.
_I24_MAPLAT_RE = re.compile(r'const\s+mapLatOf\s*=\s*"([^"]+)"')
_I24_MAPLNG_RE = re.compile(r'const\s+mapLngOf\s*=\s*"([^"]+)"')
_I24_ADDRESS_RE = re.compile(
    r'\'address\'\s*:\s*\{\s*"name"\s*:\s*"([^"]+)"', re.MULTILINE
)
_I24_TITLE_META_RE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']{5,200})',
    re.IGNORECASE,
)

# CDMX + suburbs bounding box used as a sanity gate wherever geo is parsed.
_CDMX_LAT_MIN, _CDMX_LAT_MAX = 18.5, 20.5
_CDMX_LNG_MIN, _CDMX_LNG_MAX = -100.5, -98.0


def _in_cdmx_bbox(lat: float, lng: float) -> bool:
    return _CDMX_LAT_MIN <= lat <= _CDMX_LAT_MAX and _CDMX_LNG_MIN <= lng <= _CDMX_LNG_MAX


def extract_i24_base64_geo(html: str) -> dict:
    """Extract geo + title + address from an Inmuebles24 detail page.

    i24 base64-encodes mapLatOf / mapLngOf in a script block on every detail
    page. Decoded these are plain decimal strings. This is a much higher
    recovery path than JSON-LD for i24 because their detail pages don't ship
    schema.org geo. Also picks up the OG title + the inline address literal.
    """
    out: dict = {}
    lat_m = _I24_MAPLAT_RE.search(html)
    lng_m = _I24_MAPLNG_RE.search(html)
    if lat_m and lng_m:
        try:
            lat = float(base64.b64decode(lat_m.group(1)).decode("ascii").strip())
            lng = float(base64.b64decode(lng_m.group(1)).decode("ascii").strip())
            if _in_cdmx_bbox(lat, lng):
                out["latitude"] = lat
                out["longitude"] = lng
        except (ValueError, UnicodeDecodeError, base64.binascii.Error):
            pass
    addr_m = _I24_ADDRESS_RE.search(html)
    if addr_m:
        out["address"] = addr_m.group(1).strip()[:200]
    title_m = _I24_TITLE_META_RE.search(html)
    if title_m:
        out["title"] = title_m.group(1).strip()[:200]
    return out


_NOMINATIM_ENDPOINT = "https://nominatim.openstreetmap.org/search"
from launcher import VERSION
_NOMINATIM_UA = f"Scrappy/{VERSION} (+https://github.com/ZoeDepthTokyo/scrappy)"


def nominatim_geocode(
    address: str,
    neighborhood: Optional[str] = None,
    timeout: float = 15.0,
) -> tuple[Optional[float], Optional[float]]:
    """Geocode a Spanish/CDMX street address via Nominatim.

    Free OpenStreetMap service. 1 req/sec rate limit per their ToS — the
    caller is responsible for pacing. Returns (None, None) on no result or
    on a hit outside the CDMX sanity bbox.

    When ``neighborhood`` is supplied it's appended to the query only if
    the address itself doesn't already name the colonia — this rescues
    short street-only addresses like "EL TIZATE 5" which Nominatim would
    otherwise match in some random other city.
    """
    if not address or not address.strip():
        return None, None
    query = address.strip()
    q_lower = query.lower()
    if neighborhood and neighborhood.lower() not in q_lower:
        query = f"{query}, {neighborhood}"
    if "ciudad de" not in q_lower and "méxico" not in q_lower and "mexico" not in q_lower:
        query = query + ", Ciudad de México, México"
    try:
        resp = httpx.get(
            _NOMINATIM_ENDPOINT,
            params={
                "q": query,
                "format": "json",
                "limit": 1,
                "countrycodes": "mx",  # narrow to Mexico
                "addressdetails": 0,
            },
            headers={"User-Agent": _NOMINATIM_UA},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None, None
        data = resp.json()
        if not data:
            return None, None
        lat = float(data[0]["lat"])
        lng = float(data[0]["lon"])
        if not _in_cdmx_bbox(lat, lng):
            return None, None
        return lat, lng
    except (httpx.HTTPError, ValueError, KeyError):
        return None, None


# ──────────────── MercadoLibre rendered-HTML path (Lightpanda) ────────────────

_LIGHTPANDA_TIMEOUT = 45
_ML_ADDR_RE = re.compile(
    # MercadoLibre renders the address under "Ubicación" inside a
    # <p class="ui-vip-location__subtitle"> or similar structured span.
    r'class="(?:ui-vip-location__subtitle|ui-pdp-media__title|ui-pdp-color--BLACK)"[^>]*>([^<]{5,200})</',
    re.IGNORECASE,
)
_ML_OG_TITLE_RE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']{5,200})',
    re.IGNORECASE,
)
_ML_GMAPS_EMBED_RE = re.compile(
    # Rendered ML pages embed a static-map iframe whose src carries
    # "?center=19.x,-99.x" or "&ll=19.x,-99.x". Regex tolerates both.
    r'(?:center|ll|q)=(-?1[89]\.\d{3,7})(?:%2C|,|\s)(-9[89]\.\d{3,7})',
    re.IGNORECASE,
)


def _lightpanda_render(url: str, timeout: int = _LIGHTPANDA_TIMEOUT) -> str:
    """Render a URL with WSL2 Lightpanda and return the post-JS HTML.

    Returns empty string on any failure (WSL not installed, timeout,
    lightpanda error). Callers must tolerate an empty response — it's
    always a hint that a cheaper path should have worked first.
    """
    import subprocess

    cmd = [
        "wsl", "-d", "Ubuntu", "--",
        "bash", "-c",
        f"~/lightpanda fetch --dump html --wait-until networkidle '{url}'",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            return ""
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def extract_ml_rendered(html: str) -> dict:
    """Pull title + address + (optional) coords from a rendered ML page.

    The MercadoLibre listing DOM exposes the real address under a
    known class after hydration. We also check for a Google Maps embed
    whose URL contains the center coordinates.
    """
    out: dict = {}
    title_m = _ML_OG_TITLE_RE.search(html)
    if title_m:
        out["title"] = title_m.group(1).strip()[:200]
    addr_m = _ML_ADDR_RE.search(html)
    if addr_m:
        out["address"] = addr_m.group(1).strip()[:200]
    # If the map iframe rendered, grab coords from its src.
    for m in _ML_GMAPS_EMBED_RE.finditer(html):
        try:
            lat = float(m.group(1))
            lng = float(m.group(2))
            if _in_cdmx_bbox(lat, lng):
                out["latitude"] = lat
                out["longitude"] = lng
                break
        except ValueError:
            continue
    return out
# schema.org listing-ish types that are worth mining.
_LISTING_TYPES = {"Apartment", "House", "RealEstateListing", "Product", "Offer"}


def _walk_jsonld(data) -> list[dict]:
    """Flatten JSON-LD @graph / ItemList into a flat list of dicts."""
    out: list[dict] = []
    if isinstance(data, list):
        for d in data:
            out.extend(_walk_jsonld(d))
        return out
    if not isinstance(data, dict):
        return out
    # Recurse into common nesting containers.
    if "@graph" in data and isinstance(data["@graph"], list):
        out.extend(_walk_jsonld(data["@graph"]))
    if isinstance(data.get("itemListElement"), list):
        for e in data["itemListElement"]:
            if isinstance(e, dict):
                out.extend(_walk_jsonld(e.get("item", e)))
    out.append(data)
    return out


def _pick_listing_item(items: list[dict]) -> Optional[dict]:
    """Return the richest listing-looking item in a JSON-LD payload."""
    best = None
    best_score = -1
    for it in items:
        t = it.get("@type")
        if isinstance(t, list):
            t_match = any(x in _LISTING_TYPES for x in t)
        else:
            t_match = t in _LISTING_TYPES
        if not t_match:
            continue
        score = 0
        if it.get("geo"):
            score += 4
        if it.get("address"):
            score += 2
        if it.get("name"):
            score += 1
        if score > best_score:
            best, best_score = it, score
    return best


def extract_from_html(html: str) -> dict:
    """Pull (lat, lng, title, address) from a listing-detail HTML blob."""
    for block in JSONLD_RE.findall(html):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        item = _pick_listing_item(_walk_jsonld(data))
        if not item:
            continue
        lat, lng = _extract_geo_from_jsonld(item)
        name = (item.get("name") or "").strip()[:200]
        addr_field = item.get("address")
        if isinstance(addr_field, dict):
            address = (addr_field.get("streetAddress") or addr_field.get("name") or "").strip()
        else:
            address = (str(addr_field or "")).strip()
        if lat is not None or name or address:
            return {"latitude": lat, "longitude": lng, "title": name, "address": address}
    return {}


def _rows_needing_backfill(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT id, source, url, neighborhood, latitude, longitude, title, address
        FROM listings
        WHERE (latitude IS NULL OR longitude IS NULL OR title IS NULL OR title = '')
          AND url IS NOT NULL AND url != ''
        ORDER BY alerted DESC, first_seen DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def run(
    db_path: Path = DEFAULT_DB,
    limit: int = 200,
    per_host_delay_s: float = 1.0,
    verbose: bool = True,
    use_nominatim: bool = True,
) -> dict:
    """Backfill geo + title for rows missing them. Returns a summary dict.

    When ``use_nominatim`` is true, rows still missing coords after HTML
    extraction get one Nominatim lookup (rate-limited to 1 req/s per ToS).
    This rescues MercadoLibre listings whose detail pages hide coords
    behind JS, and any other source with address-only data.
    """
    conn = db.connect(str(db_path))
    rows = _rows_needing_backfill(conn, limit)
    if verbose:
        print(f"[geo-backfill] candidates: {len(rows)} (limit={limit})")

    adapter = HttpxAdapter(
        timeout=25.0, source_name="geo-backfill", rotate_ua=True, use_curl_cffi_on_403=True
    )
    last_host_hit: dict[str, float] = defaultdict(float)
    last_nominatim_hit = 0.0
    stats = {"scanned": 0, "geo": 0, "title": 0, "address": 0, "nominatim_geo": 0, "fail": 0}

    try:
        for row in rows:
            stats["scanned"] += 1
            url = row["url"]
            host = urlparse(url).netloc
            wait = per_host_delay_s - (time.time() - last_host_hit[host])
            if wait > 0:
                time.sleep(wait)
            try:
                html = adapter.fetch_html(url)
            except Exception as exc:
                stats["fail"] += 1
                if verbose:
                    print(f"[geo-backfill] {row['id']}  FETCH FAIL  {exc}")
                last_host_hit[host] = time.time()
                continue
            last_host_hit[host] = time.time()
            if not html:
                stats["fail"] += 1
                if verbose:
                    print(f"[geo-backfill] {row['id']}  empty body")
                continue
            # Layered extraction — each step fills only the keys previous
            # steps missed. Order matters: JSON-LD is cleanest (schema.org),
            # then site-specific blob parsers, then the Lightpanda render
            # path for JS-heavy sites, then Nominatim against the address.
            found = extract_from_html(html) or {}
            if row["source"] == "inmuebles24":
                i24_extra = extract_i24_base64_geo(html)
                for k, v in i24_extra.items():
                    if not found.get(k):  # JSON-LD wins when both present
                        found[k] = v
            if row["source"] == "mercadolibre" and (not found.get("address")):
                # ML detail pages are client-rendered React shells; the
                # address lives in the DOM only after JS runs. Lightpanda
                # gives us a rendered copy we can scrape structurally.
                rendered = _lightpanda_render(url)
                if rendered:
                    ml_extra = extract_ml_rendered(rendered)
                    for k, v in ml_extra.items():
                        if not found.get(k):
                            found[k] = v

            # Build updates dict incrementally — null-safe, never overwrites
            # existing values. Nominatim runs AFTER extraction so it picks
            # up freshly-extracted addresses in the same pass.
            updates: dict = {}
            if row["latitude"] is None and found.get("latitude") is not None:
                updates["latitude"] = found["latitude"]
                updates["longitude"] = found.get("longitude")
                stats["geo"] += 1
            if (not row["title"]) and found.get("title"):
                updates["title"] = found["title"]
                stats["title"] += 1
            if (not row["address"]) and found.get("address"):
                updates["address"] = found["address"]
                stats["address"] += 1

            # Nominatim rescue: ANY row with an address but no coords. This
            # runs even when HTML extraction yielded nothing, so a stored
            # address from a previous backfill is still a usable anchor.
            resolved_addr = updates.get("address") or row["address"]
            no_coords_yet = (
                row["latitude"] is None and updates.get("latitude") is None
            )
            if use_nominatim and no_coords_yet and resolved_addr:
                # Skip obviously-junk addresses (titles accidentally stored
                # in the address column from an earlier buggy run).
                if len(resolved_addr) > 120 or "recámara" in resolved_addr.lower() \
                        or "rec maras" in resolved_addr.lower() \
                        or "departamento en renta" in resolved_addr.lower():
                    if verbose:
                        print(f"[geo-backfill] {row['id']}  address looks like a title; skipping nominatim")
                else:
                    wait = 1.0 - (time.time() - last_nominatim_hit)
                    if wait > 0:
                        time.sleep(wait)
                    nlat, nlng = nominatim_geocode(
                        resolved_addr, neighborhood=row["neighborhood"]
                    )
                    last_nominatim_hit = time.time()
                    if nlat is not None and nlng is not None:
                        updates["latitude"] = nlat
                        updates["longitude"] = nlng
                        stats["nominatim_geo"] += 1
                        if verbose:
                            print(
                                f"[geo-backfill] {row['id']}  nominatim geocoded "
                                f"{resolved_addr[:40]!r} -> {nlat:.5f},{nlng:.5f}"
                            )
            if not updates:
                stats["fail"] += 1
                if verbose:
                    print(f"[geo-backfill] {row['id']}  no new data (html empty and no address to geocode)")
                continue
            set_clause = ", ".join(f"{k}=?" for k in updates)
            params = list(updates.values()) + [row["id"]]
            conn.execute(f"UPDATE listings SET {set_clause} WHERE id=?", params)
            conn.commit()
            if verbose:
                lat = updates.get("latitude")
                suffix = f"  lat={lat:.5f}" if lat is not None else ""
                print(f"[geo-backfill] {row['id']}  updated: {list(updates)}{suffix}")
    finally:
        adapter.close()
        conn.close()
    if verbose:
        print(f"[geo-backfill] done  stats={stats}")
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between same-host requests")
    parser.add_argument("--no-nominatim", action="store_true", help="skip address → geocode fallback")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    run(
        args.db,
        args.limit,
        args.delay,
        verbose=not args.quiet,
        use_nominatim=not args.no_nominatim,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
