import json
import sys
import launcher


def test_doctor_reports_without_starting_server(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['launcher.py', '--doctor'])
    monkeypatch.setattr(launcher, 'health', lambda port: False)
    monkeypatch.setattr(launcher.ai_providers, 'credential', lambda provider: None)
    launcher.main()
    result = json.loads(capsys.readouterr().out)
    assert result['version'] == '0.5.0'
    assert result['server_running'] is False
    assert result['ai_provider'] == 'none'
    assert result['firecrawl_configured'] is False
