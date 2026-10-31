"""Scrappy Favorites — Airbnb-style split-view decision cockpit.

Architecture:
  generate_and_save() writes two files:
    data/reports/listings_data.json   — data snapshot (written each run)
    data/reports/favorites.html       — shell HTML that polls GET /listings every 30s

  The HTML no longer embeds JSON — it fetches live data from feedback_server (:5055).
  Pipeline state (stars, stage) persists to DB via POST /pipeline_status.
"""

import json
import sqlite3
from datetime import date
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
import paths
REPORTS_DIR = paths.reports_dir()

# ── Neighbourhood coordinate lookup (CDMX) ──────────────────────────────────
_NEIGHBORHOOD_COORDS: dict[str, list[float]] = {
    "Polanco":              [19.4310, -99.1993],
    "Polanco II":           [19.4310, -99.1993],
    "Amp Polanco":          [19.4310, -99.1993],
    "Lomas de Chapultepec": [19.4180, -99.2180],
    "Condesa":              [19.4116, -99.1783],
    "Hipódromo":            [19.4120, -99.1750],
    "Hipodromo":            [19.4120, -99.1750],
    "Roma Norte":           [19.4169, -99.1611],
    "Roma Sur":             [19.4088, -99.1611],
    "Juárez":               [19.4270, -99.1665],
    "Juarez":               [19.4270, -99.1665],
    "Cuauhtémoc":           [19.4350, -99.1500],
    "Cuauhtemoc":           [19.4350, -99.1500],
    "Amp Granada":          [19.4430, -99.1900],
    "Granada":              [19.4430, -99.1900],
    "Miguel Hidalgo":       [19.4220, -99.2100],
    "Bosques de las Lomas": [19.4100, -99.2600],
    "Naucalpan":            [19.4780, -99.2380],
    "Nápoles":              [19.4000, -99.1780],
    "Napoles":              [19.4000, -99.1780],
    "CDMX":                 [19.4326, -99.1332],
}
_DEFAULT_COORDS = [19.4326, -99.1332]


def _coords_for(neighborhood: str) -> list[float]:
    if not neighborhood:
        return _DEFAULT_COORDS
    if neighborhood in _NEIGHBORHOOD_COORDS:
        return _NEIGHBORHOOD_COORDS[neighborhood]
    for key, coords in _NEIGHBORHOOD_COORDS.items():
        if key.lower() in neighborhood.lower() or neighborhood.lower() in key.lower():
            return coords
    return _DEFAULT_COORDS


def _jittered_coords(listing_id: str, neighborhood: str) -> list[float]:
    """Fallback: neighborhood centroid + deterministic jitter seeded by id.

    Only used when the listing has no real lat/lng. Jitter (±~350 m) stops
    same-neighborhood rows from stacking on a single pin. This is clearly
    labeled as approximate in the UI; prefer ``_resolved_coords``.
    """
    import hashlib

    base_lat, base_lng = _coords_for(neighborhood)
    digest = hashlib.md5(listing_id.encode()).digest()
    lat_offset = ((digest[0] + (digest[1] << 8)) / 65535 - 0.5) * 0.006
    lng_offset = ((digest[2] + (digest[3] << 8)) / 65535 - 0.5) * 0.006
    return [base_lat + lat_offset, base_lng + lng_offset]


def _resolved_coords(
    listing_id: str,
    neighborhood: str,
    latitude: float | None,
    longitude: float | None,
) -> tuple[list[float], str]:
    """Return ([lat, lng], precision) where precision is 'exact' or 'approx'.

    Real per-listing coords (from scraper JSON-LD or Nominatim backfill) win.
    Only fall back to neighborhood centroid + stable jitter when the DB has
    no coords at all. Front-end uses ``precision`` to pick a pin colour and
    decide whether to tease the "approximate" banner.
    """
    if latitude is not None and longitude is not None:
        return [float(latitude), float(longitude)], "exact"
    return _jittered_coords(listing_id, neighborhood or ""), "approx"


def _safe_id(raw_id: str) -> str:
    return raw_id.replace(":", "_").replace(".", "_").replace("-", "_")


def _neighborhood_coords_js() -> str:
    """Generate JS object literal from _NEIGHBORHOOD_COORDS for embedding in HTML."""
    parts = []
    for name, coords in _NEIGHBORHOOD_COORDS.items():
        safe_name = name.replace('"', '\\"')
        parts.append(f'  "{safe_name}": [{coords[0]}, {coords[1]}]')
    return "{\n" + ",\n".join(parts) + "\n}"


def _fetch_rated(conn: sqlite3.Connection, signal: str) -> list[dict]:
    rows = conn.execute("""
        SELECT l.*, f.reason, f.created_at AS feedback_at
        FROM listings l
        JOIN feedback f ON f.listing_id = l.id
        WHERE f.signal = ?
          AND f.id = (
              SELECT MAX(id) FROM feedback f2
              WHERE f2.listing_id = f.listing_id
          )
        ORDER BY f.created_at DESC
    """, (signal,)).fetchall()
    return [dict(r) for r in rows]


def _fetch_inbox(conn: sqlite3.Connection) -> list[dict]:
    """Listings present in DB but with no feedback row yet."""
    rows = conn.execute("""
        SELECT l.* FROM listings l
        LEFT JOIN feedback f ON f.listing_id = l.id
        WHERE f.id IS NULL
        ORDER BY l.first_seen DESC
        LIMIT 200
    """).fetchall()
    return [dict(r) for r in rows]


def _build_data_dict(approved: list[dict], rejected: list[dict], inbox: list[dict] | None = None) -> dict:
    def entry(l: dict, signal: str) -> dict:
        photos = json.loads(l.get("photos_json") or "[]")
        (lat, lng), precision = _resolved_coords(
            l["id"],
            l.get("neighborhood", "") or "",
            l.get("latitude"),
            l.get("longitude"),
        )
        nth = json.loads(l.get("nice_to_haves") or "{}")
        return {
            "id":          l["id"],
            "safe_id":     _safe_id(l["id"]),
            "source":      l.get("source", ""),
            "neighborhood": l.get("neighborhood", "") or "",
            "price":       l.get("price", 0) or 0,
            "bedrooms":    l.get("bedrooms") or 0,
            "bathrooms":   l.get("bathrooms") or 0,
            "sqm":         l.get("sqm"),
            "value_per_sqm": (
                round(l.get("price", 0) / l["sqm"])
                if l.get("sqm") and l["sqm"] > 0
                else None
            ),
            "floor":       l.get("floor"),
            "url":         l.get("url", "#"),
            "photos":      photos,
            "vision_score": l.get("vision_score"),
            "nice_to_haves": nth,
            "reason":      (l.get("reason") or "").strip(),
            "feedback_at": (l.get("feedback_at") or "")[:10],
            "lat":         lat,
            "lng":         lng,
            "precision":   precision,
            "title":       (l.get("title") or "").strip(),
            "address":     (l.get("address") or "").strip(),
            "signal":      signal,
            "alerted":     l.get("alerted", 0),
        }
    return {
        "generated": date.today().isoformat(),
        "approved":  [entry(l, "up")   for l in approved],
        "rejected":  [entry(l, "down") for l in rejected],
        "inbox":     [entry(l, "new")  for l in (inbox or [])],
    }


# ── HTML template (plain string — no f-string, so JS braces need no escaping) ──
# Placeholders replaced by _html_template():
#   __NEIGHBORHOOD_COORDS__  → JS object literal of neighborhood → [lat, lng]

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scrappy · Listings Review</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.css"/>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#fff;--surface:#f7f7f7;--border:#dddddd;
  --text:#222;--muted:#717171;--faint:#bbb;
  --accent:#e94560;--green:#008a05;
  --hdr:64px;--r:12px;
}
html,body{height:100%;overflow:hidden}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--text);display:flex;flex-direction:column;-webkit-text-size-adjust:100%}

/* Header */
.header{height:var(--hdr);background:#fff;border-bottom:1px solid var(--border);padding:0 28px;display:flex;align-items:center;gap:24px;flex-shrink:0;position:sticky;top:0;z-index:200;box-shadow:0 1px 4px rgba(0,0,0,.06)}
.logo{font-size:1.1em;font-weight:800;color:var(--text)}
.logo span{color:var(--accent)}
.subtitle{font-size:.75em;color:var(--muted);margin-top:1px}
.tabs{display:flex;align-items:stretch;margin-left:12px}
.tab-btn{padding:0 20px;height:var(--hdr);background:none;border:none;border-bottom:2px solid transparent;color:var(--muted);font-size:.88em;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:8px;transition:color .15s;white-space:nowrap;min-height:44px}
.tab-btn:hover{color:var(--text)}
.tab-btn.active{color:var(--text);border-bottom-color:var(--text)}
.tab-count{background:var(--surface);color:var(--muted);padding:2px 8px;border-radius:20px;font-size:.75em;font-weight:700}
.tab-btn.active .tab-count{background:var(--text);color:#fff}

/* Layout */
.tab-panel{display:none;flex:1;min-height:0}
.tab-panel.active{display:flex}
.cards-col{width:55%;flex-shrink:0;overflow-y:auto;padding:20px 18px 80px;border-right:1px solid var(--border)}
.map-col{flex:1;position:relative;min-height:0}
.map-col > div{position:absolute;inset:0}

/* Taste panel */
.taste-panel{flex:1;overflow-y:auto;padding:28px 32px}
.taste-panel h3{font-size:1em;font-weight:700;margin-bottom:16px;color:var(--text)}
.taste-grid{display:flex;gap:32px;flex-wrap:wrap;margin-bottom:16px}
.taste-col h4{font-size:.82em;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px}
.taste-col ul{list-style:none;display:flex;flex-direction:column;gap:5px}
.taste-col li{font-size:.84em;color:var(--text)}
.taste-col li b{color:var(--accent)}
.taste-col li b.pos{color:var(--green)}
.taste-note{font-size:.76em;color:var(--muted);margin-top:8px}
.taste-empty{color:var(--muted);font-size:.88em;padding:20px 0}

/* Grid */
.cards-list{display:grid;grid-template-columns:repeat(3,1fr);gap:20px 12px}

/* Card */
.listing-card{cursor:pointer;position:relative;border-radius:var(--r);min-width:0;overflow:hidden}

/* Photo */
.card-photo{position:relative;border-radius:var(--r);overflow:hidden;aspect-ratio:1/.85;background:#efefef;transition:box-shadow .2s}
.listing-card:hover .card-photo{box-shadow:0 2px 16px rgba(0,0,0,.15)}
.carousel-track{display:flex;height:100%;transition:transform .3s ease}
.carousel-track img{min-width:100%;flex-shrink:0;height:100%;object-fit:cover;display:block}
.no-photo{height:100%;display:flex;align-items:center;justify-content:center;color:#bbb;font-size:2.2em;background:#f5f5f5}
.carousel-btn{position:absolute;top:50%;transform:translateY(-50%);background:rgba(255,255,255,.92);border:none;border-radius:50%;width:30px;height:30px;display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:1em;box-shadow:0 1px 6px rgba(0,0,0,.18);z-index:5;opacity:0;transition:opacity .15s}
.card-photo:hover .carousel-btn{opacity:1}
.carousel-btn.prev{left:6px}.carousel-btn.next{right:6px}
.carousel-dots{position:absolute;bottom:8px;left:50%;transform:translateX(-50%);display:flex;gap:4px;z-index:5}
.dot{width:5px;height:5px;border-radius:50%;background:rgba(255,255,255,.5);transition:background .15s}
.dot.active{background:#fff}
.heart-btn{position:absolute;top:8px;right:8px;font-size:1.2em;z-index:5;filter:drop-shadow(0 1px 2px rgba(0,0,0,.3));min-width:28px;min-height:28px;display:flex;align-items:center;justify-content:center}
.heart-filled{color:var(--accent)}.heart-empty{color:rgba(255,255,255,.85)}
.drag-handle{position:absolute;top:6px;left:6px;z-index:5;color:rgba(255,255,255,.7);font-size:.95em;cursor:grab;text-shadow:0 1px 3px rgba(0,0,0,.4)}
.src-badge{position:absolute;bottom:8px;left:8px;background:rgba(255,255,255,.92);color:#333;padding:2px 7px;border-radius:5px;font-size:.65em;font-weight:700;letter-spacing:.4px;text-transform:uppercase;z-index:5}
.tg-badge{display:inline-block;background:#0088cc;color:#fff;font-size:.68em;font-weight:700;padding:2px 7px;border-radius:10px;margin-bottom:4px}

/* Info */
.card-info{padding:8px 2px 0}
.card-row1{display:flex;justify-content:space-between;align-items:baseline;gap:6px;margin-bottom:1px}
.card-title{font-size:.85em;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;color:inherit;text-decoration:none}
.card-title:hover{color:var(--accent);text-decoration:underline}
.card-address{font-size:.73em;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:3px}
.cb-id{background:var(--bg2);border:1px solid rgba(255,255,255,.08);color:var(--fg);padding:4px 8px;border-radius:4px;font-family:monospace;font-size:.82em;width:180px}
.cb-id:focus{outline:none;border-color:var(--accent)}
.card-vstar{font-size:.8em;font-weight:500;white-space:nowrap;flex-shrink:0}
.card-desc{font-size:.78em;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:2px}
.card-price{font-size:.84em;font-weight:700}
.value-badge{margin-left:8px;font-size:.72em;color:var(--muted);font-weight:600;background:var(--surface);padding:2px 6px;border-radius:8px;border:1px solid var(--border)}

/* Stars */
.star-block{margin:5px 0 3px}
.star-row{display:flex;align-items:center;gap:3px;font-size:.72em;color:var(--muted);margin-bottom:2px;line-height:1}
.star-lbl{width:50px;flex-shrink:0}
.sv{color:#ddd;font-size:1em}.sv.lit{color:#b8860b}.sv.hl{color:#b8860b;opacity:.5}
.sp{color:#ddd;cursor:pointer;font-size:1em;transition:color .1s}.sp.lit{color:var(--text)}
.sa{color:#ddd;font-size:1em}.sa.lit{color:#aaa}
.star-n{margin-left:2px}

/* Reason */
.reason-note{font-size:.72em;font-style:italic;margin-top:4px;padding:3px 7px;border-left:2px solid;line-height:1.4}
.reason-approved{border-color:var(--green);color:var(--green)}
.reason-rejected{border-color:var(--accent);color:var(--accent)}

/* Pipeline (approved only) */
.pipeline{display:flex;flex-wrap:wrap;gap:4px;margin-top:8px}
.pl-btn{padding:3px 8px;border:1px solid #ddd;border-radius:20px;background:#fff;color:var(--muted);font-size:.68em;font-weight:500;cursor:pointer;transition:all .15s;white-space:nowrap}
.pl-btn.active{background:var(--text);color:#fff;border-color:var(--text)}
.pl-btn:hover:not(.active){border-color:#999;color:var(--text)}

/* Notes */
.note-section{margin-top:6px}
.add-note-link{font-size:.78em;color:var(--text);text-decoration:underline;text-underline-offset:2px;cursor:pointer;background:none;border:none;padding:0;font-family:inherit}
.note-area{display:none;margin-top:4px}
.note-textarea{width:100%;border:1px solid #ddd;border-radius:8px;padding:6px 8px;font-size:.78em;font-family:inherit;resize:vertical;outline:none;min-height:44px}
.note-textarea:focus{border-color:#888}
.note-btns{display:flex;gap:6px;margin-top:4px}
.note-save{padding:3px 10px;background:var(--text);color:#fff;border:none;border-radius:6px;font-size:.74em;cursor:pointer}
.note-cancel{padding:3px 10px;background:none;border:1px solid #ddd;border-radius:6px;font-size:.74em;cursor:pointer;color:var(--muted)}
.note-display{font-size:.75em;color:var(--muted);font-style:italic;margin-top:3px;line-height:1.5}
.card-footer{font-size:.68em;color:var(--faint);margin-top:5px;display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.card-footer a{color:var(--muted);text-decoration:underline}
.id-badge{font-family:'SF Mono','Consolas',monospace;font-size:.92em;background:rgba(255,255,255,.06);color:var(--faint);padding:1px 6px;border-radius:4px;cursor:pointer;user-select:all;letter-spacing:0}
.id-badge:hover{color:var(--accent);background:rgba(233,69,96,.08)}
.map-approx-banner{position:absolute;top:10px;left:10px;right:10px;background:rgba(15,52,96,.88);color:#fff;font-size:.72em;padding:6px 10px;border-radius:6px;z-index:500;pointer-events:none;text-align:center}

/* Glow */
.card-glow .card-photo{outline:2px solid var(--accent)}
.approved.card-glow .card-photo{outline-color:var(--green)}

/* Empty */
.empty{grid-column:1/-1;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:60px;color:var(--muted);gap:12px;text-align:center}
.empty-icon{font-size:3em}.empty h3{font-weight:400}

/* Loading */
#loading{position:fixed;inset:0;background:#fff;display:flex;align-items:center;justify-content:center;z-index:1000;font-size:.9em;color:var(--muted)}

/* Drag */
.dragging{opacity:.55}.drag-over .card-photo{outline:2px dashed var(--accent)}

/* Leaflet */
.price-tip{background:rgba(0,0,0,.78)!important;border:none!important;color:#fff!important;font-size:.72em;font-weight:600;padding:3px 6px!important;border-radius:4px!important;white-space:nowrap}
.price-tip::before{display:none!important}

/* Scrollbar */
.cards-col::-webkit-scrollbar{width:5px}
.cards-col::-webkit-scrollbar-thumb{background:#ddd;border-radius:3px}

/* Control bar (Cadence · Run · Area · Source · Min-score) */
.control-bar{background:var(--surface);border-bottom:1px solid var(--border);padding:10px 28px;display:flex;flex-direction:column;gap:8px;flex-shrink:0;position:sticky;top:var(--hdr);z-index:150}
.cb-toprow{display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.cb-filterblock{display:flex;gap:14px;align-items:center;flex-wrap:wrap;border-top:1px solid var(--border);padding-top:8px}
.cb-expand-btn{padding:5px 11px;border:1px solid var(--border);border-radius:14px;background:var(--surface);color:var(--muted);cursor:pointer;font-size:.78em;font-weight:600;margin-left:auto;min-height:32px;transition:all .12s;font-family:inherit}
.cb-expand-btn:hover{border-color:#999;color:var(--text)}
.cb-group{display:flex;gap:5px;align-items:center;flex-wrap:wrap}
.cb-lbl{font-size:.68em;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;font-weight:700;margin-right:2px}
.cb-pill{padding:5px 11px;border:1px solid var(--border);border-radius:14px;background:#fff;color:var(--text);cursor:pointer;font-size:.78em;font-weight:500;min-height:32px;transition:all .12s;font-family:inherit}
.cb-pill:hover{border-color:#999}
.cb-pill.active{background:var(--text);border-color:var(--text);color:#fff}
.cb-pill.run-btn{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:700}
.cb-pill.run-btn:hover{background:#d23950;border-color:#d23950}
.cb-pill.pill-empty{opacity:.45;font-style:italic}
.cb-num{background:#fff;border:1px solid var(--border);border-radius:8px;padding:5px 8px;font-size:.78em;width:58px;font-family:inherit;color:var(--text);min-height:32px}
.cb-status{font-size:.72em;color:var(--muted);margin-left:auto}
/* Search criteria panel (mutates next scrape) — distinct from in-memory display filters */
.criteria-panel{background:#fffaf0;border-bottom:1px solid var(--border);border-top:1px dashed #e0d4b8;padding:0 28px;flex-shrink:0;position:sticky;top:calc(var(--hdr) + 50px);z-index:140;font-size:.8em}
.criteria-toggle{display:flex;align-items:center;gap:8px;padding:8px 0;cursor:pointer;background:none;border:none;font-family:inherit;font-size:1em;color:var(--text);font-weight:600;width:100%;text-align:left}
.criteria-toggle:hover{color:var(--accent)}
.criteria-toggle .cp-caret{transition:transform .15s;display:inline-block}
.criteria-panel.open .cp-caret{transform:rotate(90deg)}
.criteria-tag{font-size:.68em;background:#fef3c7;color:#92400e;padding:2px 7px;border-radius:10px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;margin-left:4px}
.criteria-help{font-size:.72em;color:var(--muted);font-weight:400;font-style:italic;margin-left:6px}
.criteria-body{display:none;padding:8px 0 14px;flex-wrap:wrap;gap:14px 22px;align-items:flex-end}
.criteria-panel.open .criteria-body{display:flex}
.cp-field{display:flex;flex-direction:column;gap:3px;min-width:0}
.cp-field label{font-size:.68em;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;font-weight:700}
.cp-field input[type="number"]{background:#fff;border:1px solid var(--border);border-radius:6px;padding:6px 8px;font-size:.92em;font-family:inherit;color:var(--text);min-height:32px;width:88px}
.cp-field input[type="text"]{background:#fff;border:1px solid var(--border);border-radius:6px;padding:6px 8px;font-size:.92em;font-family:inherit;color:var(--text);min-height:32px;min-width:240px}
.cp-field input:focus{outline:none;border-color:var(--accent)}
.cp-checkbox{display:flex;align-items:center;gap:6px;font-size:.92em;cursor:pointer;padding-bottom:4px}
.cp-checkbox input{cursor:pointer;width:16px;height:16px;accent-color:var(--accent)}
.cp-apply{padding:7px 14px;background:var(--accent);color:#fff;border:none;border-radius:6px;font-size:.86em;font-weight:700;cursor:pointer;font-family:inherit;min-height:34px}
.cp-apply:hover{background:#d23950}
.cp-status-good{color:var(--green)!important}

/* Inbox tab thumb buttons */
.inbox-actions{display:flex;gap:8px;margin-top:8px}
.inbox-thumb{flex:1;padding:7px 10px;border:1px solid var(--border);border-radius:8px;background:#fff;cursor:pointer;font-size:.85em;font-weight:600;font-family:inherit;transition:all .12s;min-height:36px}
.inbox-thumb.up{color:var(--green);border-color:#bfe5c0}
.inbox-thumb.up:hover{background:#eaf7ea;border-color:var(--green)}
.inbox-thumb.down{color:var(--accent);border-color:#f5c8d0}
.inbox-thumb.down:hover{background:#fdecef;border-color:var(--accent)}
.inbox-thumb:disabled{opacity:.5;cursor:wait}

.stats-strip{display:flex;gap:18px;padding:8px 28px;background:#fff;border-bottom:1px solid var(--border);font-size:.78em;flex-shrink:0;overflow-x:auto}
.stat-item{white-space:nowrap}
.stat-item b{color:var(--text);font-weight:700}
.stat-item span{color:var(--muted)}

/* Draw area button */
.draw-area-btn{padding:5px 11px;border:1px solid var(--border);border-radius:14px;background:#fff;color:var(--text);cursor:pointer;font-size:.78em;font-weight:500;min-height:32px;transition:all .12s;font-family:inherit}
.draw-area-btn:hover{border-color:#999}
.draw-area-btn.active{background:#4a90d9;border-color:#4a90d9;color:#fff}

/* Responsive */
@media(max-width:1100px){.cards-list{grid-template-columns:repeat(2,1fr)}}
@media(max-width:700px){.cards-list{grid-template-columns:1fr}}
@media(max-width:900px){
  html,body{overflow:auto}
  .header{height:auto;min-height:var(--hdr);padding:12px 14px;gap:12px;align-items:flex-start;flex-wrap:wrap}
  .tabs{margin-left:0;width:100%;overflow-x:auto;padding-bottom:2px}
  .tab-btn{height:auto;flex:0 0 auto;padding:10px 14px}
  .tab-panel.active{flex-direction:column}
  .cards-col{width:100%;border-right:none;border-bottom:1px solid var(--border);max-height:58vh;padding:14px 12px calc(28px + env(safe-area-inset-bottom))}
  .map-col{flex:none;height:38vh;min-height:240px}
  .card-info{padding-top:10px}
  .card-row1{align-items:flex-start;gap:10px}
  .card-title,.card-desc{white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
  .card-footer{flex-wrap:wrap;gap:8px}
  .carousel-btn{opacity:1;width:34px;height:34px}
  .note-btns,.pipeline{gap:8px}
  .pl-btn,.note-save,.note-cancel{min-height:38px}
}
@media(prefers-reduced-motion:reduce){
  .listing-card,.carousel-track,.tab-panel{transition:none!important;animation:none!important}
}
</style>
</head>
<body>

<div id="loading">Loading listings…</div>

<div class="header">
  <div>
    <div class="logo">Scrappy · Listings Review</div>
    <div class="subtitle" id="subtitle"></div>
  </div>
  <nav class="tabs" role="tablist">
    <button class="tab-btn active" id="tab-inbox" role="tab" onclick="switchTab('inbox')">
      📥 Inbox <span class="tab-count" id="cnt-inbox">…</span>
    </button>
    <button class="tab-btn" id="tab-approved" role="tab" onclick="switchTab('approved')">
      ✓ Approved <span class="tab-count" id="cnt-approved">…</span>
    </button>
    <button class="tab-btn" id="tab-rejected" role="tab" onclick="switchTab('rejected')">
      ✗ Rejected <span class="tab-count" id="cnt-rejected">…</span>
    </button>
    <button class="tab-btn" id="tab-taste" role="tab" onclick="switchTab('taste')">
      🧠 Taste
    </button>
  </nav>
  <button class="cb-pill" onclick="openSettings()">Settings</button>
  <a href="/kanban" style="margin-left:12px;font-size:12px;color:#888;text-decoration:none;">Kanban</a>
</div>

<div class="control-bar" aria-label="Scrape controls and filters">
  <!-- Always-visible top row: run controls + score/price filters + ID jump -->
  <div class="cb-toprow">
    <div class="cb-group">
      <button class="cb-pill run-btn" onclick="triggerRun()" title="Trigger a new scrape">▶ Run Now</button>
    </div>
    <div class="cb-group">
      <span class="cb-lbl">Min ★:</span>
      <input type="number" class="cb-num" id="min-score" min="0" max="10" step="0.5" value="0" oninput="applyFilters()">
      <span class="cb-lbl" style="margin-left:6px">Max $:</span>
      <input type="number" class="cb-num" id="max-price" step="500" placeholder="—" oninput="applyFilters()">
    </div>
    <div class="cb-group" id="id-jump-group">
      <span class="cb-lbl">Find ID:</span>
      <input type="text" class="cb-id" id="id-jump" placeholder="e.g. ml:71cc7e669b or 149241453"
        onkeydown="if(event.key==='Enter')jumpToListing(this.value);"
        title="Paste a listing id to jump to its card — the same id shown on every card footer.">
      <button class="cb-pill" onclick="jumpToListing(document.getElementById('id-jump').value)">Go</button>
    </div>
    <button class="cb-expand-btn" id="cb-expand-btn" onclick="toggleFilterPanel()" title="Show/hide filters">⚙ Filters</button>
  </div>

  <!-- Collapsible filter panel: cadence, area, source, geo -->
  <div class="cb-filterblock" id="cb-filterblock" style="display:none">
    <div class="cb-group">
      <span class="cb-lbl">Cadence:</span>
      <button class="cb-pill active" data-cadence="1" onclick="setCadence(1)">Daily</button>
      <button class="cb-pill" data-cadence="3" onclick="setCadence(3)">3-day</button>
      <button class="cb-pill" data-cadence="7" onclick="setCadence(7)">Weekly</button>
    </div>
    <div class="cb-group" id="area-pills">
      <span class="cb-lbl">Area:</span>
      <button class="cb-pill active" data-area="all" onclick="filterArea(this,'all')">All</button>
      <!-- dynamic area pills injected on render -->
    </div>
    <div class="cb-group" id="source-pills">
      <span class="cb-lbl">Source:</span>
      <button class="cb-pill active" data-source="all" onclick="filterSource(this,'all')">All</button>
      <!-- dynamic source pills injected on render -->
    </div>
    <div class="cb-group">
      <button class="draw-area-btn" id="draw-area-btn" onclick="toggleDrawMode()" title="Draw search polygon on map">📐 Draw Area</button>
      <button class="cb-pill" onclick="clearGeoFilter()" title="Clear saved polygon">✕ Clear Area</button>
    </div>
  </div>

  <div class="cb-status" id="cb-status" aria-live="polite"></div>
  <div id="run-progress" style="display:none;background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:8px 12px;font-size:.76em;color:var(--muted);margin-top:6px;font-family:monospace;white-space:pre-wrap;max-height:80px;overflow-y:auto"></div>
</div>

<dialog id="setup-dialog" aria-label="Setup" style="margin:auto;padding:24px;border:0;border-radius:12px;max-width:520px;width:90%;color:#222">
  <h2>Settings</h2><p>Set search criteria using the Search criteria panel below.</p>
  <h3>Choose AI</h3>
  <label>Provider <select id="ai-provider" onchange="document.getElementById('ai-model').value=''"><option value="none">No AI</option><option value="openrouter">OpenRouter</option><option value="gemini">Gemini</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option><option value="ollama">Ollama Cloud</option></select></label>
  <p><label>Model <input id="ai-model" placeholder="Default model"></label></p>
  <p><label>API key <input id="ai-key" type="password" autocomplete="off"></label></p>
  <button onclick="saveAI()">Save AI</button> <a href="/auth/openrouter/start">OpenRouter sign in</a> <button onclick="removeAI()">Disconnect</button>
  <h3>Firecrawl (optional)</h3><p>Unlocks sources that require rendered pages. <a href="/FIRECRAWL.md" target="_blank" rel="noopener">Learn more</a></p>
  <input id="firecrawl-key" type="password" autocomplete="off" aria-label="Firecrawl key"> <button onclick="saveFirecrawl()">Save</button> <button onclick="removeFirecrawl()">Remove</button>
  <p id="setup-status" role="status"></p>
  <p><a href="/api/export" download="scrappy-backup.json">Export backup</a> <label>Import backup <input id="backup-file" type="file" accept="application/json" onchange="importBackup(this)"></label></p>
  <button onclick="closeSettings()">Done</button> <button onclick="quitApp()">Quit Scrappy</button>
</dialog>
<div id="run-warnings" role="status"></div>
<section class="criteria-panel" id="criteria-panel" aria-label="Search criteria for next scrape">
  <button type="button" class="criteria-toggle" onclick="toggleCriteriaPanel()" aria-expanded="false" aria-controls="criteria-body">
    <span class="cp-caret">▶</span>
    <span>🔍 Search criteria</span>
    <span class="criteria-tag">affects next scrape</span>
    <span class="criteria-help">— what the scraper looks for. Display filters above only filter this view.</span>
  </button>
  <div class="criteria-body" id="criteria-body">
    <div class="cp-field" style="flex:1;min-width:240px">
      <label for="cp-neighborhoods">Neighborhoods (comma-separated)</label>
      <input type="text" id="cp-neighborhoods" placeholder="Polanco, Condesa, Roma Norte" oninput="onCriteriaChange()">
    </div>
    <div class="cp-field">
      <label for="cp-price-min">Price min (MXN)</label>
      <input type="number" id="cp-price-min" min="0" step="500" oninput="onCriteriaChange()">
    </div>
    <div class="cp-field">
      <label for="cp-price-max">Price max (MXN)</label>
      <input type="number" id="cp-price-max" min="0" step="500" oninput="onCriteriaChange()">
    </div>
    <div class="cp-field">
      <label for="cp-bedrooms">Bedrooms (min)</label>
      <input type="number" id="cp-bedrooms" min="0" max="10" oninput="onCriteriaChange()">
    </div>
    <div class="cp-field">
      <label for="cp-bathrooms">Bathrooms (min)</label>
      <input type="number" id="cp-bathrooms" min="0" max="10" oninput="onCriteriaChange()">
    </div>
    <div class="cp-field">
      <label for="cp-min-floor">Min floor</label>
      <input type="number" id="cp-min-floor" min="0" max="60" oninput="onCriteriaChange()">
    </div>
    <div class="cp-field">
      <label for="cp-min-sqm">Min m²</label>
      <input type="number" id="cp-min-sqm" min="0" step="5" oninput="onCriteriaChange()">
    </div>
    <div class="cp-field">
      <label for="cp-min-vision">Min vision ★ (0-10)</label>
      <input type="number" id="cp-min-vision" min="0" max="10" step="0.5" oninput="onCriteriaChange()">
    </div>
    <label class="cp-checkbox">
      <input type="checkbox" id="cp-furnished" onchange="onCriteriaChange()">
      <span>Furnished</span>
    </label>
    <label class="cp-checkbox">
      <input type="checkbox" id="cp-require-photos" onchange="onCriteriaChange()">
      <span>Require photos</span>
    </label>
    <button type="button" class="cp-apply" onclick="applyAndRunCriteria()">Apply &amp; Run Now</button>
  </div>
</section>

<div class="stats-strip" id="stats-strip"></div>

<div id="panel-inbox" class="tab-panel active" role="tabpanel">
  <div class="cards-col"><div class="cards-list" id="cards-inbox"></div></div>
  <div class="map-col"><div id="map-inbox"></div></div>
</div>
<div id="panel-approved" class="tab-panel" role="tabpanel">
  <div class="cards-col"><div class="cards-list" id="cards-approved"></div></div>
  <div class="map-col"><div id="map-approved"></div></div>
</div>
<div id="panel-rejected" class="tab-panel" role="tabpanel">
  <div class="cards-col"><div class="cards-list" id="cards-rejected"></div></div>
  <div class="map-col"><div id="map-rejected"></div></div>
</div>
<div id="panel-taste" class="tab-panel" role="tabpanel">
  <div class="taste-panel">
    <h3>🧠 Your Taste Profile</h3>
    <div id="taste-content"><p class="taste-empty">Loading…</p></div>
  </div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.js"></script>
<script>
async function openSettings(){
  let ai = {provider:'none', model:'', connected:false};
  try { let r = await fetch('/api/ai'); if (r.ok) ai = await r.json(); } catch(e) {}
  document.getElementById('ai-provider').value = ai.provider;
  document.getElementById('ai-model').value = ai.model || '';
  let firecrawl = {connected:false};
  try { let r = await fetch('/api/firecrawl'); if (r.ok) firecrawl = await r.json(); } catch(e) {}
  document.getElementById('setup-status').textContent = 'AI: ' + (ai.connected ? 'connected' : 'not connected') + ' · Firecrawl: ' + (firecrawl.connected ? 'connected' : 'not connected');
  document.getElementById('setup-dialog').showModal();
}
function closeSettings(){ fetch('/api/setup',{method:'POST'}); document.getElementById('setup-dialog').close(); }
async function saveAI(){
  let key = document.getElementById('ai-key');
  let body = {provider:document.getElementById('ai-provider').value,model:document.getElementById('ai-model').value};
  if (key.value.trim()) body.key = key.value.trim();
  let res = await fetch('/api/ai',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  key.value=''; document.getElementById('setup-status').textContent = res.ok ? 'AI settings saved.' : 'Could not save AI settings.';
}
async function removeAI(){ let r=await fetch('/api/ai',{method:'DELETE'}); document.getElementById('setup-status').textContent=r.ok?'AI disconnected.':'Could not disconnect AI.'; }
async function saveFirecrawl(){ let key=document.getElementById('firecrawl-key'); let r=await fetch('/api/firecrawl',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:key.value})}); key.value=''; document.getElementById('setup-status').textContent=r.ok?'Firecrawl saved.':'Could not save Firecrawl.'; }
async function removeFirecrawl(){ let r=await fetch('/api/firecrawl',{method:'DELETE'}); document.getElementById('setup-status').textContent=r.ok?'Firecrawl removed.':'Could not remove Firecrawl.'; }
async function importBackup(input){ try { let data=JSON.parse(await input.files[0].text()); let r=await fetch('/api/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}); document.getElementById('setup-status').textContent=r.ok?'Backup imported.':'Import failed.'; } catch(e){document.getElementById('setup-status').textContent='Invalid backup.';} input.value=''; }
async function quitApp(){ let r=await fetch('/api/quit',{method:'POST'}); if(r.ok) document.body.textContent='Scrappy closed'; }
fetch('/run_meta').then(r=>{if(!r.ok) throw new Error('HTTP ' + r.status); return r.json();}).then(m=>{let alerts=[]; if(m.ai_status==='degraded') alerts.push('AI unavailable: using metadata scores.'); if(m.ai_status==='reconnect') alerts.push('Reconnect your AI provider in Settings.'); if(Object.values(m.sources||{}).some(s=>s.status==='needs_firecrawl')) alerts.push('Some sources need Firecrawl.'); document.getElementById('run-warnings').textContent=alerts.join(' ');}).catch(()=>{});
window.addEventListener('load',()=>fetch('/api/setup').then(r=>r.json()).then(s=>{if(!s.complete) openSettings();}));
// ── Neighbourhood coords (injected from Python) ───────────────────────────
var NEIGHBORHOOD_COORDS = __NEIGHBORHOOD_COORDS__;
var DEFAULT_COORDS = [19.4326, -99.1332];

function coordsFor(nbhd) {
  if (!nbhd) return DEFAULT_COORDS;
  if (NEIGHBORHOOD_COORDS[nbhd]) return NEIGHBORHOOD_COORDS[nbhd];
  var keys = Object.keys(NEIGHBORHOOD_COORDS);
  for (var i = 0; i < keys.length; i++) {
    var k = keys[i];
    if (k.toLowerCase().indexOf(nbhd.toLowerCase()) >= 0 ||
        nbhd.toLowerCase().indexOf(k.toLowerCase()) >= 0)
      return NEIGHBORHOOD_COORDS[k];
  }
  return DEFAULT_COORDS;
}

function safeId(id) {
  return String(id).replace(/[-:.]/g, '_');
}

// ── State ─────────────────────────────────────────────────────────────────
var DATA = {approved: [], rejected: [], inbox: []};
var MAPS = {};
var MARKERS = {};
var CAROUSEL = {};
var ACTIVE_TAB = 'inbox';
var LISTING_ID_MAP = {};   // safe_id → real listing id
var DRAW_LAYERS = {};      // tabName → L.FeatureGroup
var DRAW_MODE = false;

var PIPELINE = [
  {key: 'liked',   label: '❤ Liked'},
  {key: 'called',  label: '📞 Called'},
  {key: 'visited', label: '🏠 Visited'},
  {key: 'waiting', label: '⏳ Waiting'}
];

// ── API adapter ───────────────────────────────────────────────────────────
// Transforms GET /listings response → renderAll format
function adaptApiResponse(apiData) {
  function adapt(listings, signal) {
    return (listings || []).map(function(l) {
      var sid = safeId(l.id);
      LISTING_ID_MAP[sid] = l.id;
      // Real coords win. Only fall back to neighborhood centroid (+ jitter
      // by id) when the DB genuinely has no lat/lng. Precision is carried
      // through so pins + banner can be honest about which is which.
      var hasReal = (l.latitude != null && l.longitude != null);
      var lat, lng, precision;
      if (hasReal) {
        lat = Number(l.latitude);
        lng = Number(l.longitude);
        precision = 'exact';
      } else {
        var c = coordsFor(l.neighborhood || '');
        // Deterministic jitter seeded by id so markers don't stack.
        var h = 0;
        for (var i = 0; i < sid.length; i++) h = (h * 31 + sid.charCodeAt(i)) | 0;
        var jLat = ((h & 0xFFFF) / 65535 - 0.5) * 0.006;
        var jLng = (((h >> 16) & 0xFFFF) / 65535 - 0.5) * 0.006;
        lat = c[0] + jLat;
        lng = c[1] + jLng;
        precision = 'approx';
      }
      return {
        id:           l.id,
        safe_id:      sid,
        source:       l.source || '',
        neighborhood: l.neighborhood || '',
        title:        (l.title || '').trim(),
        address:      (l.address || '').trim(),
        price:        l.price || 0,
        bedrooms:     l.bedrooms || 0,
        bathrooms:    l.bathrooms || 0,
        sqm:          l.sqm,
        value_per_sqm: l.value_per_sqm || null,
        floor:        l.floor,
        url:          l.url || '#',
        photos:       Array.isArray(l.photos_json) ? l.photos_json : [],
        vision_score: l.vision_score,
        nice_to_haves: l.nice_to_haves || {},
        reason:       '',
        feedback_at:  (l.last_seen || '').slice(0, 10),
        lat:          lat,
        lng:          lng,
        precision:    precision,
        signal:       signal,
        pipeline_status: l.pipeline_status || null,
        pipeline_note:   l.pipeline_note || '',
        pipeline_stars:  l.pipeline_stars || 0,
        note_body:       l.note_body || '',
        alerted: l.alerted ? true : false,
      };
    });
  }
  return {
    generated: new Date().toISOString().slice(0, 10),
    approved:  adapt(apiData.approved, 'up'),
    rejected:  adapt(apiData.rejected, 'down'),
    inbox:     adapt(apiData.inbox,    'new'),
  };
}

// ── Poll ──────────────────────────────────────────────────────────────────
// Recurring-bug guard: this page MUST be served by feedback_server.py (Flask,
// default :5055). Opening the file directly or via `python -m http.server`
// breaks every same-origin fetch because /listings, /pipeline_status, /taste,
// /criteria are Flask routes that don't exist on a static server — the static
// server silently returns an HTML directory index that JSON.parse chokes on.
// Detect that explicitly and surface a loud banner instead of a quiet retry.
function showWrongServerBanner(detail) {
  var el = document.getElementById('loading');
  if (!el) return;
  el.style.display = 'block';
  el.style.background = '#e94560';
  el.style.color = '#fff';
  el.style.padding = '16px';
  el.style.borderRadius = '8px';
  el.style.fontWeight = '600';
  el.innerHTML =
    '⚠️ Wrong server — this HTML is being served by a static file server.<br>' +
    'Open <a href="http://localhost:5055/favorites" style="color:#fff;text-decoration:underline">http://localhost:5055/favorites</a> instead.<br>' +
    '<small style="font-weight:400">(feedback_server.py must be running; <code>python run.py</code> auto-starts it.)</small>' +
    (detail ? '<br><small style="font-weight:400;opacity:0.85">detail: ' + detail + '</small>' : '');
}

function poll() {
  fetch('/listings')
    .then(function(r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      var ct = (r.headers.get('content-type') || '').toLowerCase();
      if (ct.indexOf('application/json') === -1) {
        throw new Error('wrong-server: content-type=' + ct);
      }
      return r.json();
    })
    .then(function(apiData) {
      document.getElementById('loading').style.display = 'none';
      DATA = adaptApiResponse(apiData);
      renderAll(DATA);
    })
    .catch(function(e) {
      console.warn('[poll] failed:', e);
      if (String(e.message || '').indexOf('wrong-server') === 0) {
        showWrongServerBanner(e.message);
      } else {
        document.getElementById('loading').style.display = 'none';
        setStatus('Auto-refresh failed — retrying in 30s', true);
      }
    });
}

window.addEventListener('DOMContentLoaded', function() {
  poll();
  setInterval(poll, 30000);
  loadCriteria();
  loadTaste();
});

window.addEventListener('resize', function() {
  var map = MAPS[ACTIVE_TAB];
  if (map) map.invalidateSize();
});

// ── Render ────────────────────────────────────────────────────────────────
function renderAll(data) {
  document.getElementById('subtitle').textContent =
    (data.generated || '') + ' · Your feedback history';
  document.getElementById('cnt-approved').textContent = data.approved.length;
  document.getElementById('cnt-rejected').textContent = data.rejected.length;
  document.getElementById('cnt-inbox').textContent    = (data.inbox || []).length;

  var aEl = document.getElementById('cards-approved');
  var rEl = document.getElementById('cards-rejected');
  var iEl = document.getElementById('cards-inbox');
  aEl.innerHTML = data.approved.length
    ? data.approved.map(function(l) { return cardHTML(l, 'approved'); }).join('')
    : emptyHTML('No approved listings', 'Approve listings from the Inbox with 👍.');
  rEl.innerHTML = data.rejected.length
    ? data.rejected.map(function(l) { return cardHTML(l, 'rejected'); }).join('')
    : emptyHTML('No rejected listings', '');
  iEl.innerHTML = (data.inbox || []).length
    ? data.inbox.map(function(l) { return cardHTML(l, 'inbox'); }).join('')
    : emptyHTML('Inbox empty', 'New scrapes will appear here.');

  var all = data.approved.concat(data.rejected).concat(data.inbox || []);
  all.forEach(function(l) {
    if (l.photos && l.photos.length > 1)
      CAROUSEL[l.safe_id] = {cur: 0, count: l.photos.length};
    loadStars(l.safe_id, l.vision_score, l.pipeline_stars);
    loadNote(l.safe_id);
  });
  data.approved.forEach(function(l) {
    loadPipeline(l.safe_id, l.pipeline_status);
  });

  initDrag('cards-approved');
  initDrag('cards-rejected');
  initDrag('cards-inbox');
  attachCardClicks(data.approved, 'approved');
  attachCardClicks(data.rejected, 'rejected');
  attachCardClicks(data.inbox || [], 'inbox');

  // Re-init maps only on first render (MAPS is empty)
  if (Object.keys(MAPS).length === 0) {
    var allData = data.approved.concat(data.rejected).concat(data.inbox || []);
    initMap('map-approved', 'up',   allData);
    initMap('map-rejected', 'down', allData);
    initMap('map-inbox',    'new',  allData);
    // Deep link is only meaningful after DATA is populated & maps exist.
    handleDeepLink();
  } else {
    // On repoll: update markers without rebuilding maps
    refreshMarkers(data);
  }

  injectFilterPills(all, CRITERIA_LAST.neighborhoods || []);
  renderStats();
  updateApproxBanners(data);
}

// Show the "approximate" banner only on tabs that actually contain approx
// pins. If every pin on a map is real (exact coords), the banner is
// misleading; hide it.
function updateApproxBanners(data) {
  var byTab = {approved: data.approved, rejected: data.rejected, inbox: data.inbox || []};
  Object.keys(byTab).forEach(function(tab) {
    var el = document.getElementById('map-' + tab);
    if (!el) return;
    var banner = el.querySelector('.map-approx-banner');
    if (!banner) return;
    var approxCount = byTab[tab].filter(function(l) { return l.precision === 'approx'; }).length;
    var total = byTab[tab].length;
    if (approxCount === 0) {
      banner.style.display = 'none';
    } else {
      banner.style.display = 'block';
      banner.textContent = approxCount + '/' + total
        + ' pins are approximate (no per-listing geo yet). Click “View listing ↗” for the real address.';
    }
  });
}

// ── Card HTML ─────────────────────────────────────────────────────────────
function cardHTML(l, kind) {
  var sid    = l.safe_id;
  var photos = l.photos || [];
  var heart  = kind === 'approved'
    ? '<span class="heart-btn heart-filled">❤</span>'
    : '<span class="heart-btn heart-empty">♡</span>';
  var drag = '<span class="drag-handle">⠿</span>';

  var photoBlock;
  if (photos.length) {
    var imgs = photos.map(function(p, i) {
      return '<img src="' + xh(p) + '" loading="lazy" alt="Photo ' + (i + 1) + '">';
    }).join('');
    var dotsHtml = photos.length > 1
      ? photos.slice(0, Math.min(8, photos.length)).map(function(_, i) {
          return '<div class="dot' + (i === 0 ? ' active' : '') + '" data-i="' + i + '"></div>';
        }).join('')
      : '';
    var nav = photos.length > 1
      ? '<button class="carousel-btn prev" onclick="event.stopPropagation();cGo(\\'' + sid + '\\',-1)">&#8249;</button>'
      + '<button class="carousel-btn next" onclick="event.stopPropagation();cGo(\\'' + sid + '\\',1)">&#8250;</button>'
      + '<div class="carousel-dots" id="dots-' + sid + '">' + dotsHtml + '</div>'
      : '';
    photoBlock = '<div class="card-photo">'
      + '<div class="carousel-track" id="tr-' + sid + '">' + imgs + '</div>'
      + nav + heart + drag
      + '<div class="src-badge">' + xh(l.source) + '</div>'
      + '</div>';
  } else {
    photoBlock = '<div class="card-photo"><div class="no-photo">🏗</div>' + heart + drag + '</div>';
  }

  var vh = l.vision_score != null ? Math.min(5, l.vision_score / 2) : null;
  // Title uses the scraped listing name when we have it; otherwise falls
  // back to a neighborhood label so the card is never empty. The title is a
  // link to the source listing, making it load-bearing for trace.
  var displayTitle = l.title && l.title.length
    ? l.title
    : (l.neighborhood ? ('Apt in ' + l.neighborhood) : 'Apartment');
  var row1 = '<div class="card-row1">'
    + '<a class="card-title" href="' + xh(l.url) + '" target="_blank" rel="noopener noreferrer" '
    + 'onclick="event.stopPropagation()" title="' + xh(l.title || displayTitle) + '">'
    + xh(displayTitle) + '</a>'
    + (vh != null ? '<div class="card-vstar">★ ' + vh.toFixed(1) + '</div>' : '')
    + '</div>';
  var addressLine = l.address
    ? '<div class="card-address">📍 ' + xh(l.address) + '</div>'
    : '';

  var parts = [];
  if (l.bedrooms) parts.push(l.bedrooms + (l.bedrooms === 1 ? ' bed' : ' beds'));
  if (l.sqm)      parts.push(Math.round(l.sqm) + ' m²');
  if (l.floor)    parts.push('Floor ' + l.floor);
  var desc  = parts.join(' · ');
  var price = '$' + (l.price || 0).toLocaleString() + ' MXN/mo';
  var valueBadge = l.value_per_sqm
    ? '<span class="value-badge">$' + Number(l.value_per_sqm).toLocaleString() + '/m²</span>'
    : '';
  var priceRow = '<div class="card-price">' + price + valueBadge + '</div>';

  var starsHtml = starsHTML(sid, l.vision_score);

  var reasonHtml = '';
  if (l.reason) {
    var rc = kind === 'approved' ? 'reason-approved' : 'reason-rejected';
    reasonHtml = '<div class="reason-note ' + rc + '">' + xh(l.reason) + '</div>';
  }

  var plHtml = '';
  if (kind === 'approved') {
    var btns = PIPELINE.map(function(s) {
      return '<button class="pl-btn" data-step="' + s.key + '" '
        + 'onclick="event.stopPropagation();togglePL(\\'' + sid + '\\',\\'' + s.key + '\\')">'
        + s.label + '</button>';
    }).join('');
    plHtml = '<div class="pipeline" id="pl-' + sid + '">' + btns + '</div>';
  }

  var inboxActions = '';
  if (kind === 'inbox') {
    inboxActions = '<div class="inbox-actions" id="ia-' + sid + '">'
      + '<button class="inbox-thumb up" onclick="event.stopPropagation();sendInboxFeedback(\\''
        + xh(l.id) + '\\',\\'' + sid + '\\',\\'up\\')">👍 Approve</button>'
      + '<button class="inbox-thumb down" onclick="event.stopPropagation();sendInboxFeedback(\\''
        + xh(l.id) + '\\',\\'' + sid + '\\',\\'down\\')">👎 Reject</button>'
      + '</div>';
  }

  var tgBadge = '';
  if (l.alerted) {
    tgBadge = '<div class="tg-badge">📱 Sent to TG</div>';
  }

  var flipActions = '';
  if (kind === 'approved' || kind === 'rejected') {
    var flipLabel = kind === 'approved' ? '\u21a9 Move to Inbox' : '\u21a9 Reconsider';
    flipActions = '<div class="flip-actions" id="fa-' + sid + '">'
      + '<button class="flip-btn" onclick="event.stopPropagation();sendFlipFeedback(\\''
      + xh(l.id) + '\\',\\'' + sid + '\\')">' + flipLabel + '</button>'
      + '</div>';
  }

  var noteHtml = '<div class="note-section">'
    + '<button class="add-note-link" onclick="event.stopPropagation();toggleNote(\\'' + sid + '\\')">Add note</button>'
    + '<div class="note-area" id="na-' + sid + '">'
    + '<textarea class="note-textarea" id="ni-' + sid + '" rows="2" placeholder="Personal note…"></textarea>'
    + '<div class="note-btns">'
    + '<button class="note-save" onclick="event.stopPropagation();saveNote(\\'' + sid + '\\')">Save</button>'
    + '<button class="note-cancel" onclick="event.stopPropagation();toggleNote(\\'' + sid + '\\')">Cancel</button>'
    + '</div></div>'
    + '<div class="note-display" id="nd-' + sid + '"></div>'
    + '</div>';

  // Listing ID badge — click-to-copy the back-end id for debugging when a
  // card's label looks wrong (user can paste into a DB query or TG reply).
  var idBadge = '<span class="id-badge" title="Click to copy back-end listing id" '
    + 'onclick="event.stopPropagation();navigator.clipboard&&navigator.clipboard.writeText(\\''
    + xh(l.id) + '\\').then(function(){var t=event.target;var o=t.textContent;t.textContent=\\'✓ copied\\';setTimeout(function(){t.textContent=o;},900);});">'
    + xh(l.id) + '</span>';

  var footer = '<div class="card-footer">'
    + '<span>' + xh(l.feedback_at || '') + '</span>'
    + idBadge
    + '<a href="' + xh(l.url) + '" target="_blank" rel="noopener noreferrer" '
    + 'onclick="event.stopPropagation()">View listing ↗</a>'
    + '</div>';

  return '<div class="listing-card ' + kind + '" id="card-' + sid + '" draggable="true" '
    + 'data-id="' + sid + '" data-lat="' + l.lat + '" data-lng="' + l.lng
    + '" data-signal="' + l.signal + '" data-precision="' + (l.precision || 'approx') + '">'
    + photoBlock
    + '<div class="card-info">' + row1
    + addressLine
    + (desc ? '<div class="card-desc">' + xh(desc) + '</div>' : '')
    + priceRow
    + starsHtml + reasonHtml + plHtml + inboxActions + flipActions + tgBadge + noteHtml + footer
    + '</div></div>';
}

function starsHTML(sid, vs) {
  var vh = vs != null ? Math.min(5, Math.max(0, vs / 2)) : null;
  var svHtml = '';
  if (vh !== null) {
    for (var n = 1; n <= 5; n++) {
      var c = vh >= n ? 'lit' : (vh >= n - .5 ? 'hl' : '');
      svHtml += '<span class="sv ' + c + '" data-v="' + n + '">★</span>';
    }
  }
  var spHtml = '';
  for (var n = 1; n <= 5; n++) {
    spHtml += '<span class="sp" data-n="' + n + '" onclick="event.stopPropagation();setStars(\\'' + sid + '\\',' + n + ')">★</span>';
  }
  var saHtml = '';
  for (var n = 0; n < 5; n++) saHtml += '<span class="sa">★</span>';
  return '<div class="star-block">'
    + (vh !== null
      ? '<div class="star-row"><span class="star-lbl">👁 Vision</span>' + svHtml
        + '<span class="star-n" id="vsn-' + sid + '">' + vh.toFixed(1) + '</span></div>'
      : '')
    + '<div class="star-row"><span class="star-lbl">★ Yours</span>'
    + '<span id="sp-' + sid + '">' + spHtml + '</span>'
    + '<span class="star-n" id="sn-' + sid + '">—</span></div>'
    + '<div class="star-row"><span class="star-lbl">≈ Avg</span>'
    + '<span id="sa-' + sid + '">' + saHtml + '</span>'
    + '<span class="star-n" id="an-' + sid + '">—</span></div>'
    + '</div>';
}

function emptyHTML(title, msg) {
  return '<div class="empty"><div class="empty-icon">🏗</div><h3>' + xh(title) + '</h3><p>' + xh(msg) + '</p></div>';
}

function xh(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Tabs ──────────────────────────────────────────────────────────────────
function switchTab(name) {
  ACTIVE_TAB = name;
  document.querySelectorAll('.tab-panel').forEach(function(p) { p.classList.remove('active'); });
  document.querySelectorAll('.tab-btn').forEach(function(b) { b.classList.remove('active'); });
  var panel = document.getElementById('panel-' + name);
  if (panel) panel.classList.add('active');
  var btn = document.getElementById('tab-' + name);
  if (btn) btn.classList.add('active');
  setTimeout(function() { if (MAPS[name]) MAPS[name].invalidateSize(); }, 60);
  if (name === 'taste') loadTaste();
}

// ── Map ───────────────────────────────────────────────────────────────────
function initMap(containerId, signal, allListings) {
  var el = document.getElementById(containerId);
  if (!el) return;
  // Approximate-pin notice: we do not geocode per listing — markers are the
  // neighbourhood centroid + stable id-seeded jitter. Same-hood pins used to
  // stack on one spot; now they spread ~±350 m. Not a replacement for real
  // geocoding; the banner keeps the user honest about the approximation.
  if (!el.querySelector('.map-approx-banner')) {
    var banner = document.createElement('div');
    banner.className = 'map-approx-banner';
    banner.textContent = 'Pins are approximate (neighbourhood centroid + jitter) — click “View listing ↗” for the real address.';
    el.appendChild(banner);
  }
  var tabName = signal === 'up' ? 'approved' : (signal === 'down' ? 'rejected' : 'inbox');
  var color   = signal === 'up' ? '#008a05' : (signal === 'down' ? '#e94560' : '#222');

  var m = L.map(containerId).setView([19.4326, -99.1332], 12);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap contributors', maxZoom: 19
  }).addTo(m);
  MAPS[tabName] = m;

  // ── Leaflet.draw — polygon search area ────────────────────────────────
  var drawnItems = new L.FeatureGroup().addTo(m);
  DRAW_LAYERS[tabName] = drawnItems;
  var drawControl = new L.Control.Draw({
    draw: {
      polygon:      { allowIntersection: false, showArea: true },
      polyline:     false,
      rectangle:    false,
      circle:       false,
      circlemarker: false,
      marker:       false,
    },
    edit: { featureGroup: drawnItems }
  });
  m.addControl(drawControl);

  m.on(L.Draw.Event.CREATED, function(e) {
    drawnItems.clearLayers();
    drawnItems.addLayer(e.layer);
    var rawCoords = e.layer.getLatLngs()[0];
    var coords = rawCoords.map(function(ll) { return [ll.lat, ll.lng]; });
    if (coords.length > 0) coords.push(coords[0]); // close polygon
    fetch('/criteria', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({geo_filter: {type: 'polygon', coords: coords}})
    }).then(function(r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      setStatus('Search area saved — applies to next scrape');
      document.getElementById('draw-area-btn').classList.remove('active');
      DRAW_MODE = false;
    }).catch(function() { setStatus('Area save failed (server down?)', true); });
  });

  m.on(L.Draw.Event.DELETED, function() {
    fetch('/criteria', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({geo_filter: null})
    }).catch(function() {});
    setStatus('Search area cleared');
  });

  // ── Markers ───────────────────────────────────────────────────────────
  allListings.filter(function(l) { return l.signal === signal; }).forEach(function(l) {
    addMarker(m, tabName, l, color);
  });
}

function addMarker(m, tabName, l, color) {
  // Pin visual encodes precision so the user can tell real geocoded pins
  // from neighborhood-centroid fallbacks at a glance.
  //   exact  → solid teardrop with opaque center dot
  //   approx → teardrop with dashed outline + hollow center (lower visual weight)
  var isApprox = (l.precision === 'approx');
  var stroke = isApprox ? color : 'rgba(0,0,0,.25)';
  var dash = isApprox ? ' stroke-dasharray="2,2"' : '';
  var centerFill = isApprox ? 'transparent' : 'rgba(255,255,255,.9)';
  var centerStroke = isApprox ? color : 'transparent';
  var svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 36" width="20" height="30">'
    + '<path d="M12 0C5.4 0 0 5.4 0 12c0 9 12 24 12 24s12-15 12-24C24 5.4 18.6 0 12 0z"'
    + ' fill="' + color + '" stroke="' + stroke + '" stroke-width="1.2"' + dash + '/>'
    + '<circle cx="12" cy="12" r="5" fill="' + centerFill + '" stroke="' + centerStroke + '" stroke-width="1.2"/></svg>';
  var icon = L.divIcon({html: svg, iconSize: [20, 30], iconAnchor: [10, 30], popupAnchor: [0, -32], className: ''});
  var mk = L.marker([l.lat, l.lng], {icon: icon}).addTo(m);
  mk.bindTooltip('$' + (l.price || 0).toLocaleString(), {
    permanent: true, direction: 'bottom', className: 'price-tip', offset: [0, 2]
  });
  var popupTitle = l.title && l.title.length ? xh(l.title) : xh(l.neighborhood || 'Listing');
  var addressHtml = l.address ? '<br><small style="color:#666">📍 ' + xh(l.address) + '</small>' : '';
  var precisionHtml = isApprox
    ? '<br><small style="color:#e94560">⚠ approximate pin — click View for real address</small>'
    : '';
  mk.bindPopup(
    '<b>' + popupTitle + '</b>' + addressHtml
    + '<br>$' + (l.price || 0).toLocaleString() + ' MXN/mo'
    + '<br><small style="color:#888;font-family:monospace">' + xh(l.id) + '</small>'
    + precisionHtml
    + '<br><a href="' + xh(l.url) + '" target="_blank">View ↗</a>'
  );
  mk.on('click', (function(id) { return function() { onPinClick(id); }; })(l.safe_id));
  if (!MARKERS[l.safe_id]) MARKERS[l.safe_id] = {};
  MARKERS[l.safe_id][tabName] = mk;
}

function refreshMarkers(data) {
  // Clear and redraw markers without rebuilding maps
  var colorMap = {approved: '#008a05', rejected: '#e94560', inbox: '#222'};
  var signalMap = {approved: 'up', rejected: 'down', inbox: 'new'};
  ['approved', 'rejected', 'inbox'].forEach(function(tab) {
    var m = MAPS[tab];
    if (!m) return;
    // Remove old markers
    Object.keys(MARKERS).forEach(function(sid) {
      if (MARKERS[sid][tab]) {
        m.removeLayer(MARKERS[sid][tab]);
        delete MARKERS[sid][tab];
      }
    });
    // Add fresh markers
    var listings = data[tab] || [];
    listings.forEach(function(l) { addMarker(m, tab, l, colorMap[tab]); });
  });
}

function attachCardClicks(listings, tabName) {
  listings.forEach(function(l) {
    var card = document.getElementById('card-' + l.safe_id);
    if (!card) return;
    card.addEventListener('click', function(e) {
      if (e.target.closest('a,button,textarea')) return;
      var map = MAPS[tabName];
      if (map) map.flyTo([l.lat, l.lng], 15, {animate: true, duration: 1.2});
      var entry = MARKERS[l.safe_id];
      if (entry && entry[tabName]) {
        var el = entry[tabName].getElement();
        if (el) {
          el.style.transition = 'transform .2s';
          el.style.transform  = 'scale(1.5)';
          setTimeout(function() { el.style.transform = ''; }, 600);
        }
      }
    });
  });
}

function onPinClick(safeId) {
  var card = document.getElementById('card-' + safeId);
  if (!card) return;
  card.scrollIntoView({behavior: 'smooth', block: 'center'});
  card.classList.add('card-glow');
  setTimeout(function() { card.classList.remove('card-glow'); }, 2000);
}

// ── Bidirectional trace: backend id ↔ HTML card ────────────────
// Accept raw backend ids ('ml:71cc7e669b', 'i24:149241453') OR bare numeric
// fragments from a listing URL ('149241453' → i24:149241453). Switches the
// tab, scrolls to the card, flyTo'd the pin. This is the "two touchpoints
// share one database" promise: paste any id from Telegram or the DB into
// the Find ID box, land on the exact row in the cockpit.
function jumpToListing(rawInput) {
  var q = String(rawInput || '').trim();
  if (!q) return;
  var tabs = ['approved', 'rejected', 'inbox'];
  var found = null;
  for (var i = 0; i < tabs.length && !found; i++) {
    var tab = tabs[i];
    var list = DATA[tab] || [];
    for (var j = 0; j < list.length; j++) {
      var l = list[j];
      if (
        l.id === q ||
        l.safe_id === q ||
        (l.id && l.id.indexOf(q) >= 0) ||
        (l.url && l.url.indexOf(q) >= 0)
      ) {
        found = {tab: tab, listing: l};
        break;
      }
    }
  }
  if (!found) {
    setStatus('No listing found for ' + q, true);
    return;
  }
  switchTab(found.tab);
  setTimeout(function() {
    onPinClick(found.listing.safe_id);
    var mk = MARKERS[found.listing.safe_id] && MARKERS[found.listing.safe_id][found.tab];
    var map = MAPS[found.tab];
    if (mk && map) {
      map.flyTo([found.listing.lat, found.listing.lng], 16, {animate: true, duration: 1.0});
      mk.openPopup && mk.openPopup();
    }
    setStatus('→ ' + found.listing.id + (found.listing.precision === 'approx' ? ' (pin is approximate)' : ''));
  }, 120);
}

// Deep link: ?listing=<id> — run once after first render so DATA is populated.
function handleDeepLink() {
  try {
    var params = new URLSearchParams(window.location.search);
    var wanted = params.get('listing');
    if (wanted) jumpToListing(wanted);
  } catch (e) { /* ignore */ }
}

// ── Draw mode toggle ──────────────────────────────────────────────────────
function toggleDrawMode() {
  DRAW_MODE = !DRAW_MODE;
  document.getElementById('draw-area-btn').classList.toggle('active', DRAW_MODE);
  setStatus(DRAW_MODE ? 'Click map to draw search polygon. Click again to finish.' : '');
}

function clearGeoFilter() {
  fetch('/criteria', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({geo_filter: null})
  }).then(function() {
    ['approved', 'rejected', 'inbox'].forEach(function(tab) {
      var dl = DRAW_LAYERS[tab];
      if (dl) dl.clearLayers();
    });
    setStatus('Search area cleared');
  }).catch(function() { setStatus('Clear failed (server down?)', true); });
}

// ── Carousel ──────────────────────────────────────────────────────────────
function cGo(sid, dir) {
  var s = CAROUSEL[sid];
  if (!s) return;
  s.cur = (s.cur + dir + s.count) % s.count;
  var tr = document.getElementById('tr-' + sid);
  if (tr) tr.style.transform = 'translateX(-' + (s.cur * 100) + '%)';
  document.querySelectorAll('#dots-' + sid + ' .dot').forEach(function(d, i) {
    d.classList.toggle('active', i === s.cur);
  });
}

// ── Stars (persists to DB via /pipeline_status) ───────────────────────────
function loadStars(sid, vs, serverStars) {
  var n = Number(serverStars || 0);
  renderSP(sid, n);
  var vh = vs != null ? Math.min(5, vs / 2) : 0;
  renderAvg(sid, vh, n);
}

function setStars(sid, n) {
  var realId = LISTING_ID_MAP[sid];
  if (!realId) return;
  fetch('/pipeline_status', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({listing_id: realId, stars: n})
  })
    .then(function(r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then(function(payload) {
      updateListingState(sid, {pipeline_stars: payload.stars, pipeline_status: payload.status});
      renderSP(sid, payload.stars);
      var l = findListing(sid);
      var vh = (l && l.vision_score != null) ? Math.min(5, l.vision_score / 2) : 0;
      renderAvg(sid, vh, payload.stars);
      setStatus('Stars saved');
    })
    .catch(function(e) {
      console.warn('[stars] POST failed:', e);
      setStatus('Stars update failed (server down?)', true);
    });
}

function renderSP(sid, n) {
  var c = document.getElementById('sp-' + sid);
  if (c) c.querySelectorAll('.sp').forEach(function(s) {
    s.classList.toggle('lit', parseInt(s.dataset.n) <= n);
  });
  var el = document.getElementById('sn-' + sid);
  if (el) el.textContent = n > 0 ? n : '—';
}

function renderAvg(sid, vh, personal) {
  var avg = vh > 0 && personal > 0 ? (vh + personal) / 2
          : personal > 0 ? personal / 2
          : vh > 0 ? vh : 0;
  var c = document.getElementById('sa-' + sid);
  if (c) c.querySelectorAll('.sa').forEach(function(s, i) {
    s.classList.toggle('lit', (i + .5) < avg);
  });
  var el = document.getElementById('an-' + sid);
  if (el) el.textContent = avg > 0 ? avg.toFixed(1) : '—';
}

// ── Pipeline (persists to DB via /pipeline_status) ────────────────────────
function pipelineState(status) {
  var st = {};
  if (status && status !== 'none') st[status] = true;
  return st;
}

function loadPipeline(sid, serverStatus) {
  renderPL(sid, pipelineState(serverStatus));
}

function togglePL(sid, step) {
  var realId = LISTING_ID_MAP[sid];
  if (!realId) return;
  var current = findListing(sid);
  var currentStatus = (current && current.pipeline_status) ? current.pipeline_status : 'none';
  var nextStatus = currentStatus === step ? 'none' : step;
  fetch('/pipeline_status', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({listing_id: realId, status: nextStatus})
  })
    .then(function(r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then(function(payload) {
      updateListingState(sid, {pipeline_status: payload.status, pipeline_stars: payload.stars});
      renderPL(sid, pipelineState(payload.status));
      setStatus('Pipeline updated');
    })
    .catch(function(e) {
      console.warn('[pipeline] POST failed:', e);
      setStatus('Pipeline update failed (server down?)', true);
    });
}

function renderPL(sid, st) {
  var c = document.getElementById('pl-' + sid);
  if (!c) return;
  c.querySelectorAll('.pl-btn').forEach(function(b) {
    b.classList.toggle('active', !!st[b.dataset.step]);
  });
}

// ── Notes ─────────────────────────────────────────────────────────────────
function loadNote(sid) {
  var l = findListing(sid);
  var v = (l && l.note_body) ? l.note_body : '';
  var inp = document.getElementById('ni-' + sid);
  if (inp) inp.value = v;
  var d = document.getElementById('nd-' + sid);
  if (d) d.textContent = v ? '📝 ' + v : '';
}

function toggleNote(sid) {
  var a = document.getElementById('na-' + sid);
  if (!a) return;
  var open = a.style.display === 'block';
  a.style.display = open ? 'none' : 'block';
  if (!open) {
    var inp = document.getElementById('ni-' + sid);
    var l = findListing(sid);
    var v = (l && l.note_body) ? l.note_body : '';
    if (inp) { inp.value = v; inp.focus(); }
  }
}

function saveNote(sid) {
  var inp = document.getElementById('ni-' + sid);
  if (!inp) return;
  var t = inp.value.trim();
  var realId = LISTING_ID_MAP[sid];
  if (!realId) return;
  fetch(CRITERIA_BASE + '/note', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({listing_id: realId, body: t})
  })
    .then(function(r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then(function() {
      updateListingState(sid, {note_body: t});
      var d = document.getElementById('nd-' + sid);
      if (d) d.textContent = t ? '📝 ' + t : '';
      toggleNote(sid);
      setStatus('Note saved');
    })
    .catch(function(e) {
      console.warn('[note] POST failed:', e);
      setStatus('Note save failed (server down?)', true);
    });
}

// ── Taste tab ─────────────────────────────────────────────────────────────
function loadTaste() {
  fetch('/taste')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var el = document.getElementById('taste-content');
      if (!el) return;
      var html = '';
      if (!d.has_signal) {
        html = '<p class="taste-empty">Not enough feedback yet (need 3+ rows). Keep reviewing!</p>';
      } else {
        html = '<div class="taste-grid">';
        if (d.top_positive && d.top_positive.length) {
          html += '<div class="taste-col"><h4>👍 Positive signals</h4><ul>';
          d.top_positive.forEach(function(pair) {
            html += '<li>' + xh(pair[0]) + ' <b class="pos">+' + pair[1] + 'x</b></li>';
          });
          html += '</ul></div>';
        }
        if (d.top_negative && d.top_negative.length) {
          html += '<div class="taste-col"><h4>👎 Negative signals</h4><ul>';
          d.top_negative.forEach(function(pair) {
            html += '<li>' + xh(pair[0]) + ' <b>-' + pair[1] + 'x</b></li>';
          });
          html += '</ul></div>';
        }
        if (d.hard_filter_candidates && d.hard_filter_candidates.length) {
          html += '<div class="taste-col"><h4>🚫 Auto-reject candidates (5+ dislikes)</h4><ul>';
          d.hard_filter_candidates.forEach(function(t) {
            html += '<li>' + xh(t) + '</li>';
          });
          html += '</ul></div>';
        }
        html += '</div>';
        html += '<p class="taste-note">' + d.total_feedback + ' feedback rows · weights injected into vision scoring</p>';
      }
      el.innerHTML = html;
    })
    .catch(function() { /* silent — server may not be up */ });
}

// ── Control bar ───────────────────────────────────────────────────────────
var FILTER = {area: 'all', source: 'all', minScore: 0, maxPrice: null};

function setStatus(msg, isError) {
  var el = document.getElementById('cb-status');
  if (!el) return;
  el.textContent = msg || '';
  el.style.color = isError ? '#e94560' : 'var(--muted)';
}

function setCadence(days) {
  fetch('/cadence', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({days: days})
  }).then(function(r) {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    document.querySelectorAll('.cb-pill[data-cadence]').forEach(function(b) {
      b.classList.toggle('active', parseInt(b.dataset.cadence) === days);
    });
    setStatus('Cadence → ' + days + 'd');
  }).catch(function() { setStatus('Cadence update failed (server down?)', true); });
}

var _runPollTimer = null;
var _runPollCount = 0;

function startRunProgressPolling() {
  _runPollCount = 0;
  if (_runPollTimer) clearInterval(_runPollTimer);
  var el = document.getElementById('run-progress');
  if (el) el.style.display = 'block';
  _runPollTimer = setInterval(function() {
    _runPollCount++;
    if (_runPollCount > 120) {
      clearInterval(_runPollTimer);
      var el = document.getElementById('run-progress');
      if (el) el.style.display = 'none';
      setStatus('Run timed out — check logs', true);
      return;
    }
    fetch('/run_meta')
      .then(function(r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function(d) {
        var lines = (d.log_tail || []).slice(-3).join('\\n');
        var el = document.getElementById('run-progress');
        if (el) el.textContent = lines || 'Waiting for output…';
        if (lines.indexOf('[run] Done.') >= 0) {
          clearInterval(_runPollTimer);
          if (el) el.style.display = 'none';
          setStatus('Run complete — refreshing…');
          setTimeout(poll, 1000);
        }
      })
      .catch(function() {});
  }, 5000);
}

function triggerRun() {
  setStatus('Triggering run…');
  fetch('/run', {method: 'POST'})
    .then(function(r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      setStatus('Run started — watching progress…');
      startRunProgressPolling();
    })
    .catch(function() { setStatus('Run trigger failed (server down?)', true); });
}

function filterArea(btn, area) {
  FILTER.area = area;
  document.querySelectorAll('.cb-pill[data-area]').forEach(function(b) {
    b.classList.toggle('active', b.dataset.area === area);
  });
  applyFilters();
}

function filterSource(btn, src) {
  FILTER.source = src;
  document.querySelectorAll('.cb-pill[data-source]').forEach(function(b) {
    b.classList.toggle('active', b.dataset.source === src);
  });
  applyFilters();
}

function injectFilterPills(listings, extraAreas) {
  var areas = {}, sources = {};
  listings.forEach(function(l) {
    if (l.neighborhood) areas[l.neighborhood] = 'data';
    if (l.source)       sources[l.source]     = 1;
  });
  (extraAreas || []).forEach(function(a) {
    if (a && !areas[a]) areas[a] = 'criteria';
  });
  var areaGroup = document.getElementById('area-pills');
  // Clear dynamic pills (keep "All")
  Array.from(areaGroup.querySelectorAll('.cb-pill:not([data-area="all"])')).forEach(function(b) { b.remove(); });
  Object.keys(areas).sort().forEach(function(a) {
    var b = document.createElement('button');
    b.className    = 'cb-pill' + (areas[a] === 'criteria' ? ' pill-empty' : '');
    b.dataset.area = a;
    b.textContent  = a;
    b.title        = areas[a] === 'criteria' ? 'No listings yet' : '';
    b.onclick = function() { filterArea(b, a); };
    areaGroup.appendChild(b);
  });
  var srcGroup = document.getElementById('source-pills');
  Array.from(srcGroup.querySelectorAll('.cb-pill:not([data-source="all"])')).forEach(function(b) { b.remove(); });
  Object.keys(sources).sort().forEach(function(s) {
    var b = document.createElement('button');
    b.className      = 'cb-pill';
    b.dataset.source = s;
    b.textContent    = s;
    b.onclick = function() { filterSource(b, s); };
    srcGroup.appendChild(b);
  });
}

function applyFilters() {
  FILTER.minScore = parseFloat(document.getElementById('min-score').value) || 0;
  FILTER.maxPrice = parseFloat(document.getElementById('max-price').value) || null;
  var shown = 0, total = 0;
  document.querySelectorAll('.listing-card').forEach(function(c) {
    total++;
    var sid = c.dataset.id;
    var l   = findListing(sid);
    if (!l) return;
    var vs    = l.vision_score || 0;
    var price = l.price || 0;
    var ok = (FILTER.area === 'all' || l.neighborhood === FILTER.area)
          && (FILTER.source === 'all' || l.source === FILTER.source)
          && (vs >= FILTER.minScore)
          && (!FILTER.maxPrice || price <= FILTER.maxPrice);
    c.style.display = ok ? '' : 'none';
    if (ok) shown++;
  });
  setStatus('Showing ' + shown + ' of ' + total);
  renderStats();
}

function findListing(sid) {
  var all = (DATA.approved || []).concat(DATA.rejected || []).concat(DATA.inbox || []);
  for (var i = 0; i < all.length; i++) if (all[i].safe_id === sid) return all[i];
  return null;
}

function updateListingState(sid, patch) {
  var listing = findListing(sid);
  if (!listing) return;
  Object.keys(patch).forEach(function(key) {
    listing[key] = patch[key];
  });
}

function renderStats() {
  var all = (DATA.approved || []).concat(DATA.rejected || []).concat(DATA.inbox || []);
  var visible = all.filter(function(l) {
    var c = document.getElementById('card-' + l.safe_id);
    return c && c.style.display !== 'none';
  });
  var prices = visible.map(function(l) { return l.price || 0; }).filter(function(p) { return p > 0; });
  var scores = visible.map(function(l) { return l.vision_score || 0; });
  var avgP   = prices.length ? Math.round(prices.reduce(function(a, b) { return a + b; }, 0) / prices.length) : 0;
  var avgS   = scores.length ? scores.reduce(function(a, b) { return a + b; }, 0) / scores.length : 0;
  var minP   = prices.length ? Math.min.apply(null, prices) : 0;
  var maxP   = prices.length ? Math.max.apply(null, prices) : 0;
  var el = document.getElementById('stats-strip');
  el.innerHTML =
    '<div class="stat-item"><b>' + visible.length + '</b> <span>listings</span></div>'
    + '<div class="stat-item"><b>$' + Math.round(avgP / 1000) + 'k</b> <span>avg</span></div>'
    + '<div class="stat-item"><b>$' + Math.round(minP / 1000) + 'k–$' + Math.round(maxP / 1000) + 'k</b> <span>range</span></div>'
    + '<div class="stat-item"><b>' + avgS.toFixed(1) + '</b> <span>avg vision ★</span></div>';
}

// ── Drag & Drop ───────────────────────────────────────────────────────────
function initDrag(listId) {
  var list = document.getElementById(listId);
  if (!list) return;
  var src = null;
  list.querySelectorAll('.listing-card').forEach(function(card) {
    card.addEventListener('dragstart', function(e) {
      src = card.id; card.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move';
    });
    card.addEventListener('dragend', function() { card.classList.remove('dragging'); });
    card.addEventListener('dragover', function(e) { e.preventDefault(); card.classList.add('drag-over'); });
    card.addEventListener('dragleave', function() { card.classList.remove('drag-over'); });
    card.addEventListener('drop', function(e) {
      e.preventDefault(); card.classList.remove('drag-over');
      if (src && src !== card.id) {
        var s = document.getElementById(src), p = card.parentNode;
        var si = Array.from(p.children).indexOf(s), di = Array.from(p.children).indexOf(card);
        if (si < di) p.insertBefore(s, card.nextSibling); else p.insertBefore(s, card);
        persistDragOrder(card.parentNode);
      }
    });
  });
}

function persistDragOrder(listEl) {
  Array.from(listEl.querySelectorAll('.listing-card')).forEach(function(c, idx) {
    var sid = c.dataset.id;
    var realId = LISTING_ID_MAP[sid];
    if (!realId) return;
    fetch('/pipeline_status', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({listing_id: realId, order_rank: idx})
    })
      .then(function(r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        updateListingState(sid, {pipeline_order: idx});
      })
      .catch(function(e) { console.warn('[drag] order persist failed:', e); });
  });
}

// ── Search criteria editor ────────────────────────────────────────────────
var CRITERIA_BASE = '';  // same-origin — served by Flask
var CRITERIA_LAST = {};
var CRITERIA_DEBOUNCE = null;

function toggleCriteriaPanel() {
  var p = document.getElementById('criteria-panel');
  if (!p) return;
  var open = p.classList.toggle('open');
  var btn  = p.querySelector('.criteria-toggle');
  if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function toggleFilterPanel() {
  var block = document.getElementById('cb-filterblock');
  var btn   = document.getElementById('cb-expand-btn');
  if (!block) return;
  var open = block.style.display === 'none';
  block.style.display = open ? '' : 'none';
  if (btn) btn.textContent = open ? '✕ Filters' : '⚙ Filters';
}

function loadCriteria() {
  fetch(CRITERIA_BASE + '/criteria').then(function(r) {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }).then(function(c) {
    if (!c || typeof c !== 'object') return;
    CRITERIA_LAST = JSON.parse(JSON.stringify(c));
    var setNum  = function(id, v) { var el = document.getElementById(id); if (el && v != null && v !== '') el.value = v; };
    var setBool = function(id, v) { var el = document.getElementById(id); if (el) el.checked = !!v; };
    if (Array.isArray(c.neighborhoods)) {
      var el = document.getElementById('cp-neighborhoods');
      if (el) el.value = c.neighborhoods.join(', ');
    }
    setNum('cp-price-min',  c.price_min);
    setNum('cp-price-max',  c.price_max);
    setNum('cp-bedrooms',   c.bedrooms);
    setNum('cp-bathrooms',  c.bathrooms);
    setNum('cp-min-floor',  c.min_floor);
    setNum('cp-min-sqm',    c.min_sqm);
    setNum('cp-min-vision', c.min_vision_score);
    setBool('cp-furnished',      c.furnished);
    setBool('cp-require-photos', c.require_photos);
  }).catch(function() { /* silent */ });
}

function readCriteriaForm() {
  var c   = {};
  var nbText = (document.getElementById('cp-neighborhoods').value || '').trim();
  c.neighborhoods = nbText
    ? nbText.split(',').map(function(s) { return s.trim(); }).filter(function(s) { return s.length > 0; })
    : [];
  var pickInt   = function(id) { var v = document.getElementById(id).value; return v === '' ? null : parseInt(v, 10); };
  var pickFloat = function(id) { var v = document.getElementById(id).value; return v === '' ? null : parseFloat(v); };
  c.price_min        = pickInt('cp-price-min');
  c.price_max        = pickInt('cp-price-max');
  c.bedrooms         = pickInt('cp-bedrooms');
  c.bathrooms        = pickInt('cp-bathrooms');
  c.min_floor        = pickInt('cp-min-floor');
  c.min_sqm          = pickFloat('cp-min-sqm');
  c.min_vision_score = pickFloat('cp-min-vision');
  c.furnished        = document.getElementById('cp-furnished').checked;
  c.require_photos   = document.getElementById('cp-require-photos').checked;
  return c;
}

function postCriteria(payload, onOk) {
  return fetch(CRITERIA_BASE + '/criteria', {
    method:  'POST',
    headers: {'Content-Type': 'application/json'},
    body:    JSON.stringify(payload)
  }).then(function(r) {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    var el = document.getElementById('cb-status');
    if (el) { el.textContent = '✓ Criteria saved'; el.classList.add('cp-status-good'); }
    if (typeof onOk === 'function') onOk();
  }).catch(function() {
    var el = document.getElementById('cb-status');
    if (el) { el.textContent = 'Criteria save failed (server down?)'; el.style.color = '#e94560'; }
  });
}

function onCriteriaChange() {
  if (CRITERIA_DEBOUNCE) clearTimeout(CRITERIA_DEBOUNCE);
  CRITERIA_DEBOUNCE = setTimeout(function() {
    var current = readCriteriaForm();
    var diff = {};
    Object.keys(current).forEach(function(k) {
      var a = current[k], b = CRITERIA_LAST[k];
      var aa = Array.isArray(a) ? JSON.stringify(a) : a;
      var bb = Array.isArray(b) ? JSON.stringify(b) : b;
      if (aa !== bb && a !== null) diff[k] = a;
    });
    if (Object.keys(diff).length === 0) return;
    postCriteria(diff, function() { CRITERIA_LAST = current; });
  }, 500);
}

function applyAndRunCriteria() {
  var current = readCriteriaForm();
  // T3.4: filter out null values so /criteria doesn't reject with type error
  var payload = {};
  Object.keys(current).forEach(function(k) { if (current[k] !== null) payload[k] = current[k]; });
  postCriteria(payload, function() {
    CRITERIA_LAST = current;
    triggerRun();
  });
}

// ── Inbox thumbs ──────────────────────────────────────────────────────────
function sendInboxFeedback(listingId, safeId, signal) {
  var actions = document.getElementById('ia-' + safeId);
  if (actions) actions.querySelectorAll('button').forEach(function(b) { b.disabled = true; });
  fetch(CRITERIA_BASE + '/feedback', {
    method:  'POST',
    headers: {'Content-Type': 'application/json'},
    body:    JSON.stringify({listing_id: listingId, signal: signal, reason: ''})
  }).then(function(r) {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    moveInboxCard(safeId, signal);
    setStatus(signal === 'up' ? 'Approved — moved to Approved tab' : 'Rejected — moved to Rejected tab');
  }).catch(function() {
    if (actions) actions.querySelectorAll('button').forEach(function(b) { b.disabled = false; });
    setStatus('Feedback failed (server down?)', true);
  });
}

function sendFlipFeedback(listingId, safeId) {
  var btn = document.querySelector('#fa-' + safeId + ' .flip-btn');
  if (btn) btn.disabled = true;
  fetch(CRITERIA_BASE + '/feedback', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({listing_id: listingId, signal: 'reset', reason: ''})
  }).then(function(r) {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    var card = document.getElementById('card-' + safeId);
    if (card && card.parentNode) card.parentNode.removeChild(card);
    var wasApproved = !!(DATA.approved || []).find(
      function(l) { return l.safe_id === safeId; });
    if (wasApproved) {
      DATA.approved = (DATA.approved || []).filter(
        function(l) { return l.safe_id !== safeId; });
      var ca = document.getElementById('cnt-approved');
      if (ca) ca.textContent = DATA.approved.length;
    } else {
      DATA.rejected = (DATA.rejected || []).filter(
        function(l) { return l.safe_id !== safeId; });
      var cr = document.getElementById('cnt-rejected');
      if (cr) cr.textContent = DATA.rejected.length;
    }
    setStatus('Moved back to Inbox \u2014 will appear on next refresh');
  }).catch(function() {
    if (btn) btn.disabled = false;
    setStatus('Flip failed (server down?)', true);
  });
}

function moveInboxCard(safeId, signal) {
  var idx = -1;
  for (var i = 0; i < (DATA.inbox || []).length; i++) {
    if (DATA.inbox[i].safe_id === safeId) { idx = i; break; }
  }
  if (idx < 0) return;
  var listing = DATA.inbox.splice(idx, 1)[0];
  listing.signal = signal;
  if (signal === 'up') (DATA.approved = DATA.approved || []).unshift(listing);
  else                 (DATA.rejected = DATA.rejected || []).unshift(listing);

  var card = document.getElementById('card-' + safeId);
  if (card && card.parentNode) card.parentNode.removeChild(card);

  var ci = document.getElementById('cnt-inbox');    if (ci) ci.textContent = (DATA.inbox || []).length;
  var ca = document.getElementById('cnt-approved'); if (ca) ca.textContent = (DATA.approved || []).length;
  var cr = document.getElementById('cnt-rejected'); if (cr) cr.textContent = (DATA.rejected || []).length;

  var ibList = document.getElementById('cards-inbox');
  if (ibList && (DATA.inbox || []).length === 0) {
    ibList.innerHTML = emptyHTML('Inbox empty', 'New scrapes will appear here.');
  }
}
</script>
</body>
</html>"""


def _html_template() -> str:
    """Return the complete HTML, injecting the neighbourhood coords JS object."""
    coord_js = _neighborhood_coords_js()
    return _HTML_TEMPLATE.replace("__NEIGHBORHOOD_COORDS__", coord_js)


def generate_and_save(
    db_path: str | Path = APP_DIR / "scrapy.db",
    output_dir: "Path | None" = None,
) -> Path:
    """Write listings_data.json + favorites.html. Called by feedback_server on every feedback event.

    Args:
        db_path: Path to the SQLite database.
        output_dir: Directory for output files. Defaults to REPORTS_DIR.
                    Pass tmp_path in tests to avoid concurrent-write collisions.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    approved = _fetch_rated(conn, "up")
    rejected = _fetch_rated(conn, "down")
    inbox    = _fetch_inbox(conn)
    conn.close()

    data = _build_data_dict(approved, rejected, inbox)

    out = Path(output_dir) if output_dir is not None else REPORTS_DIR
    out.mkdir(parents=True, exist_ok=True)

    # Write JSON snapshot (for external tooling / backward compat)
    json_path = out / "listings_data.json"
    json_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Write HTML shell — no embedded JSON, polls /listings every 30s
    html_path = out / "favorites.html"
    html_path.write_text(_html_template(), encoding="utf-8")

    print(f"Review page: {html_path.resolve()}")
    print(f"  {len(approved)} approved · {len(rejected)} rejected · {len(inbox)} inbox")
    return html_path


# Backward-compat alias used by older call sites
def generate_favorites_html(db_path: str = "scrapy.db") -> str:
    return str(generate_and_save(db_path).resolve())


if __name__ == "__main__":
    generate_and_save()
