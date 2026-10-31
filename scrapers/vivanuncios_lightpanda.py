"""Vivanuncios Lightpanda scraper — headless JS rendering via WSL2 Ubuntu.

T3 fallback when httpx + curl_cffi (VivanunciosNative) cannot render JS-heavy pages.
Calls lightpanda fetch via WSL2 subprocess — stateless, no CDP server required.

Command executed:
  wsl -d Ubuntu -- bash -c "~/lightpanda fetch --dump html --wait-until networkidle '<url>'"
"""
import subprocess
from urllib.parse import quote_plus

from scrapers.base import BaseScraper, RawListing
from scrapers.httpx_adapter import JsonLdExtractor
from scrapers.microdata_extractor import MicrodataExtractor
from scrapers.vivanuncios_native import _parse_olx_next_data
from scrapers.vivanuncios_base import VivanunciosMixin


class VivanunciosLightpanda(VivanunciosMixin, BaseScraper):
    _TIMEOUT = 45  # seconds — lightpanda + WSL startup overhead

    def _fetch_html(self, url: str) -> str:
        """Render URL with Lightpanda (WSL2 Ubuntu) and return HTML stdout."""
        # Single-quote the URL inside the bash -c string.
        # URLs from _search_urls contain no single-quotes.
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
                timeout=self._TIMEOUT,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                print(
                    f"[vivanuncios-lp] lightpanda error (rc={result.returncode}): "
                    f"{result.stderr[:300]}"
                )
                return ""
            return result.stdout
        except subprocess.TimeoutExpired:
            print(f"[vivanuncios-lp] timeout ({self._TIMEOUT}s) fetching {url}")
            return ""
        except FileNotFoundError:
            print("[vivanuncios-lp] wsl not found — WSL2 unavailable on this host")
            return ""
        except Exception as e:
            print(f"[vivanuncios-lp] subprocess error: {e}")
            return ""

    def fetch_listings(self) -> list[RawListing]:
        out: list[RawListing] = []
        crit = self.config.get("criteria", {})
        hoods = crit.get("neighborhoods", [])
        for i, url in enumerate(self._search_urls()):
            hood_name = hoods[i] if i < len(hoods) else None
            html = self._fetch_html(url)
            if not html:
                continue
            items = JsonLdExtractor.extract(html)
            parsed = self._parse_extract(items, fallback_hood=hood_name) if items else []
            if not parsed:
                parsed = _parse_olx_next_data(html, self.SOURCE_NAME, fallback_hood=hood_name)
            if not parsed:
                items = MicrodataExtractor.extract(html)
                parsed = self._parse_extract(items, fallback_hood=hood_name) if items else []
            out.extend(parsed)
            print(f"[vivanuncios-lp] {url.split('q=')[-1]}: {len(parsed)} listings")
        return out
