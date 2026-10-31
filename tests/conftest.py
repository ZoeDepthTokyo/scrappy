import socket
import pytest

@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRAPPY_HOME", str(tmp_path))
    def no_network(*args, **kwargs):
        raise OSError("Network access disabled during tests")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    import feedback_server, kanban, run, favorites
    monkeypatch.setattr(feedback_server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(feedback_server, "DB_PATH", tmp_path / "scrappy.db")
    monkeypatch.setitem(feedback_server.app.config, "SERVER_NAME", "localhost:5055")
    monkeypatch.setattr(kanban, "DB_PATH", tmp_path / "scrappy.db")
    monkeypatch.setattr(run, "DB_PATH", tmp_path / "scrappy.db")
    monkeypatch.setattr(run, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(run, "RUN_LOCK_PATH", tmp_path / "run.lock")
    monkeypatch.setattr(favorites, "REPORTS_DIR", tmp_path / "reports")
