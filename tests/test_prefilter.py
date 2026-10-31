import pytest
from prefilter import PreFilter
from scrapers.base import RawListing


def _l(**kw):
    base = dict(id="x:1", source="x", url="https://x", price=40000,
                bedrooms=2, bathrooms=2, floor=5, neighborhood="Polanco",
                address="Av X 1", photos=["a.jpg"], nice_to_haves={})
    base.update(kw)
    return RawListing(**base)


def test_rejects_furnished_in_title():
    pf = PreFilter(config={"criteria": {"furnished": False, "neighborhoods": ["Polanco"]}})
    out = pf.filter([_l(url="https://x.com/depto-amueblado-en-polanco")])
    assert out == []


def test_rejects_out_of_area():
    pf = PreFilter(config={"criteria": {"furnished": False, "neighborhoods": ["Polanco"]}})
    out = pf.filter([_l(neighborhood="Iztapalapa")])
    assert out == []


def test_rejects_known_bad_address(tmp_path):
    import sqlite3
    import db
    p = tmp_path / "d.db"
    conn = sqlite3.connect(p)
    db._ensure_schema(conn)
    listing = _l()
    conn.execute(
        "INSERT INTO listings (id,source,url,price,bedrooms,bathrooms,floor,neighborhood,address_hash,photos_json,first_seen,last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (listing.id, listing.source, listing.url, listing.price, listing.bedrooms, listing.bathrooms,
         listing.floor, listing.neighborhood, listing.address_hash, "[]", "2026-04-14", "2026-04-14"),
    )
    conn.execute(
        "INSERT INTO feedback (listing_id,signal,reason,created_at) VALUES (?,?,?,?)",
        (listing.id, "down", "smell test", "2026-04-14"),
    )
    conn.commit()
    conn.close()
    pf = PreFilter(config={"criteria": {"furnished": False, "neighborhoods": ["Polanco"]}}, db_path=p)
    out = pf.filter([listing])
    assert out == []


def test_keeps_valid_listing():
    pf = PreFilter(config={"criteria": {"furnished": False, "neighborhoods": ["Polanco"]}})
    out = pf.filter([_l()])
    assert len(out) == 1
