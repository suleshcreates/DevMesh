import json
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.memory_manager import MemoryManager

async def test_transaction_failure_rolls_back_state_and_events(tmp_path, monkeypatch):
    app = create_app(tmp_path / 'db')
    async with app.router.lifespan_context(app):
        events = []
        async def capture(*args):
            events.append(args)
        monkeypatch.setattr(app.state.ws, 'broadcast', capture)
        await app.state.db.connection.execute("CREATE TRIGGER reject_message BEFORE INSERT ON messages BEGIN SELECT RAISE(ABORT, 'storage failure'); END")
        await app.state.db.connection.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://test') as c:
            response = await c.post('/api/decisions/propose', json=dict(request_id='failure', agent='codex', title='Design', content='Persist data', rationale='Durability'))
            assert response.status_code == 500
            assert (await c.get('/api/decisions')).json() == {'decisions': []}
            assert await app.state.db.inbox('antigravity') == []
            assert not events
            rows = await (await app.state.db.connection.execute('SELECT * FROM decision_requests')).fetchall()
            assert not rows

def test_generated_files_are_never_authoritative_and_atomic(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / 'db')) as c:
        result = c.put('/api/context/PROJECT_SPEC.md/source', json={'content':'An operator-supplied fact'})
        assert result.status_code == 200
        path = tmp_path / 'PROJECT_SPEC.md'
        original = path.read_text()
        path.write_text('Forged source')
        assert 'Forged' not in c.get('/api/context/PROJECT_SPEC.md').json()['content']
        assert path.read_text() == original
        import os
        replace = os.replace
        calls = []
        def inspect(src, dst):
            from pathlib import Path
            assert Path(src).parent == Path(dst).parent
            # Opening for append verifies the temporary handle is closed on Windows.
            with open(src, 'a'):
                pass
            calls.append((src, dst))
            return replace(src, dst)
        monkeypatch.setattr('backend.memory_manager.os.replace', inspect)
        assert c.get('/api/context/PROJECT_SPEC.md').json()['generated']
        assert calls
        assert c.get('/api/context/UNKNOWN.md').status_code == 422

def test_request_identifier_redaction_and_startup_failure_recovery(tmp_path, monkeypatch):
    secret = 'sk-private123456789'
    db_path = tmp_path / 'db'
    def fail(*args):
        raise OSError('disk failure')
    with monkeypatch.context() as m:
        m.setattr(MemoryManager, 'write_atomic', fail)
        with TestClient(create_app(db_path)) as c:
            p = dict(request_id=secret, agent='codex', title='Design', content='Persist data', rationale='Durability')
            d = c.post('/api/decisions/propose', json=p).json()
            assert c.post('/api/decisions/propose', json=p).json() == d
            assert not c.get('/api/context/DECISIONS.md').json()['generated']
    assert secret.encode() not in db_path.read_bytes()
    with TestClient(create_app(db_path)) as c:
        assert (tmp_path / 'DECISIONS.md').exists()
        assert c.get('/api/decisions/DEC-001').json() == d

@pytest.mark.parametrize('counter', [None, '', '   '])
def test_challenge_requires_peer_proposal(counter):
    from pydantic import ValidationError
    from backend.decision_models import Critique
    with pytest.raises(ValidationError):
        Critique.model_validate(dict(verdict='CHALLENGE', identified_risks=[], counter_proposal=counter,
                                     tradeoff_analysis=dict(security='Local', deployment='Local', complexity='Small')))
