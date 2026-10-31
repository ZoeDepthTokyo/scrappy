"""Behavioural tests for MercadoLibre neighborhood re-derivation.

Root cause: the scraper formerly tagged every listing with the query-bucket
name, so a widened result set (e.g. a 'cuauhtemoc' query returning a
'lomas-de-sotelo' listing from Naucalpan) would carry a false 'Cuauhtémoc'
label through to Telegram alerts. These tests pin the URL-slug retag.
"""

from scrapers.mercadolibre import _infer_neighborhood


def test_url_slug_overrides_wrong_query_bucket():
    url = "https://departamento.mercadolibre.com.mx/MLM-4968783046-departamento-en-renta-en-lomas-de-sotelo-_JM"
    label, retagged = _infer_neighborhood(url, "", fallback="Cuauhtémoc")
    assert label == "Lomas de Sotelo"
    assert retagged is True, "Cross-area leak must be flagged for downstream filter"


def test_query_bucket_kept_when_url_has_no_known_slug():
    url = "https://departamento.mercadolibre.com.mx/MLM-1234567890-departamento-en-renta"
    label, retagged = _infer_neighborhood(url, "", fallback="Polanco")
    assert label == "Polanco"
    assert retagged is False


def test_description_text_does_not_override_bucket_without_url_slug():
    """Free-text proximity mentions are too noisy to retag without URL evidence."""
    url = "https://departamento.mercadolibre.com.mx/MLM-1234567890-departamento-en-renta"
    label, retagged = _infer_neighborhood(url, "A pasos de Escandon", fallback="Cuauhtémoc")
    assert label == "Cuauhtémoc"
    assert retagged is False


def test_polanco_ampliacion_matches_specific_first():
    """'ampliacion-polanco' must match before the looser 'polanco' slug."""
    url = "https://departamento.mercadolibre.com.mx/MLM-1-departamento-en-ampliacion-polanco"
    label, _ = _infer_neighborhood(url, "", fallback="Polanco")
    assert label == "Amp Polanco"


def test_retagged_flag_false_when_url_confirms_bucket():
    url = "https://departamento.mercadolibre.com.mx/MLM-1-departamento-en-polanco"
    label, retagged = _infer_neighborhood(url, "", fallback="Polanco")
    assert label == "Polanco"
    assert retagged is False


def test_hipodromo_beats_condesa_when_both_present():
    """'hipodromo-condesa' is the full slug — must match before bare 'condesa'."""
    url = "https://departamento.mercadolibre.com.mx/MLM-1-en-hipodromo-condesa"
    label, _ = _infer_neighborhood(url, "", fallback="Roma Norte")
    assert label == "Hipódromo"
