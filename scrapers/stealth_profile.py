# scrapers/stealth_profile.py
"""Coordinated browser fingerprint profiles for ML authenticated scraping.

A profile is a bundle of signals that ML (and other anti-bot vendors) cross-
reference: UA must match viewport must match timezone must match platform.
A scrambled fingerprint is its own signature.

These profiles are ALL Mexico City-oriented — the scraper user lives in CDMX,
so a timezone of America/Mexico_City + Spanish-MX locale is consistent with
a real session. Using Pacific timezone would itself flag the session.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class StealthProfile:
    user_agent: str
    viewport: dict
    timezone_id: str
    accept_language: str
    platform: str

    def to_playwright_context_args(self) -> dict:
        return {
            "user_agent": self.user_agent,
            "viewport": self.viewport,
            "locale": "es-MX",
            "timezone_id": self.timezone_id,
            "extra_http_headers": {"Accept-Language": self.accept_language},
        }

    @classmethod
    def pick(cls, seed: str = "") -> "StealthProfile":
        if not seed:
            return PROFILE_POOL[0]
        idx = int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(PROFILE_POOL)
        return PROFILE_POOL[idx]


PROFILE_POOL: list[StealthProfile] = [
    StealthProfile(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1920, "height": 1080},
        timezone_id="America/Mexico_City",
        accept_language="es-MX,es;q=0.9,en;q=0.8",
        platform="Win32",
    ),
    StealthProfile(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1440, "height": 900},
        timezone_id="America/Mexico_City",
        accept_language="es-MX,es;q=0.9,en;q=0.6",
        platform="MacIntel",
    ),
    StealthProfile(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) "
            "Gecko/20100101 Firefox/127.0"
        ),
        viewport={"width": 1536, "height": 864},
        timezone_id="America/Mexico_City",
        accept_language="es-MX,es;q=0.9",
        platform="Win32",
    ),
    StealthProfile(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0"
        ),
        viewport={"width": 1680, "height": 1050},
        timezone_id="America/Mexico_City",
        accept_language="es-MX,es;q=0.9,en-US;q=0.7",
        platform="Win32",
    ),
]
