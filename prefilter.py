"""Reject listings via cheap URL/title/DB heuristics BEFORE paid API calls.

Order of checks (cheapest first):
  1. URL contains "amueblado" / "furnished" / "amoblado"  -> reject if criteria.furnished is False
  2. Neighborhood not in criteria.neighborhoods  -> reject (case-insensitive substring match)
  3. Address hash exists in DB with prior 'down' feedback  -> reject (don't re-show)
  4. Price outside criteria.price_min / price_max  -> reject
"""
import sqlite3
import unicodedata
from pathlib import Path

from scrapers.base import RawListing

_FURNISHED_TOKENS = ("amueblado", "amoblado", "furnished", "amueblada")


def _normalize(s: str) -> str:
    """Lowercase + strip diacritics for fuzzy neighborhood matching.

    Handles charset corruption (á → \ufffd) and accent mismatches
    (Juárez vs Juarez) from scraped HTML.
    """
    # Replace Unicode replacement characters and other mojibake before NFKD
    s = s.replace("\ufffd", "")
    nfkd = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


class PreFilter:
    def __init__(self, config: dict, db_path: str | Path | None = None):
        self.config = config or {}
        self.criteria = (self.config.get("criteria") or {}) if isinstance(self.config, dict) else {}
        self.db_path = Path(db_path) if db_path else None
        self._rejected_hashes: set[str] | None = None

    def _load_rejected_hashes(self) -> set[str]:
        if self._rejected_hashes is not None:
            return self._rejected_hashes
        if not self.db_path or not self.db_path.exists():
            self._rejected_hashes = set()
            return self._rejected_hashes
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                """
                SELECT DISTINCT l.address_hash
                FROM listings l JOIN feedback f ON f.listing_id = l.id
                WHERE f.signal = 'down'
                """
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            self._rejected_hashes = set()
            return self._rejected_hashes
        self._rejected_hashes = {r[0] for r in rows if r[0]}
        return self._rejected_hashes

    def _hood_ok(self, listing_hood: str) -> bool:
        wanted = [_normalize(h) for h in self.criteria.get("neighborhoods", []) or []]
        if not wanted:
            return True
        h = _normalize(listing_hood or "")
        if not h:
            return False
        return any(w in h or h in w for w in wanted if w)

    def _is_furnished_url(self, url: str) -> bool:
        u = (url or "").lower()
        return any(tok in u for tok in _FURNISHED_TOKENS)

    def _price_ok(self, price: int) -> bool:
        lo = self.criteria.get("price_min", 0)
        hi = self.criteria.get("price_max", 10**9)
        try:
            return lo <= int(price) <= hi
        except (TypeError, ValueError):
            return False

    def url_ok(self, url: str) -> bool:
        """Cheap URL-level pre-check (skip furnished search URLs entirely)."""
        if not self.criteria.get("furnished", False) and self._is_furnished_url(url):
            return False
        return True

    def filter(self, listings: list[RawListing]) -> list[RawListing]:
        rejected = self._load_rejected_hashes()
        out: list[RawListing] = []
        stats = {"furnished": 0, "out_of_area": 0, "prior_reject": 0, "price": 0}
        for l in listings:
            if not self.criteria.get("furnished", False) and self._is_furnished_url(l.url):
                stats["furnished"] += 1
                continue
            if not self._hood_ok(l.neighborhood):
                stats["out_of_area"] += 1
                continue
            if l.address_hash in rejected:
                stats["prior_reject"] += 1
                continue
            if not self._price_ok(l.price):
                stats["price"] += 1
                continue
            out.append(l)
        if any(stats.values()):
            print(f"[prefilter] dropped: {stats} \u00b7 kept {len(out)}/{len(listings)}")
        return out
