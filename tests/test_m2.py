import asyncio
import json
from uuid import uuid4
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.database import Database
from backend.decision_engine import DecisionEngine, DecisionError, transition
from backend.decision_models import Proposal, CritiqueRequest
from backend.memory_manager import MemoryManager, DOCUMENTS
from mcp_bridge import devmesh_mcp as bridge

SECRET = 'sk-testsecret123456789'

def proposal(**kwargs):
    return dict(request_id=str(uuid4()), agent='codex', title='Storage design',
                content='Use SQLite WAL', rationale='Durable local records', **kwargs)

def critique(verdict='CHALLENGE', blocking=False, counter='Use SQLite with backups'):
    return dict(verdict=verdict, identified_risks=[dict(description='Recovery risk', blocking=blocking)] if blocking else [],
                counter_proposal=counter, tradeoff_analysis=dict(security='Local access', deployment='One process', complexity='Small'))

def submit(client, d, agent, value, **extra):
    body = dict(request_id=str(uuid4()), agent=agent, expected_revision=d['revision'],
                critique_json=json.dumps(value) if isinstance(value, dict) else value)
    body.update(extra)
    return client.post(f'/api/decisions/{d["id"]}/critique', json=body)

def test_challenge_agreement_and_restart_memory(tmp_path):
    path = tmp_path / 'db.sqlite'
    with TestClient(create_app(path)) as c:
        d = c.post('/api/decisions/propose', json=proposal()).json()
        assert d['id'] == 'DEC-001'
        d = submit(c, d, 'antigravity', critique()).json()
        assert d['round_count'] == 1
        d = submit(c, d, 'codex', critique('AGREE', counter=None)).json()
        assert d['status'] == 'agreed' and d['round_count'] == 2
        for name in ('DECISIONS.md', 'ARCHITECTURE.md'):
            assert 'DEC-001' in (tmp_path / name).read_text()
            assert 'Use SQLite with backups' in (tmp_path / name).read_text()
        (tmp_path / 'ARCHITECTURE.md').unlink()
    with TestClient(create_app(path)) as c:
        assert c.get('/api/decisions/DEC-001').json() == d
        assert c.get('/api/decisions').json() == {'decisions': []}
        assert 'Use SQLite with backups' in (tmp_path / 'ARCHITECTURE.md').read_text()
        assert 'No task planning' in c.get('/api/context/TASKS.md').json()['content']

def test_escalation_event_and_human_resolution(tmp_path):
    with TestClient(create_app(tmp_path / 'db')) as c:
        d = c.post('/api/decisions/propose', json=proposal()).json()
        for agent in ('antigravity', 'codex', 'antigravity'):
            d = submit(c, d, agent, critique(counter=f'Alternative from {agent}')).json()
        assert d['status'] == 'escalated_to_human' and d['round_count'] == 3
        event = c.get('/api/inbox/human').json()['messages'][0]
        assert event['subject'] == 'HUMAN_APPROVAL_REQUIRED'
        payload = json.loads(event['content'])['decision']
        assert {p['agent'] for p in payload['proposals']} == {'codex', 'antigravity'}
        assert len(payload['critiques']) == 3
        assert submit(c, d, 'codex', critique('AGREE')).status_code == 409
        body = dict(request_id=str(uuid4()), expected_revision=d['revision'], directive='Use the backup design', rationale='Recovery is required')
        resolved = c.post('/api/decisions/DEC-001/human-resolve', json=body).json()
        assert resolved['status'] == 'human_resolved'
        assert resolved['human_resolution']['directive'] == body['directive']
        assert 'Use the backup design' in (tmp_path / 'ARCHITECTURE.md').read_text()
        assert c.post('/api/decisions/DEC-001/human-resolve', json=body).json() == resolved

def test_unilateral_blocking_and_invalid_states(tmp_path):
    with TestClient(create_app(tmp_path / 'db')) as c:
        body = proposal()
        assert c.post('/api/decisions/propose', json={**body, 'status': 'agreed'}).status_code == 422
        d = c.post('/api/decisions/propose', json=body).json()
        assert submit(c, d, 'codex', critique('AGREE')).status_code == 409
        assert submit(c, d, 'human', critique('AGREE')).status_code == 422
        assert c.post('/api/decisions/DEC-001/human-resolve', json=dict(request_id='human', expected_revision=1, directive='yes', rationale='yes')).status_code == 409
        d = submit(c, d, 'antigravity', critique('AGREE', True, None)).json()
        assert d['status'] == 'open' and d['round_count'] == 1
        assert c.post('/api/messages', json=dict(sender='codex', recipient='antigravity', content='agreed')).status_code == 201
        assert c.get('/api/decisions/DEC-001').json()['status'] == 'open'
        assert submit(c, d, 'antigravity', critique('AGREE'), status='agreed').status_code == 422
    with pytest.raises(DecisionError):
        transition({'state': 'NEW', 'history': []}, 'RECORD')

@pytest.mark.parametrize('invalid', ['{bad', '{"verdict":"AGREE"}', json.dumps({**critique(), 'identified_risks': [{'description':'risk', 'blocking':'false'}]})])
def test_invalid_reask_persisted_once(tmp_path, invalid):
    path = tmp_path / 'db'
    with TestClient(create_app(path)) as c:
        d = c.post('/api/decisions/propose', json=proposal()).json()
        d = submit(c, d, 'antigravity', invalid).json()
        assert d['invalid_count'] == 1 and d['round_count'] == 0
    with TestClient(create_app(path)) as c:
        d = submit(c, d, 'antigravity', invalid).json()
        assert d['status'] == 'escalated_to_human'
        messages = c.get('/api/inbox/antigravity').json()['messages']
        assert sum(m['subject'] == 'CRITIQUE_REASK' for m in messages) == 1

async def test_concurrent_connections_and_idempotency(tmp_path):
    a, b = Database(tmp_path / 'db'), Database(tmp_path / 'db')
    await a.open()
    await b.open()
    try:
        ea, eb = DecisionEngine(a), DecisionEngine(b)
        await ea.initialize()
        await eb.initialize()
        req = Proposal(**proposal())
        results = await asyncio.gather(ea.mutate('propose', req), eb.mutate('propose', req))
        assert results[0][0] == results[1][0]
        assert sum(bool(r[1]) for r in results) == 1
        body = dict(agent='antigravity', expected_revision=1, critique_json=json.dumps(critique('AGREE')))
        results = await asyncio.gather(ea.mutate('critique', CritiqueRequest(request_id='one', **body), 'DEC-001'),
                                       eb.mutate('critique', CritiqueRequest(request_id='two', **body), 'DEC-001'), return_exceptions=True)
        assert sum(isinstance(r, DecisionError) for r in results) == 1
        d = await ea.get('DEC-001')
        assert d['round_count'] == 1 and len(d['critiques']) == 1 and d['status'] == 'agreed'
        winner = 'one' if not isinstance(results[0], Exception) else 'two'
        repeated, messages = await ea.mutate('critique', CritiqueRequest(request_id=winner, **body), 'DEC-001')
        assert repeated == d and not messages
        with pytest.raises(DecisionError):
            await ea.mutate('critique', CritiqueRequest(request_id=winner, **{**body, 'critique_json':'{}'}), 'DEC-001')
    finally:
        await a.close()
        await b.close()

def test_redaction_events_and_render_failure(tmp_path, monkeypatch):
    path = tmp_path / 'db'
    app = create_app(path)
    with TestClient(app) as c:
        with c.websocket_connect('/ws/dashboard') as ws:
            body = proposal()
            body['content'] = 'Design ' + SECRET
            d = c.post('/api/decisions/propose', json=body).json()
            assert SECRET not in json.dumps(d)
            for _ in range(3):
                assert SECRET not in json.dumps(ws.receive_json())
        def fail(*args):
            raise OSError('cannot replace')
        with monkeypatch.context() as m:
            m.setattr('backend.memory_manager.os.replace', fail)
            d = submit(c, d, 'antigravity', critique('AGREE', counter=None)).json()
            assert d['status'] == 'agreed'
            context = c.get('/api/context/DECISIONS.md').json()
            assert not context['generated'] and 'DEC-001' in context['content']
        context = c.get('/api/context/DECISIONS.md').json()
        assert context['generated'] and SECRET not in context['content']
        for name in DOCUMENTS:
            assert SECRET not in (tmp_path / name).read_text()
        assert SECRET not in json.dumps(c.get('/api/inbox/antigravity').json())
        c.put('/api/context/PROJECT_SPEC.md/source', json={'content': 'Project fact ' + SECRET})
        assert '[REDACTED]' in c.get('/api/context/PROJECT_SPEC.md').json()['content']
        assert c.put('/api/context/TASKS.md/source', json={'content':'fake task'}).status_code == 422
    assert SECRET.encode() not in path.read_bytes()
    with TestClient(create_app(path)) as c:
        assert 'DEC-001' in c.get('/api/context/DECISIONS.md').json()['content']

@pytest.mark.parametrize('name', ['../secret', 'C:\\secret', 'OTHER.md', 'architecture.md', '/etc/passwd', '..%2Fsecret'])
async def test_context_rejects_paths(name, monkeypatch):
    async def fail(*args, **kwargs):
        pytest.fail('Unsupported name reached HTTP')
    monkeypatch.setattr(bridge, 'request', fail)
    with pytest.raises(ValueError):
        await bridge.get_project_context(name)

async def test_mcp_m2_wrappers(tmp_path, monkeypatch):
    app = create_app(tmp_path / 'db')
    original = httpx.AsyncClient
    monkeypatch.setattr(bridge.httpx, 'AsyncClient', lambda **kw: original(**kw, transport=httpx.ASGITransport(app=app)))
    async with app.router.lifespan_context(app):
        d = await bridge.submit_decision_proposal('proposal', 'codex', 'Design', 'Persist facts', 'Durability')
        assert (await bridge.list_open_decisions())['decisions'] == [d]
        d = await bridge.submit_critique(d['id'], 'critique', 'antigravity', 1, json.dumps(critique('AGREE')))
        assert d['status'] == 'agreed'
        assert 'Persist facts' in (await bridge.get_project_context('DECISIONS.md'))['content']
