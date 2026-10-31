"""Shared HTTP fetcher + JSON-LD / microdata extractors.

Replaces Firecrawl's `extract` endpoint for sites that serve JSON-LD or
schema.org microdata in initial HTML. For sites that serve empty HTML only
under default UA, HttpxAdapter rotates browser-real UA headers and falls back
to curl_cffi (Chrome TLS fingerprint impersonation) on HTTP 403.

Self-improvement hook
---------------------
Each fetch records a row in a per-adapter health log. Call
`HttpxAdapter.write_health_log()` (or `flush_health_log()`) to persist the
accumulated rows to `data/scraper_health.json` for future runs to consult.
"""
import json
import os
import random
import re
import time
from pathlib import Path
from typing import Iterable

import httpx
import paths

try:  # optional — present on Windows/Py3.14 per 2026-04-14 env check
    from curl_cffi import requests as curl_requests  # type: ignore

    _CURL_CFFI_AVAILABLE = True
except Exception:  # pragma: no cover
    curl_requests = None  # type: ignore
    _CURL_CFFI_AVAILABLE = False


# Rotating pool of recent real-browser UAs. Updated 2026-04 (Chrome 131+, FF 127+).
UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

_BROWSER_REAL_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
    "DNT": "1",
    "Connection": "keep-alive",
}


def _pick_ua() -> str:
    return random.choice(UA_POOL)


def _jsonld_block_count(html: str) -> int:
    if not html:
        return 0
    return len(re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\']',
        html,
        re.IGNORECASE,
    ))


class HttpxAdapter:
    """Per-source fetcher with UA rotation and curl_cffi 403 fallback.

    Parameters
    ----------
    timeout : float
        Per-request timeout in seconds (default 15; use 60s for slow SSR sites
        like propiedades.com).
    headers : dict | None
        Extra headers merged on top of browser-real defaults.
    source_name : str
        Label used in health log rows (e.g. "vivanuncios"). Optional.
    rotate_ua : bool
        Pick a fresh UA from UA_POOL on every request. Default True.
    use_curl_cffi_on_403 : bool
        If True and `curl_cffi` is installed, retry via Chrome-TLS fingerprint
        when the primary httpx request returns 403. Default True.
    """


    # Class-level accumulator so multiple adapter instances in a single run
    # flush to the same file.
    _health_rows: list[dict] = []

    def __init__(
        self,
        timeout: float = 15.0,
        headers: dict | None = None,
        source_name: str = "",
        rotate_ua: bool = True,
        use_curl_cffi_on_403: bool = True,
        retry_curl_cffi_on_error: bool = False,
        retry_curl_cffi_below_bytes: int = 0,
    ):
        self.timeout = timeout
        self.source_name = source_name
        self.rotate_ua = rotate_ua
        self.use_curl_cffi_on_403 = use_curl_cffi_on_403 and _CURL_CFFI_AVAILABLE
        # Retry with curl_cffi when httpx raises (e.g. TCP-level timeout/hang)
        self.retry_curl_cffi_on_error = retry_curl_cffi_on_error and _CURL_CFFI_AVAILABLE
        # Retry with curl_cffi when 200 response is suspiciously small (anti-bot skeleton)
        self.retry_curl_cffi_below_bytes = retry_curl_cffi_below_bytes
        self._extra_headers = dict(headers or {})
        self.client = httpx.Client(
            timeout=timeout,
            headers=self._build_headers(),
            follow_redirects=True,
        )

    # ---- header helpers ---------------------------------------------------

    def _build_headers(self) -> dict:
        merged = dict(_BROWSER_REAL_HEADERS)
        merged["User-Agent"] = _pick_ua() if self.rotate_ua else UA_POOL[0]
        merged.update(self._extra_headers)
        return merged

    # ---- fetch ------------------------------------------------------------

    def fetch_html(self, url: str) -> str:
        """Fetch URL, returning text on 200 or "" on any failure.

        Records a health-log row with status_code, bytes, elapsed, jsonld count,
        and which transport served the response.
        """
        transport = "httpx"
        status = 0
        text = ""
        err = ""
        t0 = time.monotonic()
        try:
            # rotate UA per request
            if self.rotate_ua:
                self.client.headers["User-Agent"] = _pick_ua()
            r = self.client.get(url)
            status = r.status_code
            if status == 200:
                text = r.text
                # Skeleton/anti-bot page: 200 but suspiciously small — retry via curl_cffi
                if (
                    self.retry_curl_cffi_below_bytes > 0
                    and len(text) < self.retry_curl_cffi_below_bytes
                    and _CURL_CFFI_AVAILABLE
                ):
                    curl_text, curl_status, curl_transport = self._curl_cffi_fetch(url)
                    if curl_text and len(curl_text) > len(text):
                        text, status, transport = curl_text, curl_status, curl_transport
            else:
                print(f"[httpx] {url} -> HTTP {status}")
                if status == 403 and self.use_curl_cffi_on_403:
                    text, status, transport = self._curl_cffi_fetch(url)
        except httpx.HTTPError as e:
            err = f"{type(e).__name__}: {e}"
            print(f"[httpx] {url} -> {err}")
            if self.retry_curl_cffi_on_error:
                text, status, transport = self._curl_cffi_fetch(url)
                err = "" if text else err
        finally:
            elapsed = time.monotonic() - t0
            self._record(
                url=url,
                status=status,
                bytes_=len(text),
                elapsed=elapsed,
                transport=transport,
                error=err,
                jsonld_blocks=_jsonld_block_count(text),
            )
        return text

    def _curl_cffi_fetch(self, url: str) -> tuple[str, int, str]:
        """Retry via curl_cffi with Chrome-131 TLS impersonation."""
        if curl_requests is None:
            return "", 0, "curl_cffi_unavailable"
        try:
            headers = self._build_headers()
            r = curl_requests.get(
                url,
                headers=headers,
                timeout=self.timeout,
                impersonate="chrome131",
                allow_redirects=True,
            )
            print(f"[curl_cffi] {url} -> HTTP {r.status_code}")
            if r.status_code == 200:
                return r.text, r.status_code, "curl_cffi"
            return "", r.status_code, "curl_cffi"
        except Exception as e:  # pragma: no cover - network-dependent
            print(f"[curl_cffi] {url} -> {type(e).__name__}: {e}")
            return "", 0, "curl_cffi_error"

    # ---- health log -------------------------------------------------------

    def _record(
        self,
        url: str,
        status: int,
        bytes_: int,
        elapsed: float,
        transport: str,
        error: str,
        jsonld_blocks: int,
    ) -> None:
        HttpxAdapter._health_rows.append(
            {
                "source": self.source_name,
                "url": url,
                "status": status,
                "bytes": bytes_,
                "elapsed_s": round(elapsed, 3),
                "transport": transport,
                "error": error,
                "jsonld_blocks": jsonld_blocks,
                "ts": time.time(),
            }
        )

    @classmethod
    def write_health_log(cls, path: os.PathLike | None = None) -> Path:
        """Flush accumulated health rows to JSON, merging with any prior run."""
        target = Path(path) if path else paths.data_dir() / "scraper_health.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        prior: list[dict] = []
        if target.exists():
            try:
                prior = json.loads(target.read_text(encoding="utf-8")) or []
                if not isinstance(prior, list):
                    prior = []
            except (json.JSONDecodeError, OSError):
                prior = []
        merged = prior + cls._health_rows
        # Cap at last 2000 rows to keep the file bounded.
        merged = merged[-2000:]
        target.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        cls._health_rows = []
        return target

    flush_health_log = write_health_log  # alias

    def close(self):
        self.client.close()


# ---- JSON-LD extractor (unchanged public API) ----------------------------


class JsonLdExtractor:
    """Parse <script type='application/ld+json'> blocks into a flat list of items."""

    _BLOCK_RE = re.compile(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.DOTALL | re.IGNORECASE,
    )
    _ALLOWED_TYPES = {"Apartment", "House", "Product", "RealEstateListing", "Residence"}

    @classmethod
    def extract(cls, html: str) -> list[dict]:
        out: list[dict] = []
        for raw in cls._BLOCK_RE.findall(html):
            try:
                data = json.loads(raw.strip())
            except (json.JSONDecodeError, ValueError):
                continue
            out.extend(cls._flatten(data))
        return out

    @classmethod
    def _flatten(cls, data) -> Iterable[dict]:
        if isinstance(data, list):
            for d in data:
                yield from cls._flatten(d)
            return
        if not isinstance(data, dict):
            return
        t = data.get("@type", "")
        if isinstance(t, list):
            t = t[0] if t else ""
        if t in cls._ALLOWED_TYPES:
            yield data
            return
        if t == "ItemList":
            for e in data.get("itemListElement", []):
                if isinstance(e, dict):
                    item = e.get("item", e)
                    if isinstance(item, dict):
                        yield from cls._flatten(item)
            return
        # SearchResultsPage (OLX/Vivanuncios) wraps listings in mainEntity
        if "mainEntity" in data:
            yield from cls._flatten(data["mainEntity"])
            return
        if "@graph" in data:
            yield from cls._flatten(data["@graph"])


CURL_CFFI_AVAILABLE = _CURL_CFFI_AVAILABLE
