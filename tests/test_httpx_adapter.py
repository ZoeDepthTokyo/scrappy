import pytest
from scrapers.httpx_adapter import HttpxAdapter, JsonLdExtractor

SAMPLE_HTML = """
<html><head>
<script type="application/ld+json">
{"@type":"Apartment","name":"Test apt","url":"https://x.com/1",
 "address":{"streetAddress":"Av Test 1","addressLocality":"Polanco"},
 "numberOfBedrooms":2,"numberOfBathroomsTotal":2,
 "offers":{"price":"40000","priceCurrency":"MXN"},
 "image":["https://x.com/1.jpg"]}
</script>
</head><body>...</body></html>
"""


def test_jsonld_extracts_apartment():
    items = JsonLdExtractor.extract(SAMPLE_HTML)
    assert len(items) == 1
    assert items[0]["name"] == "Test apt"
    assert items[0]["@type"] == "Apartment"


def test_adapter_rotates_ua_and_writes_health_log(tmp_path):
    """UA rotation picks from UA_POOL; health log captures bytes/status/jsonld."""
    from unittest.mock import patch, MagicMock
    from scrapers.httpx_adapter import HttpxAdapter, UA_POOL

    a = HttpxAdapter(source_name="test_src", rotate_ua=True, use_curl_cffi_on_403=False)
    fake_response = MagicMock(status_code=200, text=SAMPLE_HTML)
    with patch.object(a.client, "get", return_value=fake_response):
        html = a.fetch_html("https://example.test/x")
    a.close()
    assert html == SAMPLE_HTML
    # UA used must be from the pool
    assert a.client.headers["User-Agent"] in UA_POOL
    # Health log flush
    target = tmp_path / "scraper_health.json"
    HttpxAdapter.write_health_log(target)
    import json as _j
    rows = _j.loads(target.read_text(encoding="utf-8"))
    assert any(
        r["url"] == "https://example.test/x" and r["status"] == 200 and r["bytes"] > 0
        for r in rows
    )


def test_jsonld_handles_itemlist():
    html = '<script type="application/ld+json">{"@type":"ItemList","itemListElement":[{"@type":"ListItem","item":{"@type":"Apartment","name":"a"}},{"@type":"ListItem","item":{"@type":"Apartment","name":"b"}}]}</script>'
    items = JsonLdExtractor.extract(html)
    assert len(items) == 2
