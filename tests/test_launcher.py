import json
import os
import subprocess
import sys
import threading
from unittest.mock import Mock

import feedback_server
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


def test_quit_during_startup_exits_normally(monkeypatch, tmp_path):
    quit_finished = threading.Event()
    exit_process = Mock()
    timer = Mock()
    timer_factory = Mock(return_value=timer)

    class Server:
        def serve_forever(self):
            response = feedback_server.app.test_client().post('/api/quit')
            assert response.status_code == 200
            quit_finished.set()

        def shutdown(self):
            pass

        def server_close(self):
            pass

    calls = 0

    def health(port):
        nonlocal calls
        calls += 1
        if calls >= 3:
            assert quit_finished.wait(1)
        return False

    monkeypatch.setattr(sys, 'argv', ['launcher.py', '--no-browser'])
    monkeypatch.setattr(launcher, 'health', health)
    monkeypatch.setattr(launcher, 'make_server', lambda *args: Server())
    monkeypatch.setattr(feedback_server, '_SERVER', None)
    monkeypatch.setattr(threading, 'Timer', timer_factory)
    monkeypatch.setattr(launcher.os, '_exit', exit_process)
    launcher.main()

    exit_process.assert_not_called()
    timer_factory.assert_called_once_with(10.0, exit_process, args=(0,))
    assert timer.daemon is True
    timer.start.assert_called_once()
    assert not (tmp_path / 'runtime.json').exists()


def test_quit_stops_real_server_without_forced_exit(tmp_path):
    script = '''
import http.client
import os
import socket
import sys
import threading
import time
from unittest.mock import Mock
import launcher

sock = socket.socket()
sock.bind(('127.0.0.1', 0))
port = sock.getsockname()[1]
sock.close()
os.environ['SCRAPPY_PORT'] = str(port)
sys.argv = ['launcher.py', '--no-browser']
forced_exit = Mock()
os._exit = forced_exit
result = []
def quit_when_ready():
    for _ in range(100):
        try:
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=0.5)
            conn.request('POST', '/api/quit')
            result.append((conn.getresponse().status, time.monotonic()))
            conn.close()
            return
        except OSError:
            time.sleep(0.02)
thread = threading.Thread(target=quit_when_ready, daemon=True)
thread.start()
launcher.main()
thread.join(1)
assert len(result) == 1 and result[0][0] == 200, result
assert time.monotonic() - result[0][1] < 2
forced_exit.assert_not_called()
assert not (launcher.paths.data_dir() / 'runtime.json').exists()
'''
    env = os.environ.copy()
    env['SCRAPPY_HOME'] = str(tmp_path)
    result = subprocess.run([sys.executable, '-c', script], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
