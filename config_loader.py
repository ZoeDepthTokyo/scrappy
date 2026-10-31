import json
from pathlib import Path
from typing import Any
import paths

CONFIG_PATH = paths.bundled_path("config.json")
_REMOVED = frozenset({"telegram_bot_token", "telegram_chat_id", "firecrawl_api_key", "anthropic_api_key", "easybroker_api_key"})

def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))

def _merge_dicts(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if key in _REMOVED:
            continue
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged

def resolve_local_config_path(config_path=CONFIG_PATH, local_path=None) -> Path:
    return Path(local_path) if local_path is not None else paths.user_config_path()

def load_file_config(config_path=CONFIG_PATH, local_path=None) -> dict:
    config = _merge_dicts({}, _read_json(config_path))
    local = resolve_local_config_path(config_path, local_path)
    return _merge_dicts(config, _read_json(local)) if local.exists() else config

def load_config(config_path=CONFIG_PATH, local_path=None) -> dict:
    return load_file_config(config_path, local_path)

def update_base_config(updates: dict, config_path=CONFIG_PATH) -> dict:
    target = paths.user_config_path()
    current = _read_json(target) if target.exists() else {}
    current = _merge_dicts(current, updates)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return load_file_config(config_path)
