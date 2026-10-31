import json
import sqlite3
import sys

import httpx
import pytest

import ai_providers
import config_loader
import db
import feedback_server
import launcher
from scrapers.base import RawListing
from vision import VisionScorer


def test_invalid_ai_reply_is_listing_local(monkeypatch):
    replies = iter(['', 'not json', '{bad}', '{"overall_score": 8}'])
    monkeypatch.setattr(ai_providers, 'credential', lambda _: 'test')
    monkeypatch.setattr(httpx, 'post', lambda url, **kw: httpx.Response(200, request=httpx.Request('POST', url), json={'choices': [{'message': {'content': next(replies)}}]}))
    scorer = VisionScorer({'ai': {'provider': 'openai'}})
    listing = RawListing(id='x', source='test', url='https://example.com', price=0, bedrooms=0, bathrooms=0, floor=None, neighborhood='', address='', photos=['https://example.com/a'])
    for _ in range(3):
        assert 'metadata_score' in scorer.score_listing(listing, [], [])['flags']
    assert scorer.score_listing(listing, [], [])['overall_score'] == 8
    assert scorer.ai_status == 'ok'


@pytest.mark.parametrize('status,expected', [(401, 'reconnect'), (403, 'reconnect'), (429, 'degraded'), (500, 'degraded')])
def test_http_failure_stops_ai(monkeypatch, status, expected):
    calls = []
    monkeypatch.setattr(ai_providers, 'credential', lambda _: 'test')
    monkeypatch.setattr(ai_providers.time, 'sleep', lambda _: None)
    def post(url, **kwargs):
        calls.append(1)
        return httpx.Response(status, request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx, 'post', post)
    listing = RawListing(id='x', source='test', url='https://example.com', price=0, bedrooms=0, bathrooms=0, floor=None, neighborhood='', address='', photos=['https://example.com/a'])
    scorer = VisionScorer({'ai': {'provider': 'openai'}})
    scorer.score_listing(listing, [], [])
    scorer.score_listing(listing, [], [])
    assert scorer.ai_status == expected
    assert len(calls) == (3 if status in (429, 500) else 1)


@pytest.mark.parametrize('route,method', [(p, 'POST') for p in ['/api/quit','/api/ai','/api/firecrawl','/api/import','/api/setup','/run','/feedback','/note','/pipeline_status','/criteria','/cadence','/api/stage']] + [('/api/ai','DELETE'),('/api/firecrawl','DELETE'),('/api/export','GET')])
def test_local_request_guard(route, method):
    client = feedback_server.app.test_client()
    assert client.open(route, method=method, headers={'Host':'evil.example:5055'}).status_code == 403
    assert client.open(route, method=method, headers={'Host':'localhost:5055', 'Origin':'http://evil.example:5055'}).status_code == 403


def test_ai_blank_key_preserves_saved(monkeypatch):
    saved = {}
    monkeypatch.setattr('keyring.set_password', lambda service, name, key: saved.update({name:key}))
    monkeypatch.setattr(ai_providers, 'credential', lambda name: saved.get(name))
    client = feedback_server.app.test_client()
    for key in ['  secret  ', '  ', None]:
        body = {'provider':'openai'}
        if key is not None: body['key'] = key
        assert client.post('/api/ai', json=body).status_code == 200
    assert saved['openai'] == 'secret'


def test_backup_rejects_invalid_types_and_merges_empty(monkeypatch, tmp_path):
    defaults = tmp_path / 'defaults.json'
    defaults.write_text('{"criteria": {}}')
    monkeypatch.setattr(feedback_server, 'CONFIG_PATH', defaults)
    client = feedback_server.app.test_client()
    for body in ({'criteria': []}, {'listings': [None]}, {'feedback': {}}, {'notes': [1]}, {'pipeline_status': ['x']}):
        response = client.post('/api/import', json=body)
        assert response.status_code == 400 and response.json['error']
    assert client.post('/api/import', json={'criteria': {'price_max': 42}}).status_code == 200
    assert json.loads((tmp_path / 'config.json').read_text())['criteria']['price_max'] == 42


def test_config_creates_user_directory(monkeypatch, tmp_path):
    monkeypatch.setenv('SCRAPPY_HOME', str(tmp_path / 'nested' / 'home'))
    bundled = tmp_path / 'defaults.json'
    bundled.write_text('{}')
    config_loader.update_base_config({'last_run':'today'}, bundled)
    assert bundled.read_text() == '{}'
    assert json.loads((tmp_path / 'nested' / 'home' / 'config.json').read_text())['last_run'] == 'today'


def test_existing_db_version_is_preserved(tmp_path):
    path = tmp_path / 'db.sqlite'
    with sqlite3.connect(path) as conn: conn.execute('PRAGMA user_version=7')
    with db.connect(str(path)) as conn: assert conn.execute('PRAGMA user_version').fetchone()[0] == 7


def test_import_db_exits(monkeypatch, tmp_path, capsys):
    source = tmp_path / 'source.sqlite'
    with sqlite3.connect(source) as conn: conn.execute('CREATE TABLE sample (value TEXT)')
    monkeypatch.setattr(launcher, 'health', lambda port: False)
    monkeypatch.setattr(sys, 'argv', ['launcher.py', '--import-db', str(source)])
    launcher.main()
    assert json.loads(capsys.readouterr().out)['ok'] is True
    assert (tmp_path / 'scrappy.db').exists()


def test_oauth_parallel_signins():
    feedback_server._OAUTH_PENDING.clear()
    client = feedback_server.app.test_client()
    from urllib.parse import parse_qs, urlparse
    states = []
    for _ in range(2):
        response = client.get('/auth/openrouter/start')
        callback = parse_qs(urlparse(response.location).query)['callback_url'][0]
        states.append(urlparse(callback).path.rsplit('/', 1)[-1])
    assert all(state in feedback_server._OAUTH_PENDING for state in states)


def test_source_empty_and_firecrawl_failure():
    from run import _source_status
    assert _source_status('inmuebles24', [], False, False)['status'] == 'empty'
    assert _source_status('inmuebles24', [], True, False)['status'] == 'needs_firecrawl'
    assert _source_status('inmuebles24', [], True, True)['status'] == 'error'
    assert _source_status('inmuebles24', [1], False, False)['status'] == 'ok'


def test_local_request_guard_accepts_browser_origins():
    client = feedback_server.app.test_client()
    for host in ('localhost:5055', '127.0.0.1:5055'):
        for origin in (None, 'http://localhost:5055', 'http://127.0.0.1:5055'):
            headers = {'Host': host}
            if origin: headers['Origin'] = origin
            assert client.post('/feedback', headers=headers, json={}).status_code == 400
