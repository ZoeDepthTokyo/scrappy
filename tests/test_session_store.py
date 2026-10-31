# tests/test_session_store.py
import json
from pathlib import Path
import pytest

from scrapers.session_store import SessionStore, SessionHealth


def _storage_state(cookies=None, origins=None):
    """Minimal Playwright storage_state blob shape."""
    return {
        "cookies": cookies or [],
        "origins": origins or [],
    }


def test_save_and_load_roundtrip(tmp_path):
    store = SessionStore(tmp_path / "s.json")
    blob = _storage_state(
        cookies=[
            {"name": "orguseridp", "value": "abc", "domain": ".mercadolibre.com.mx",
             "path": "/", "expires": 9999999999, "httpOnly": True, "secure": True,
             "sameSite": "Lax"},
        ]
    )
    store.save(blob)
    loaded = store.load()
    assert loaded["cookies"][0]["name"] == "orguseridp"


def test_cookie_header_serialization(tmp_path):
    store = SessionStore(tmp_path / "s.json")
    store.save(_storage_state(cookies=[
        {"name": "a", "value": "1", "domain": ".mercadolibre.com.mx", "path": "/"},
        {"name": "b", "value": "2", "domain": ".mercadolibre.com.mx", "path": "/"},
    ]))
    header = store.cookie_header()
    assert header == "a=1; b=2"


def test_cookie_header_filters_other_domains(tmp_path):
    store = SessionStore(tmp_path / "s.json")
    store.save(_storage_state(cookies=[
        {"name": "a", "value": "1", "domain": ".mercadolibre.com.mx", "path": "/"},
        {"name": "b", "value": "2", "domain": ".example.com", "path": "/"},
    ]))
    header = store.cookie_header()
    assert "a=1" in header
    assert "b=2" not in header


def test_health_missing_file(tmp_path):
    store = SessionStore(tmp_path / "missing.json")
    assert store.health() == SessionHealth.MISSING


def test_health_empty_cookies(tmp_path):
    store = SessionStore(tmp_path / "s.json")
    store.save(_storage_state(cookies=[]))
    assert store.health() == SessionHealth.EMPTY


def test_health_expired(tmp_path):
    import time
    store = SessionStore(tmp_path / "s.json")
    store.save(_storage_state(cookies=[
        {"name": "a", "value": "1", "domain": ".mercadolibre.com.mx",
         "path": "/", "expires": time.time() - 10},
    ]))
    assert store.health() == SessionHealth.EXPIRED


def test_health_ok(tmp_path):
    import time
    store = SessionStore(tmp_path / "s.json")
    store.save(_storage_state(cookies=[
        {"name": "a", "value": "1", "domain": ".mercadolibre.com.mx",
         "path": "/", "expires": time.time() + 86400},
    ]))
    assert store.health() == SessionHealth.OK
