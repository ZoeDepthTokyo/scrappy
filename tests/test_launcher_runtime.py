import json

import launcher
import paths


def test_doctor_reports_port_of_running_instance(monkeypatch, capsys):
    paths.data_dir().mkdir(parents=True, exist_ok=True)
    (paths.data_dir() / "runtime.json").write_text(json.dumps({"port": 5999}), encoding="utf-8")
    monkeypatch.setattr(launcher, "health", lambda port: port == 5999)
    monkeypatch.setattr("sys.argv", ["launcher.py", "--doctor"])
    launcher.main()
    report = json.loads(capsys.readouterr().out)
    assert report["port"] == 5999 and report["server_running"] is True


def test_run_meta_reports_running_flag():
    import feedback_server

    client = feedback_server.app.test_client()
    body = client.get("/run_meta").get_json()
    assert body["running"] is False


def test_run_meta_merges_live_progress_file():
    import json as _json
    import feedback_server
    import paths as _paths

    _paths.data_dir().mkdir(parents=True, exist_ok=True)
    (_paths.data_dir() / "run_meta.json").write_text(_json.dumps(
        {"stage": "scraping", "elapsed_s": 12, "ai_status": "pending", "sources": {"lamudi": {"status": "ok", "count": 3}}}), encoding="utf-8")
    body = feedback_server.app.test_client().get("/run_meta").get_json()
    assert body["stage"] == "scraping" and body["sources"]["lamudi"]["count"] == 3
