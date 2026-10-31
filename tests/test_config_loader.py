import json
import config_loader
import paths

def test_defaults_and_user_overrides(tmp_path):
    defaults = tmp_path / "defaults.json"
    defaults.write_text(json.dumps({"criteria": {"bedrooms": 2, "bathrooms": 1}}))
    paths.user_config_path().write_text(json.dumps({"criteria": {"bedrooms": 3}}))
    result = config_loader.load_config(defaults)
    assert result["criteria"] == {"bedrooms": 3, "bathrooms": 1}

def test_updates_only_user_config(tmp_path):
    defaults = tmp_path / "defaults.json"
    defaults.write_text('{"cadence_days": 1}')
    config_loader.update_base_config({"cadence_days": 3}, defaults)
    assert json.loads(defaults.read_text())["cadence_days"] == 1
    assert json.loads(paths.user_config_path().read_text())["cadence_days"] == 3

def test_environment_keys_are_ignored(tmp_path, monkeypatch):
    defaults = tmp_path / "defaults.json"
    defaults.write_text('{}')
    monkeypatch.setenv("ANTHROPIC_API_KEY", "example")
    assert "anthropic_api_key" not in config_loader.load_config(defaults)
