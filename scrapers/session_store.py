# scrapers/session_store.py
"""Persist + validate the Playwright storage-state used by the ML scraper.

A Playwright `storage_state` blob looks like:
    {
        "cookies": [ { "name": "...", "value": "...", "domain": "...",
                        "path": "/", "expires": 1234567890, ... }, ... ],
        "origins": [ { "origin": "https://...", "localStorage": [...] }, ... ],
    }

This module is intentionally small: save / load / filter cookies / report health.
No scraping logic lives here.
"""
from __future__ import annotations

import enum
import json
import time
from pathlib import Path


class SessionHealth(enum.Enum):
    OK = "ok"
    MISSING = "missing"       # file does not exist
    EMPTY = "empty"           # file exists but no cookies
    EXPIRED = "expired"       # every ML cookie has an `expires` in the past


ML_DOMAIN_SUFFIX = ".mercadolibre.com.mx"


class SessionStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    # ---- persistence -----------------------------------------------------

    def save(self, storage_state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(storage_state), encoding="utf-8")

    def load(self) -> dict:
        if not self.path.exists():
            return {"cookies": [], "origins": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    # ---- read paths ------------------------------------------------------

    def _ml_cookies(self) -> list[dict]:
        data = self.load()
        return [
            c for c in data.get("cookies", [])
            if ML_DOMAIN_SUFFIX in (c.get("domain") or "")
        ]

    def cookie_header(self) -> str:
        return "; ".join(f"{c['name']}={c['value']}" for c in self._ml_cookies())

    def cookies_for_httpx(self) -> dict[str, str]:
        return {c["name"]: c["value"] for c in self._ml_cookies()}

    # ---- health ----------------------------------------------------------

    def health(self) -> SessionHealth:
        if not self.path.exists():
            return SessionHealth.MISSING
        ml = self._ml_cookies()
        if not ml:
            return SessionHealth.EMPTY
        now = time.time()
        if all((c.get("expires") or 0) and (c.get("expires") or 0) < now for c in ml):
            return SessionHealth.EXPIRED
        return SessionHealth.OK
