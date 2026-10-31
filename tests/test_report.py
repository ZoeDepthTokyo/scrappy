import json
from report import generate_html

LISTINGS = [
    {
        "id": "i24:1",
        "source": "inmuebles24",
        "url": "http://x.com/1",
        "price": 32000,
        "bedrooms": 2,
        "bathrooms": 2,
        "floor": 5,
        "neighborhood": "Polanco",
        "photos_json": '["http://img.com/1.jpg"]',
        "vision_score": 8.2,
        "vision_flags": '["good_natural_light"]',
        "nice_to_haves": '{"washer_dryer": true, "fridge": false}',
        "alerted": 0,
    }
]


def test_generate_html_contains_listing():
    html = generate_html(LISTINGS, cadence_days=1)
    assert "Polanco" in html
    assert "32,000" in html
    assert "8.2" in html


def test_generate_html_contains_cadence_controls():
    html = generate_html(LISTINGS, cadence_days=1)
    assert "cadence" in html.lower()
    assert "Daily" in html
    assert 'name="viewport"' in html


def test_generate_html_contains_feedback_buttons():
    html = generate_html(LISTINGS, cadence_days=1)
    assert "👍" in html
    assert "👎" in html
    assert "i24:1" in html


def test_generate_html_contains_filter_buttons():
    html = generate_html(LISTINGS, cadence_days=1)
    assert "Polanco" in html


def test_generate_html_contains_mobile_layout_rules():
    html = generate_html(LISTINGS, cadence_days=1)
    assert "@media (max-width: 700px)" in html
    assert "grid-template-columns: 1fr" in html


def test_generate_html_escapes_embedded_content():
    html = generate_html(
        [
            {
                **LISTINGS[0],
                "id": 'i24:"1"',
                "neighborhood": 'Polanco <script>alert(1)</script>',
                "url": 'http://x.com/?q="bad"',
                "vision_flags": '["great_light", "<b>unsafe</b>"]',
            }
        ],
        cadence_days=1,
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert 'rel="noopener noreferrer"' in html
