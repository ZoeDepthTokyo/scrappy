import json
import pytest
import db
import feedback_server
import kanban


@pytest.fixture
def client(tmp_path, monkeypatch):
    config = tmp_path / 'defaults.json'
    config.write_text(json.dumps({'criteria': {'price_max': 100}}))
    monkeypatch.setattr(feedback_server, 'CONFIG_PATH', config)
    monkeypatch.setattr(feedback_server, 'DB_PATH', tmp_path / 'scrappy.db')
    monkeypatch.setattr(kanban, 'DB_PATH', tmp_path / 'scrappy.db')
    db.connect(str(tmp_path / 'scrappy.db')).close()
    return feedback_server.app.test_client()


def test_ai_settings_never_return_key(client, monkeypatch):
    import ai_providers
    saved = {}
    monkeypatch.setattr('keyring.set_password', lambda service, name, key: saved.update({name: key}))
    monkeypatch.setattr(ai_providers, 'credential', lambda name: saved.get(name))
    response = client.post('/api/ai', json={'provider': 'openai', 'model': 'model', 'key': 'private-value'})
    assert response.status_code == 200
    assert response.json == {'provider': 'openai', 'model': 'model', 'connected': True}
    assert 'private-value' not in response.get_data(as_text=True)
    assert 'private-value' not in feedback_server._config_write_path().read_text()


def test_backup_round_trip_and_invalid_import(client):
    conn = db.connect(str(feedback_server.DB_PATH))
    conn.execute("INSERT INTO listings(id,source,url,price) VALUES ('id','source','https://example.com',123)")
    conn.commit()
    conn.close()
    backup = client.get('/api/export').json
    assert backup['listings'][0]['price'] == 123
    assert client.post('/api/import', json=backup).status_code == 200
    assert client.post('/api/import', json={'listings': [{'unexpected': 'value'}]}).status_code == 400


def test_openrouter_signin_uses_pkce_and_stores_key(client, monkeypatch):
    from urllib.parse import parse_qs, urlparse
    import httpx
    stored = {}
    monkeypatch.setattr('keyring.set_password', lambda service, name, key: stored.update({name: key}))
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(200, request=httpx.Request('POST', url), json={'key': 'private-value'})
    monkeypatch.setattr(httpx, 'post', post)
    start = client.get('/auth/openrouter/start')
    params = parse_qs(urlparse(start.location).query)
    assert params['code_challenge_method'] == ['S256']
    state = urlparse(params['callback_url'][0]).path.rsplit('/', 1)[-1]
    verifier = feedback_server._OAUTH_PENDING[state][0]
    response = client.get('/auth/openrouter/callback/' + state, query_string={'code': 'test-code'})
    assert response.status_code == 302
    assert stored == {'openrouter': 'private-value'}
    assert calls[0]['json']['code_challenge_method'] == 'S256'
    assert calls[0]['json']['code_verifier'] == verifier
    assert 'private-value' not in feedback_server._config_write_path().read_text()
    assert client.get('/auth/openrouter/callback/' + state, query_string={'code': 'test-code'}).status_code == 400
