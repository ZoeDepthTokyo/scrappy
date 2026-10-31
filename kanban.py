"""Kanban cockpit — additive view on top of the existing Scrappy pipeline.

Registers two routes on the shared Flask app:
  GET  /kanban       -> serve Kanban HTML
  POST /api/stage    -> move a listing to a new stage (persists to pipeline_status.kanban_stage)

Revert: delete this file + remove `kanban.register(app, DB_PATH)` from feedback_server.py.
"""
from __future__ import annotations

import json as _json
from datetime import datetime, timezone
from flask import request, jsonify, make_response

KANBAN_STAGES = ["inbox", "shortlisted", "contacted", "visit_scheduled"]
STAGE_LABELS = {
    "inbox": "Inbox",
    "shortlisted": "Me interesa",
    "contacted": "Contactado",
    "visit_scheduled": "Visita agendada",
}

# Set by feedback_server via register(): kanban.DB_PATH = DB_PATH
app = None
DB_PATH = None


def _conn():
    import db as _db
    return _db.connect(str(DB_PATH))


def _listing_exists(conn, listing_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM listings WHERE id=?", (listing_id,)
    ).fetchone() is not None


def register(flask_app, db_path) -> None:
    """Call once from feedback_server.py after creating the Flask app."""
    global app, DB_PATH
    app = flask_app
    DB_PATH = db_path

    @flask_app.route("/api/stage", methods=["POST"])
    def api_stage():
        body = request.get_json(silent=True) or {}
        listing_id = body.get("listing_id", "").strip()
        stage = body.get("stage", "").strip()

        if not listing_id or not stage:
            return jsonify({"ok": False, "error": "listing_id and stage required"}), 400
        if stage not in KANBAN_STAGES:
            return jsonify({"ok": False, "error": f"stage must be one of {KANBAN_STAGES}"}), 400

        conn = _conn()
        try:
            if not _listing_exists(conn, listing_id):
                return jsonify({"ok": False, "error": "listing not found"}), 404
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO pipeline_status (listing_id, status, kanban_stage, updated_at) "
                "VALUES (?, 'active', ?, ?) "
                "ON CONFLICT(listing_id) DO UPDATE SET "
                "kanban_stage=excluded.kanban_stage, updated_at=excluded.updated_at",
                (listing_id, stage, now),
            )
            conn.commit()
        finally:
            conn.close()

        return jsonify({"ok": True})

    @flask_app.route("/kanban")
    def kanban_view():
        return _serve_kanban()


# ---------------------------------------------------------------------------
# HTML generator
# ---------------------------------------------------------------------------

def _fetch_kanban_listings() -> dict[str, list[dict]]:
    """Return listings grouped by stage. Excludes 'down'-signalled listings."""
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT
                l.id, l.source, l.url, l.price, l.neighborhood,
                l.title, l.address, l.bedrooms, l.bathrooms, l.sqm,
                l.photos_json, l.vision_score, l.latitude, l.longitude,
                ps.kanban_stage,
                (SELECT signal FROM feedback f
                 WHERE f.listing_id = l.id
                 ORDER BY f.id DESC LIMIT 1) AS latest_signal
            FROM listings l
            LEFT JOIN pipeline_status ps ON ps.listing_id = l.id
            WHERE (
                SELECT signal FROM feedback f
                WHERE f.listing_id = l.id
                ORDER BY f.id DESC LIMIT 1
            ) IS NULL
            OR (
                SELECT signal FROM feedback f
                WHERE f.listing_id = l.id
                ORDER BY f.id DESC LIMIT 1
            ) != 'down'
            ORDER BY l.first_seen DESC
        """).fetchall()
    finally:
        conn.close()

    cols = [
        "id", "source", "url", "price", "neighborhood", "title", "address",
        "bedrooms", "bathrooms", "sqm", "photos_json", "vision_score",
        "latitude", "longitude", "kanban_stage", "latest_signal",
    ]
    grouped: dict[str, list[dict]] = {s: [] for s in KANBAN_STAGES}
    for row in rows:
        r = dict(zip(cols, row))
        if r["kanban_stage"] and r["kanban_stage"] in KANBAN_STAGES:
            stage = r["kanban_stage"]
        elif r["latest_signal"] == "up":
            stage = "shortlisted"
        else:
            stage = "inbox"
        r["photos"] = _json.loads(r["photos_json"] or "[]")
        grouped[stage].append(r)
    return grouped


def _card_html(listing: dict) -> str:
    photo = listing["photos"][0] if listing["photos"] else ""
    price_str = f"${listing['price']:,} MXN" if listing["price"] else "Precio no disponible"
    title = listing.get("title") or listing.get("neighborhood") or listing["source"]
    neigh = listing.get("neighborhood") or ""
    sqm = f"{int(listing['sqm'])} m²" if listing.get("sqm") else ""
    beds = f"{listing['bedrooms']} rec" if listing.get("bedrooms") else ""
    baths = f"{listing['bathrooms']} baños" if listing.get("bathrooms") else ""
    specs = " · ".join(x for x in [beds, baths, sqm] if x)
    lid = listing["id"].replace(":", "-").replace(".", "-")
    photo_html = (
        f"<img class='kcard__photo' src='{photo}' loading='lazy'>"
        if photo else "<div class='kcard__nophoto'>Sin foto</div>"
    )
    neigh_html = f"<div class='kcard__neigh'>{neigh}</div>" if neigh else ""
    specs_html = f"<div class='kcard__specs'>{specs}</div>" if specs else ""
    return (
        f'<li class="kcard" data-listing-id="{listing["id"]}"'
        f' data-lat="{listing.get("latitude") or ""}"'
        f' data-lng="{listing.get("longitude") or ""}" id="kc-{lid}">'
        f'<a class="kcard__move" title="Mover" data-lid="{listing["id"]}">&#8644;</a>'
        f"{photo_html}"
        f'<div class="kcard__body">'
        f'<div class="kcard__title"><a href="{listing["url"]}" target="_blank" rel="noopener">{title}</a></div>'
        f"{neigh_html}{specs_html}"
        f'<div class="kcard__price">{price_str}</div>'
        f"</div></li>"
    )


def _column_html(stage: str, listings: list[dict]) -> str:
    label = STAGE_LABELS[stage]
    cards = "".join(_card_html(l) for l in listings)
    count = len(listings)
    empty = '<li class="kcol__empty">Arrastra aquí</li>'
    return (
        f'<div class="kcol" id="kcol-{stage}" data-stage="{stage}">'
        f'<div class="kcol__header">'
        f'<span class="kcol__label">{label}</span>'
        f'<span class="kcol__count">{count}</span>'
        f"</div>"
        f'<ul class="kcol__cards" id="cards-{stage}" data-stage="{stage}">'
        f"{cards if cards else empty}"
        f"</ul></div>"
    )


def _serve_kanban():
    grouped = _fetch_kanban_listings()
    columns = "".join(_column_html(s, grouped[s]) for s in KANBAN_STAGES)
    total = sum(len(v) for v in grouped.values())
    stages_js = _json.dumps(KANBAN_STAGES)
    modal_btns = "".join(
        f'<button class="kmove-modal__btn" data-stage="{s}">{STAGE_LABELS[s]}</button>'
        for s in KANBAN_STAGES
    )

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kanban — Scrappy</title>
<script src="https://cdn.jsdelivr.net/npm/sortablejs@1.15.3/Sortable.min.js"></script>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:"Red Hat Text",system-ui,sans-serif;background:#0d0d0d;color:#f0f0f0;height:100vh;display:flex;flex-direction:column;overflow:hidden}}
.ktop{{display:flex;align-items:center;gap:16px;padding:12px 20px;background:#161616;border-bottom:1px solid #2a2a2a;flex-shrink:0}}
.ktop h1{{font-size:16px;font-weight:600;color:#e94560}}
.ktop a{{color:#888;font-size:13px;text-decoration:none}}
.ktop a:hover{{color:#f0f0f0}}
.ktop .count{{margin-left:auto;font-size:13px;color:#555}}
.kboard{{display:flex;gap:16px;padding:16px;overflow-x:auto;flex:1;align-items:flex-start}}
.kcol{{background:#1a1a1a;border-radius:12px;width:300px;min-width:300px;display:flex;flex-direction:column;max-height:100%}}
.kcol__header{{display:flex;align-items:center;justify-content:space-between;padding:14px 16px 10px;border-bottom:1px solid #242424}}
.kcol__label{{font-size:13px;font-weight:600;color:#e94560;text-transform:uppercase;letter-spacing:.04em}}
.kcol__count{{font-size:12px;color:#555;background:#242424;border-radius:999px;padding:2px 8px}}
.kcol__cards{{list-style:none;padding:12px 8px 12px 12px;display:flex;flex-direction:column;gap:12px;overflow-y:auto;flex:1;min-height:120px}}
.kcol__empty{{color:#444;font-size:13px;text-align:center;padding:24px 0;border:2px dashed #2a2a2a;border-radius:8px;user-select:none}}
.kcard{{flex-shrink:0;background:#fff;border-radius:10px;overflow:hidden;cursor:grab;position:relative;border:1px solid #eeeef1;box-shadow:0 1px 4px rgba(0,0,0,.04);color:#0b0b0e}}
.kcard:active{{cursor:grabbing}}
.kcard__photo{{width:100%;height:160px;object-fit:cover;display:block}}
.kcard__nophoto{{width:100%;height:80px;background:#f5f5f5;display:flex;align-items:center;justify-content:center;color:#bbb;font-size:12px}}
.kcard__body{{padding:10px 12px 12px}}
.kcard__title{{font-size:13px;font-weight:600;line-height:1.3;margin-bottom:4px}}
.kcard__title a{{color:inherit;text-decoration:none}}
.kcard__title a:hover{{text-decoration:underline}}
.kcard__neigh{{font-size:11px;color:#888;margin-bottom:4px}}
.kcard__specs{{font-size:11px;color:#666;margin-bottom:6px}}
.kcard__price{{font-size:14px;font-weight:700;color:#0b0b0e}}
.kcard__move{{position:absolute;top:8px;left:8px;background:rgba(255,255,255,.9);border-radius:50%;width:28px;height:28px;display:flex;align-items:center;justify-content:center;font-size:14px;cursor:pointer;text-decoration:none;color:#333;z-index:10;opacity:0;transition:opacity .15s}}
.kcard:hover .kcard__move{{opacity:1}}
.kcard.sortable-ghost{{opacity:.35}}
.kcard.sortable-chosen{{box-shadow:0 8px 24px rgba(233,69,96,.25)}}
.kmove-modal{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:1000;align-items:center;justify-content:center}}
.kmove-modal.open{{display:flex}}
.kmove-modal__box{{background:#1e1e1e;border-radius:12px;padding:20px;min-width:240px}}
.kmove-modal__title{{font-size:14px;font-weight:600;color:#e94560;margin-bottom:12px}}
.kmove-modal__btn{{display:block;width:100%;text-align:left;background:#2a2a2a;border:none;border-radius:8px;padding:10px 14px;color:#f0f0f0;font-size:13px;cursor:pointer;margin-bottom:6px}}
.kmove-modal__btn:hover{{background:#333}}
.kmove-modal__cancel{{color:#666;font-size:12px;text-align:center;cursor:pointer;margin-top:8px;display:block}}
#kmap{{height:220px;flex-shrink:0;border-top:1px solid #2a2a2a}}
</style>
</head>
<body>
<div class="ktop">
  <h1>Scrappy Kanban</h1>
  <a href="/favorites">Vista clásica</a>
  <a href="/run" onclick="fetch('/run',{{method:'POST'}});return false;">Nuevo scrape</a>
  <span class="count">{total} propiedades</span>
</div>
<div class="kboard" id="kboard">
  {columns}
</div>
<div id="kmap"></div>

<div class="kmove-modal" id="kmove-modal">
  <div class="kmove-modal__box">
    <div class="kmove-modal__title">Mover a…</div>
    {modal_btns}
    <span class="kmove-modal__cancel" id="kmove-cancel">Cancelar</span>
  </div>
</div>

<script>
(function() {{
  var STAGES = {stages_js};
  var activeLid = null;

  STAGES.forEach(function(stage) {{
    var el = document.getElementById('cards-' + stage);
    if (!el) return;
    Sortable.create(el, {{
      group: 'listings',
      animation: 150,
      ghostClass: 'sortable-ghost',
      chosenClass: 'sortable-chosen',
      onEnd: function(evt) {{
        var lid = evt.item.dataset.listingId;
        var toStage = evt.to.dataset.stage;
        fetch('/api/stage', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{ listing_id: lid, stage: toStage }})
        }}).then(function(r) {{
          if (!r.ok) console.error('Stage move failed', r.status);
          else updateCounts();
        }});
      }}
    }});
  }});

  function updateCounts() {{
    STAGES.forEach(function(stage) {{
      var ul = document.getElementById('cards-' + stage);
      var hdr = document.querySelector('#kcol-' + stage + ' .kcol__count');
      if (ul && hdr) hdr.textContent = ul.querySelectorAll('.kcard').length;
    }});
  }}

  var modal = document.getElementById('kmove-modal');
  document.querySelectorAll('.kcard__move').forEach(function(btn) {{
    btn.addEventListener('click', function(e) {{
      e.preventDefault();
      activeLid = btn.dataset.lid;
      modal.classList.add('open');
    }});
  }});
  document.getElementById('kmove-cancel').addEventListener('click', function() {{
    modal.classList.remove('open');
  }});
  modal.querySelectorAll('.kmove-modal__btn').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      var stage = btn.dataset.stage;
      if (!activeLid) return;
      var card = document.querySelector('[data-listing-id="' + activeLid + '"]');
      var targetUl = document.getElementById('cards-' + stage);
      if (card && targetUl) {{ targetUl.prepend(card); updateCounts(); }}
      fetch('/api/stage', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{ listing_id: activeLid, stage: stage }})
      }});
      modal.classList.remove('open');
    }});
  }});

  var map = L.map('kmap').setView([19.42, -99.13], 12);
  L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    attribution: '© OpenStreetMap contributors', maxZoom: 19
  }}).addTo(map);
  document.querySelectorAll('.kcard[data-lat]').forEach(function(card) {{
    var lat = parseFloat(card.dataset.lat), lng = parseFloat(card.dataset.lng);
    if (!lat || !lng) return;
    L.circleMarker([lat, lng], {{radius: 7, color: '#e94560', fillOpacity: .7}})
      .bindPopup(card.querySelector('.kcard__title') ? card.querySelector('.kcard__title').textContent : '')
      .addTo(map);
  }});
}})();
</script>
</body>
</html>"""

    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp
