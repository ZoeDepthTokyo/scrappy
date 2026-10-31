import httpx
import ai_providers
from vision import VisionScorer
from scrapers.base import RawListing


def test_provider_sends_image_urls_and_retries(monkeypatch):
    calls = []
    monkeypatch.setattr(ai_providers, "credential", lambda _: "test-key")
    monkeypatch.setattr(ai_providers.time, "sleep", lambda _: None)
    def post(url, **kwargs):
        calls.append(kwargs)
        request = httpx.Request("POST", url)
        if len(calls) < 3:
            return httpx.Response(429, request=request)
        return httpx.Response(200, request=request, json={"choices": [{"message": {"content": '{"overall_score": 8}'}}]})
    monkeypatch.setattr(ai_providers.httpx, "post", post)
    assert ai_providers.score("openai", "", "prompt", ["https://example.com/a.jpg"])["overall_score"] == 8
    assert len(calls) == 3
    assert calls[0]["json"]["messages"][0]["content"][1]["image_url"]["url"] == "https://example.com/a.jpg"


def test_auth_failure_falls_back(monkeypatch):
    request = httpx.Request("POST", "https://example.com")
    response = httpx.Response(401, request=request)
    def fail(*args):
        raise httpx.HTTPStatusError("failed", request=request, response=response)
    monkeypatch.setattr(ai_providers, "score", fail)
    scorer = VisionScorer({"ai": {"provider": "openai"}})
    listing = RawListing(id="x", source="test", url="https://example.com", price=0, bedrooms=0, bathrooms=0, floor=None, neighborhood="", address="", photos=["https://example.com/a.jpg"])
    assert scorer.score_listing(listing, [], []) is not None
    assert scorer.ai_status == "reconnect"
