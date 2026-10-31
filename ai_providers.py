"""Explicitly selected AI providers and operating-system credentials."""
import json
import time

import httpx
import keyring

PROVIDERS = {
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "google/gemini-2.5-flash"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "gemini-2.5-flash"),
    "openai": ("https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
    "anthropic": ("https://api.anthropic.com/v1/messages", "claude-sonnet-4-5"),
    "ollama": ("https://ollama.com/v1/chat/completions", "gemma3:27b"),
    "none": (None, ""),
}


def credential(provider):
    try:
        return keyring.get_password("Scrappy", provider)
    except Exception:
        return None


def connected(provider):
    return provider == "none" or bool(credential(provider))


def score(provider, model, prompt, photos):
    key = credential(provider)
    if not key or provider not in PROVIDERS or provider == "none":
        raise ValueError("Provider unavailable")
    url, default_model = PROVIDERS[provider]
    model = model or default_model
    if provider == "anthropic":
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        content = [{"type": "text", "text": prompt}] + [
            {"type": "image", "source": {"type": "url", "url": photo}} for photo in photos]
        payload = {"model": model, "max_tokens": 1024, "messages": [{"role": "user", "content": content}]}
    else:
        headers = {"Authorization": "Bearer " + key}
        content = [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {"url": photo}} for photo in photos]
        payload = {"model": model, "messages": [{"role": "user", "content": content}]}
    for attempt in range(3):
        response = httpx.post(url, headers=headers, json=payload, timeout=45, trust_env=False)
        if response.status_code != 429 and response.status_code < 500:
            break
        if attempt < 2:
            time.sleep(0.5 * (2 ** attempt))
    response.raise_for_status()
    data = response.json()
    raw = data["content"][0]["text"] if provider == "anthropic" else data["choices"][0]["message"]["content"]
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise json.JSONDecodeError("No JSON object", raw, 0)
    return json.loads(raw[start:end + 1])
