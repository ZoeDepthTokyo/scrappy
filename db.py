import json
import sqlite3
import paths
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    price INTEGER NOT NULL,
    bedrooms INTEGER,
    bathrooms INTEGER,
    floor INTEGER,
    sqm REAL,
    neighborhood TEXT,
    address TEXT,
    address_hash TEXT,
    title TEXT,
    latitude REAL,
    longitude REAL,
    photos_json TEXT,
    vision_score REAL,
    vision_flags TEXT,
    nice_to_haves TEXT,
    first_seen TEXT,
    last_seen TEXT,
    alerted INTEGER DEFAULT 0,
    dead_checks INTEGER DEFAULT 0,
    visit_status TEXT,
    visit_notes TEXT
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id TEXT NOT NULL,
    signal TEXT NOT NULL,
    reason TEXT,
    tags_json TEXT,
    parse_model TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pipeline_status (
    listing_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    note TEXT,
    stars INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    listing_id TEXT PRIMARY KEY,
    body TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
"""

VALID_SIGNALS: frozenset = frozenset({"up", "down", "reset"})

_ALLOWED_COLUMNS = frozenset(
    {
        "id",
        "source",
        "url",
        "price",
        "bedrooms",
        "bathrooms",
        "floor",
        "sqm",
        "neighborhood",
        "address",
        "address_hash",
        "title",
        "latitude",
        "longitude",
        "photos_json",
        "vision_score",
        "vision_flags",
        "nice_to_haves",
        "first_seen",
        "last_seen",
        "alerted",
        "dead_checks",
    }
)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Apply SCHEMA to an existing connection. Idempotent."""
    conn.executescript(SCHEMA)


def connect(path: str | None = None) -> sqlite3.Connection:
    target = path if path is not None else paths.db_path()
    if path is None:
        target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.executescript(SCHEMA)
    # Safety-net migrations for DBs created before the columns were added to SCHEMA.
    # Each ALTER TABLE is a no-op if the column already exists (OperationalError = already present).
    _migrations = [
        "ALTER TABLE listings ADD COLUMN sqm REAL",
        "ALTER TABLE listings ADD COLUMN dead_checks INTEGER DEFAULT 0",
        "ALTER TABLE listings ADD COLUMN visit_status TEXT",
        "ALTER TABLE listings ADD COLUMN visit_notes TEXT",
        "ALTER TABLE listings ADD COLUMN address TEXT",
        "ALTER TABLE listings ADD COLUMN title TEXT",
        "ALTER TABLE listings ADD COLUMN latitude REAL",
        "ALTER TABLE listings ADD COLUMN longitude REAL",
        "ALTER TABLE feedback ADD COLUMN tags_json TEXT",
        "ALTER TABLE feedback ADD COLUMN parse_model TEXT",
        "ALTER TABLE pipeline_status ADD COLUMN order_rank INTEGER",
        "ALTER TABLE pipeline_status ADD COLUMN kanban_stage TEXT",
    ]
    for stmt in _migrations:
        try:
            conn.execute(stmt)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
    if conn.execute("PRAGMA user_version").fetchone()[0] == 0:
        conn.execute("PRAGMA user_version = 1")
    conn.commit()
    return conn


def get_shortlist(conn: sqlite3.Connection) -> list[dict]:
    """Return listings whose latest feedback is positive, with visit status."""
    cur = conn.execute("""
        SELECT l.id, l.neighborhood, l.price, l.bedrooms, l.bathrooms,
               l.floor, l.sqm, l.url, l.visit_status, l.visit_notes, f.reason
        FROM listings l
        JOIN feedback f ON l.id = f.listing_id
        WHERE f.signal = 'up'
          AND f.id = (
              SELECT MAX(f2.id)
              FROM feedback f2
              WHERE f2.listing_id = f.listing_id
          )
        ORDER BY l.price ASC
    """)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def update_visit_status(
    conn: sqlite3.Connection,
    listing_id: str,
    status: str,
    notes: str = "",
    *,
    commit: bool = True,
) -> bool:
    """Update visit_status and notes for a listing. Returns True if listing found."""
    result = conn.execute(
        "UPDATE listings SET visit_status=?, visit_notes=? WHERE id=?",
        (status, notes, listing_id),
    )
    if commit:
        conn.commit()
    return result.rowcount > 0


def upsert_listing(
    conn: sqlite3.Connection,
    listing: dict,
    *,
    commit: bool = True,
) -> None:
    unknown = set(listing.keys()) - _ALLOWED_COLUMNS
    if unknown:
        raise ValueError(f"upsert_listing: unknown columns {unknown}")

    # Build update SET clause — everything except id and first_seen
    update_cols = [k for k in listing.keys() if k not in ("id", "first_seen")]

    cols = ", ".join(listing.keys())
    placeholders = ", ".join("?" * len(listing))
    update_clause = ", ".join(f"{c}=excluded.{c}" for c in update_cols)

    conn.execute(
        f"INSERT INTO listings ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {update_clause}",
        list(listing.values()),
    )
    if commit:
        conn.commit()


def save_feedback(
    conn: sqlite3.Connection,
    listing_id: str,
    signal: str,
    reason: str = "",
    tags: list | None = None,
    parse_model: str | None = None,
    *,
    commit: bool = True,
) -> None:
    # Dedup: skip if the most recent row for this listing has identical signal + reason.
    # Prevents duplicate writes from retried requests without blocking signal changes.
    existing = conn.execute(
        "SELECT signal, reason FROM feedback WHERE listing_id=? ORDER BY id DESC LIMIT 1",
        (listing_id,),
    ).fetchone()
    if existing and existing[0] == signal and (existing[1] or "") == (reason or ""):
        return
    conn.execute(
        "INSERT INTO feedback (listing_id, signal, reason, tags_json, parse_model, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            listing_id,
            signal,
            reason,
            json.dumps(tags) if tags is not None else None,
            parse_model,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    if commit:
        conn.commit()


def get_unalerted(conn: sqlite3.Connection) -> list[dict]:
    """Return listings with a vision_score that have not yet been alerted.
    Listings without a vision_score are excluded — run the vision scorer first.
    """
    cur = conn.execute(
        "SELECT * FROM listings WHERE alerted=0 AND vision_score IS NOT NULL ORDER BY vision_score DESC"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def mark_alerted(
    conn: sqlite3.Connection,
    listing_id: str,
    *,
    commit: bool = True,
) -> None:
    conn.execute("UPDATE listings SET alerted=1 WHERE id=?", (listing_id,))
    if commit:
        conn.commit()


def upsert_pipeline_status(
    conn: sqlite3.Connection,
    listing_id: str,
    status: str | None = None,
    note: str | None = None,
    stars: int | None = None,
    order_rank: int | None = None,
    *,
    commit: bool = True,
) -> None:
    """Upsert pipeline status for a listing (starred, scheduled, visited, etc.).

    Any field omitted as None preserves the existing stored value. For a new row,
    omitted values fall back to status='none', note='', and stars=0.
    """
    existing = conn.execute(
        "SELECT status, note, stars, order_rank FROM pipeline_status WHERE listing_id=?",
        (listing_id,),
    ).fetchone()
    merged_status = status if status is not None else (existing[0] if existing else "none")
    merged_note = note if note is not None else (existing[1] if existing else "")
    merged_stars = stars if stars is not None else (existing[2] if existing else 0)
    merged_order_rank = order_rank if order_rank is not None else (existing[3] if existing else None)
    conn.execute(
        "INSERT INTO pipeline_status (listing_id, status, note, stars, order_rank, updated_at) "
        "VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(listing_id) DO UPDATE SET "
        "status=excluded.status, note=excluded.note, stars=excluded.stars, "
        "order_rank=excluded.order_rank, "
        "updated_at=excluded.updated_at",
        (
            listing_id,
            merged_status,
            merged_note,
            merged_stars,
            merged_order_rank,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    if commit:
        conn.commit()


def get_pipeline_status(
    conn: sqlite3.Connection,
    listing_id: str,
) -> dict | None:
    """Return pipeline status for a listing, or None if not set."""
    cur = conn.execute(
        "SELECT listing_id, status, note, stars, updated_at FROM pipeline_status WHERE listing_id=?",
        (listing_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip(["listing_id", "status", "note", "stars", "updated_at"], row))


def get_all_pipeline_statuses(
    conn: sqlite3.Connection,
) -> dict[str, dict]:
    """Return all pipeline statuses keyed by listing_id."""
    cur = conn.execute(
        "SELECT listing_id, status, note, stars, updated_at FROM pipeline_status"
    )
    return {
        row[0]: dict(zip(["listing_id", "status", "note", "stars", "updated_at"], row))
        for row in cur.fetchall()
    }


def upsert_note(conn: sqlite3.Connection, listing_id: str, body: str) -> None:
    """Upsert a free-text note for a listing (cockpit-side persistence)."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO notes (listing_id, body, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(listing_id) DO UPDATE SET "
        "body=excluded.body, updated_at=excluded.updated_at",
        (listing_id, body, now),
    )
    conn.commit()


def get_feedback_examples(
    conn: sqlite3.Connection,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Returns (pos_ids, neg_ids, pos_tags, neg_tags) for vision prompt + TasteProfile."""
    up_rows = conn.execute(
        "SELECT listing_id, tags_json FROM feedback "
        "WHERE signal='up' ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
    down_rows = conn.execute(
        "SELECT listing_id, tags_json FROM feedback "
        "WHERE signal='down' ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
    pos_ids = [r[0] for r in up_rows]
    neg_ids = [r[0] for r in down_rows]
    pos_tags: list[str] = list({t for r in up_rows if r[1] for t in json.loads(r[1])})
    neg_tags: list[str] = list({t for r in down_rows if r[1] for t in json.loads(r[1])})
    return pos_ids, neg_ids, pos_tags, neg_tags
