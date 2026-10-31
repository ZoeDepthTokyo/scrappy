"""Schema.org microdata extractor — fallback when JSON-LD is absent.

Parses HTML with itemscope/itemtype/itemprop attributes into dicts that mirror
the JSON-LD shape consumed by BaseScraper._parse_extract().

Supported top-level types: Apartment, House, Product, RealEstateListing,
Residence (matches JsonLdExtractor._ALLOWED_TYPES). itemtype URLs may use
http:// or https:// and may have a trailing slash.
"""
from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup, Tag

_ALLOWED_TYPES = {
    "Apartment",
    "House",
    "Product",
    "RealEstateListing",
    "Residence",
}


def _itemtype_tail(val: str | list[str] | None) -> str:
    if not val:
        return ""
    if isinstance(val, list):
        val = val[0] if val else ""
    return val.rstrip("/").rsplit("/", 1)[-1]


def _prop_value(tag: Tag) -> Any:
    """Resolve a single itemprop value — recurse if it's a nested itemscope."""
    if tag.has_attr("itemscope"):
        return _parse_item(tag)
    name = tag.name.lower()
    if name == "meta":
        return tag.get("content", "").strip()
    if name in ("a", "link"):
        return (tag.get("href") or "").strip()
    if name in ("img", "audio", "embed", "iframe", "source", "track", "video"):
        return (tag.get("src") or "").strip()
    if name == "object":
        return (tag.get("data") or "").strip()
    if name in ("data", "meter"):
        return (tag.get("value") or tag.get_text(strip=True)).strip()
    if name == "time":
        return (tag.get("datetime") or tag.get_text(strip=True)).strip()
    return tag.get_text(strip=True)


def _parse_item(scope: Tag) -> dict:
    """Walk an itemscope subtree, returning its itemprops as a dict.

    Multiple itemprops with the same name collapse into a list.
    Nested itemscopes become nested dicts.
    """
    out: dict[str, Any] = {}
    # Walk descendants but don't descend into OTHER itemscope subtrees
    # (other than when we hit them via an itemprop).
    for desc in scope.descendants:
        if not isinstance(desc, Tag):
            continue
        if not desc.has_attr("itemprop"):
            continue
        # Skip props that belong to a deeper itemscope unrelated to us
        parent = desc.parent
        found_other_scope = False
        while parent is not None and parent is not scope:
            if isinstance(parent, Tag) and parent.has_attr("itemscope") and parent is not scope:
                found_other_scope = True
                break
            parent = parent.parent
        if found_other_scope:
            continue
        for prop in desc["itemprop"].split():
            val = _prop_value(desc)
            if prop in out:
                existing = out[prop]
                if isinstance(existing, list):
                    existing.append(val)
                else:
                    out[prop] = [existing, val]
            else:
                out[prop] = val
    return out


class MicrodataExtractor:
    """Parses schema.org microdata itemscope blocks from HTML."""

    ALLOWED_TYPES = _ALLOWED_TYPES

    @classmethod
    def extract(cls, html: str) -> list[dict]:
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        out: list[dict] = []
        for node in soup.find_all(attrs={"itemscope": True, "itemtype": True}):
            # Only top-level listing itemscopes (skip nested address/offer scopes).
            # A nested scope has an itemprop linking it to a parent scope.
            if node.has_attr("itemprop"):
                continue
            tail = _itemtype_tail(node.get("itemtype"))
            if tail not in cls.ALLOWED_TYPES:
                continue
            item = _parse_item(node)
            # attach the resolved @type for downstream consumers
            item.setdefault("@type", tail)
            out.append(item)
        return out
