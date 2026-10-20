import json
import sqlite3
from fastapi.testclient import TestClient
from backend.main import create_app


def test_dashboard_read_persists_history_without_acknowledging(tmp_path):
    path = tmp_path / 'state.sqlite'
    with TestClient(create_app(path)) as client:
        assert client.get('/api/dashboard/snapshot').json() == {'messages': [], 'agents': [], 'integration_attempts': []}
        first = client.post('/api/messages', json=dict(sender='codex', recipient='antigravity', content='Persisted message', subject='Read-only check')).json()
        second = client.post('/api/messages', json=dict(sender='antigravity', recipient='codex', content='Pending message')).json()
        client.post(f"/api/messages/{first['id']}/ack", json={'agent': 'antigravity'})
        seen = client.post('/api/agents/codex/heartbeat').json()
        with sqlite3.connect(path) as db:
            db.execute('INSERT INTO integration_attempts VALUES (?,?,?,?)', ('attempt-fixture', 'TASK-001', 'finished', json.dumps({'promoted': False, 'test_log': 'assertion failed'})))
        data = client.get('/api/dashboard/snapshot').json()
        assert {m['id']: m['delivered'] for m in data['messages']} == {first['id']: True, second['id']: False}
        assert data['agents'] == [seen]
        assert data['integration_attempts'][0]['document']['test_log'] == 'assertion failed'
        assert client.get('/api/inbox/codex').json()['messages'][0]['id'] == second['id']
        assert client.get('/api/approvals').json() == {'requests': []}
        assert client.get('/api/audit-logs').json() == {'audit_logs': []}
        assert client.get('/api/dashboard/snapshot').json() == data
    with TestClient(create_app(path)) as client:
        assert client.get('/api/dashboard/snapshot').json() == data


def test_dashboard_cors_is_limited_to_local_frontend(tmp_path):
    with TestClient(create_app(tmp_path / 'state.sqlite')) as client:
        headers = {'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'content-type'}
        response = client.options('/api/approvals/example/decision', headers=headers)
        assert response.status_code == 200
        assert response.headers['access-control-allow-origin'] == 'http://localhost:5173'
        assert 'access-control-allow-credentials' not in response.headers
        for origin in ('https://evil.example', 'http://localhost:8421', 'http://127.0.0.1:5173'):
            response = client.options('/api/approvals/example/decision', headers={**headers, 'Origin': origin})
            assert response.status_code == 400
            assert 'access-control-allow-origin' not in response.headers
        assert client.options('/api/dashboard/snapshot', headers={**headers, 'Access-Control-Request-Method': 'DELETE'}).status_code == 400
