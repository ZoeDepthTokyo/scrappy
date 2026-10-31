import json
from datetime import date
from html import escape


def _e(v) -> str:
    return escape(str(v if v is not None else ""), quote=True)


def generate_html(listings: list[dict], cadence_days: int = 1) -> str:
    today = date.today().isoformat()
    neighborhoods = sorted(
        {l.get("neighborhood", "") for l in listings if l.get("neighborhood")}
    )
    cards = "".join(_card(l) for l in listings)
    filter_btns = "".join(
        f'<button class="filter-btn" onclick="filterBy(\'{_e(n)}\')">{_e(n)}</button>'
        for n in neighborhoods
    )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scrappy · {today}</title>
<style>
  body {{ font-family: system-ui; background: #1a1a2e; color: #e0e0e0; margin: 0; padding: 20px; }}
  .header {{ background: #16213e; padding: 20px; border-radius: 12px; margin-bottom: 20px; }}
  h1 {{ margin: 0 0 12px; color: #e94560; }}
  .cadence-bar {{ display: flex; gap: 8px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }}
  .cadence-btn {{ padding: 6px 14px; border: 2px solid #e94560; border-radius: 20px;
                  background: transparent; color: #e0e0e0; cursor: pointer; }}
  .cadence-btn.active {{ background: #e94560; color: white; }}
  .filter-bar {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 20px; }}
  .filter-btn {{ padding: 6px 14px; border: 1px solid #444; border-radius: 20px;
                  background: #16213e; color: #e0e0e0; cursor: pointer; }}
  .filter-btn.active {{ border-color: #e94560; color: #e94560; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 20px; }}
  .card {{ background: #16213e; border-radius: 12px; overflow: hidden; }}
  .photos {{ display: flex; gap: 4px; height: 160px; overflow: hidden; }}
  .photos img {{ flex: 1; object-fit: cover; min-width: 0; }}
  .card-body {{ padding: 16px; }}
  .price {{ font-size: 1.4em; font-weight: bold; color: #e94560; }}
  .meta {{ color: #aaa; font-size: 0.85em; margin: 4px 0 8px; }}
  .score-bar {{ background: #0f3460; border-radius: 4px; height: 8px; margin: 8px 0; }}
  .score-fill {{ background: #e94560; height: 8px; border-radius: 4px; }}
  .badges {{ display: flex; gap: 6px; flex-wrap: wrap; margin: 8px 0; font-size: 0.82em; }}
  .badge {{ background: #0f3460; padding: 2px 8px; border-radius: 10px; }}
  .actions {{ display: flex; gap: 8px; margin-top: 12px; }}
  .btn {{ padding: 8px 16px; border-radius: 8px; border: none; cursor: pointer; font-size: 0.9em; }}
  .btn-view {{ background: #0f3460; color: white; text-decoration: none; }}
  .btn-up {{ background: #1a472a; color: white; }}
  .btn-down {{ background: #4a1942; color: white; }}
  .btn:disabled {{ opacity: 0.5; cursor: default; }}
  @media (max-width: 700px) {{
    body {{ padding: 10px; }}
    .grid {{ grid-template-columns: 1fr; }}
    .cadence-bar {{ flex-wrap: wrap; }}
  }}
</style>
</head>
<body>
<div class="header">
  <h1>Scrappy · {today} · {len(listings)} listings</h1>
  <div class="cadence-bar">
    <span>Cadence:</span>
    <button class="cadence-btn {"active" if cadence_days == 1 else ""}" onclick="setCadence(1)">Daily</button>
    <button class="cadence-btn {"active" if cadence_days == 3 else ""}" onclick="setCadence(3)">Every 3 days</button>
    <button class="cadence-btn {"active" if cadence_days == 7 else ""}" onclick="setCadence(7)">Weekly</button>
    <button class="cadence-btn" onclick="triggerRun()" style="margin-left:12px">▶ Run Now</button>
  </div>
</div>
<div class="filter-bar">
  <button class="filter-btn active" onclick="filterBy('all')">All</button>
  {filter_btns}
</div>
<div class="grid" id="grid">{cards}</div>
<script>
function filterBy(n) {{
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  document.querySelectorAll('.card').forEach(c => {{
    c.style.display = (n==='all' || c.dataset.neighborhood===n) ? '' : 'none';
  }});
}}
function setCadence(days) {{
  fetch('http://localhost:5055/cadence', {{method:'POST',
    headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{days}})
  }}).then(() => document.querySelectorAll('.cadence-btn').forEach((b,i) =>
    b.classList.toggle('active', [1,3,7][i]===days)));
}}
function sendFeedback(id, signal) {{
  const reason = signal==='down' ? (prompt('Reason? (optional)') || '') : '';
  fetch('http://localhost:5055/feedback', {{method:'POST',
    headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{listing_id:id, signal, reason}})
  }}).then(() => {{
    document.getElementById('up-'+id).disabled = true;
    document.getElementById('down-'+id).disabled = true;
    document.getElementById(signal+'-'+id).style.opacity = '1';
  }});
}}
function triggerRun() {{
  fetch('http://localhost:5055/run', {{method:'POST'}})
    .then(() => alert('Run triggered — refresh in ~5 minutes'));
}}
</script>
</body>
</html>"""


def _card(l: dict) -> str:
    try:
        photos = json.loads(l.get("photos_json") or "[]")
    except (ValueError, TypeError):
        photos = []
    try:
        flags = json.loads(l.get("vision_flags") or "[]")
    except (ValueError, TypeError):
        flags = []
    try:
        nth = json.loads(l.get("nice_to_haves") or "{}")
    except (ValueError, TypeError):
        nth = {}
    score = l.get("vision_score") or 0
    photo_html = "".join(f'<img src="{_e(p)}" loading="lazy">' for p in photos[:3])
    badge_html = ""
    if nth.get("washer_dryer"):
        badge_html += '<span class="badge">🧺 Washer/dryer</span>'
    if nth.get("fridge"):
        badge_html += '<span class="badge">🧊 Fridge</span>'
    if nth.get("parking"):
        badge_html += '<span class="badge">🚗 Parking</span>'
    for f in flags[:2]:
        badge_html += f'<span class="badge">{_e(str(f).replace("_", " "))}</span>'
    lid = _e(l.get("id", ""))
    price = l.get("price", 0) or 0
    try:
        price_str = f"{int(price):,}"
    except (ValueError, TypeError):
        price_str = _e(price)
    return f"""
<div class="card" data-neighborhood="{_e(l.get("neighborhood", ""))}">
  <div class="photos">{photo_html}</div>
  <div class="card-body">
    <div class="price">${price_str} MXN</div>
    <div class="meta">{_e(l.get("neighborhood", ""))} · Floor {_e(l.get("floor", "?"))} · {_e(l.get("source", ""))}</div>
    <div class="score-bar"><div class="score-fill" style="width:{min(score * 10, 100):.0f}%"></div></div>
    <div style="font-size:0.8em;color:#aaa">Vision score: {score:.1f}/10</div>
    <div class="badges">{badge_html}</div>
    <div class="actions">
      <a class="btn btn-view" href="{_e(l.get("url", "#"))}" target="_blank" rel="noopener noreferrer">View ↗</a>
      <button class="btn btn-up" id="up-{lid}" onclick="sendFeedback('{lid}','up')">👍 Good</button>
      <button class="btn btn-down" id="down-{lid}" onclick="sendFeedback('{lid}','down')">👎 Pass</button>
    </div>
  </div>
</div>"""
