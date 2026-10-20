"""M5 deterministic agents; real disposable Git, SQLite, tests and guarded promotion."""
import asyncio
import json
import sys
import time
from pathlib import Path
from uuid import uuid4
import httpx
import pytest
import pytest_asyncio
from backend.main import create_app
from backend.orchestrator import Orchestrator
from backend.git_workspace import GitWorkspace, process
from backend.project_models import StartProject, AgentReport, ProjectControl, Usage
from backend.task_models import ApprovalDecision
from backend.decision_models import HumanResolution
from backend.decision_engine import DecisionError


def uid():
    return str(uuid4())


def critique(verdict='AGREE'):
    return dict(verdict=verdict, identified_risks=[] if verdict == 'AGREE' else [{'description': 'Recovery must be specified', 'blocking': True}],
                counter_proposal=None if verdict == 'AGREE' else 'Small login with explicit validation and recovery',
                tradeoff_analysis=dict(security='Validate inputs locally', deployment='No deployment', complexity='Two small tasks'))


class FakeCodex:
    def __init__(self):
        self.calls = []
        self.challenge = False
        self.invalid_critique = False
        self.fail_tests = False
        self.system = None

    async def request(self, job, repository):
        self.calls.append(json.loads(json.dumps(job)))
        phase = job['phase']
        if phase == 'analysis':
            assert set(job['context']) == {'goal'}
            return {'content': 'Build a small login feature with validated credentials and a form', 'rationale': 'Keep logic and UI separate'}
        if phase == 'critique':
            assert set(job['context']['independent_analyses']) == {'codex', 'antigravity'}
            return {'text': 'approved'} if self.invalid_critique else critique('CHALLENGE' if self.challenge else 'AGREE')
        if phase == 'plan':
            return {'tasks': [dict(key='form', request_id=uid(), title='Login form', description='Render the accepted login UI', category='frontend', assigned_agent='antigravity', dependencies=['logic'], touched_files_scope=['login.html'], acceptance_criteria=['Form has username and credential inputs']),
                              dict(key='logic', request_id=uid(), title='Login logic', description='Implement the accepted login validator', category='backend', assigned_agent='codex', dependencies=[], touched_files_scope=['login.py'], acceptance_criteria=['Valid login succeeds and invalid login fails'])]}
        if phase == 'execution':
            t = job['context']['task']
            assert job['context']['memory']['version']
            assert t['assigned_agent'] == job['agent']
            ws = GitWorkspace(repository)
            await ws.validate_task(t)
            filename = t['touched_files_scope'][0]
            target = ws.write_path(t, filename)
            if filename == 'login.py':
                content = 'def authenticate(name, supplied):\n    return ' + ('False' if self.fail_tests else 'name == "demo" and supplied == "example"') + '\n'
            else:
                content = '<form><input name="username"><input type="password"><button>Login</button></form>\n'
            target.write_text(content + ('# attempt ' + str(t['retry_count']) + '\n' if filename.endswith('.py') else ''))
            await ws.git('add', '--', filename, cwd=target.parent)
            await ws.git('commit', '-m', 'Implement fixture ' + t['id'], cwd=target.parent)
            if self.system:
                await self.system.usage(job['project_id'], Usage(agent=job['agent'], correlation_id=job['id'], event_id='fixture-write-and-commit', kind='tool'))
            return {'commit': await ws.head(t['branch']), 'summary': 'Actual scoped fixture commit created'}
        if phase == 'review':
            assert job['agent'] != job['context']['task']['assigned_agent']
            assert job['commit'] == job['context']['task']['review']['commit']
            assert await GitWorkspace(repository).head(job['context']['task']['branch']) == job['commit']
            return {'verdict': 'APPROVED', 'feedback': 'Fixture reviewer inspected the exact recorded diff'}
        raise AssertionError(phase)


class FakeAntigravity(FakeCodex):
    """Separately instantiated peer, delivered through the durable poll outbox."""


async def make_repo(directory):
    repo = directory / 'target with spaces'
    repo.mkdir()
    code, _, _ = await process(['git', 'init', '-b', 'main'], repo)
    assert code == 0
    ws = GitWorkspace(repo)
    (repo / '.gitignore').write_text('/.worktrees/\n__pycache__/\n')
    (repo / 'tests').mkdir()
    (repo / 'tests' / 'test_login.py').write_text('import unittest\nfrom login import authenticate\nclass LoginTest(unittest.TestCase):\n    def test_login(self):\n        self.assertTrue(authenticate("demo", "example"))\n        self.assertFalse(authenticate("other", "wrong"))\n')
    await ws.git('add', '.')
    await ws.git('commit', '-m', 'Baseline fixture test')
    await ws.git('switch', '-c', 'operator')
    return repo


@pytest_asyncio.fixture
async def system(tmp_path):
    repo = await make_repo(tmp_path)
    codex, antigravity = FakeCodex(), FakeAntigravity()
    app = create_app(tmp_path / 'state.sqlite', adapters={'codex': codex}, orchestration_worker=False)
    async with app.router.lifespan_context(app):
        o = app.state.orchestrator
        codex.system = antigravity.system = o
        yield app, o, repo, codex, antigravity


async def start(system, **changes):
    app, o, repo, codex, antigravity = system
    data = dict(request_id=uid(), goal='build a small login feature', repository=str(repo), test_command=[sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests'])
    data.update(changes)
    return await o.start(StartProject(**data))


async def deliver(o, p, job, adapter):
    # The fake Antigravity consumes the real durable poll queue, without implicit ack.
    inbox = await o.db.inbox(job['agent'])
    assert any(m['id'] == job['message_id'] and not m['delivered'] for m in inbox)
    context = await o.assigned_context(p['id'], job['id'], job['agent'])
    result = await adapter.request(context, p['repository'])
    report = AgentReport(request_id=uid(), correlation_id=job['id'], agent=job['agent'], phase=job['phase'], task_id=job['task_id'], decision_id=job['decision_id'], commit=job['commit'], payload=result)
    returned = await o.report(p['id'], report)
    assert any(m['id'] == job['message_id'] and not m['delivered'] for m in await o.db.inbox(job['agent']))
    # Acceptance and completion do not pretend that reading acknowledged work.
    await o.db.acknowledge(job['message_id'], job['agent'])
    return returned, report


async def pump(system, *, until=None, approve=False, limit=100):
    app, o, repo, codex, antigravity = system
    for _ in range(limit):
        p = (await o.list())[0]
        if until and until(p):
            return p
        if p['state'] in ('paused', 'complete', 'cancelled'):
            return p
        await o.tick()
        p = (await o.list())[0]
        if until and until(p):
            return p
        if p['state'] in ('paused', 'complete', 'cancelled'):
            return p
        for job in [j for j in p['jobs'] if j['status'] == 'pending']:
            await deliver(o, p, job, codex if job['agent'] == 'codex' else antigravity)
        if approve and p['state'] == 'approval':
            a = next(a for a in await app.state.guard.list() if a['id'] == p['integration']['approval_id'])
            if a['state'] == 'pending':
                await app.state.guard.decide(a['id'], ApprovalDecision(fingerprint=a['fingerprint'], decision='approved', reason='Test operator inspected exact harmless fixture test action'))
    raise AssertionError('Lifecycle failed to reach expected state')


async def test_full_lifecycle_uses_real_commits_tests_and_gates(system):
    app, o, repo, codex, antigravity = system
    p = await start(system)
    initial_main = await GitWorkspace(repo).head('main')
    first = await pump(system, until=lambda p: p['state'] == 'approval')
    assert await GitWorkspace(repo).head('main') == initial_main
    assert not any(m['subject'] == 'PROJECT_COMPLETE' for m in await o.db.inbox('human'))
    task = await app.state.tasks.get(first['tasks'][0]['id'])
    with pytest.raises(DecisionError):
        await app.state.runner.integrate(task['id'], first['integration']['key'], first['integration']['approval_id'])
    assert task['review']['reviewer'] == 'antigravity'
    result = await pump(system, approve=True)
    assert result['state'] == 'complete', result.get('reason')
    assert result['progress'] == {'complete': 2, 'required': 2}
    assert result['tasks'][0]['key'] == 'logic' and result['tasks'][1]['key'] == 'form'
    assert all(t['memory_version'] == result['memory']['version'] for t in result['tasks'])
    assert 'Locked memory version' in (await app.state.memory.context('ARCHITECTURE.md'))['content']
    assert codex.calls[0]['context'] == antigravity.calls[0]['context'] == {'goal': p['goal']}
    assert any(c['phase'] == 'review' and c['agent'] == 'codex' for c in codex.calls)
    assert any(c['phase'] == 'critique' for c in codex.calls) and any(c['phase'] == 'critique' for c in antigravity.calls)
    assert len(result['memory']['content']['decisions']) == 2
    rows = await (await o.db.connection.execute('SELECT document FROM integration_attempts')).fetchall()
    outcomes = [json.loads(r[0]) for r in rows]
    assert len(outcomes) == 2 and all(r['promoted'] and 'OK' in r['test_log'] for r in outcomes)
    assert await GitWorkspace(repo).head('main') == outcomes[-1]['candidate']
    assert any(m['subject'] == 'PROJECT_COMPLETE' for m in await o.db.inbox('human'))
    before = await o.list()
    await o.tick()
    assert await o.list() == before


async def test_independent_analyses_before_exchange_and_duplicate_delivery(system):
    app, o, repo, codex, antigravity = system
    p = await start(system)
    c = p['jobs'][0]
    p, report = await deliver(o, p, c, codex)
    assert len(p['analyses']) == 1 and not p['decision_id']
    await o.tick()
    assert not (await o.get(p['id']))['decision_id']
    assert await o.report(p['id'], report) == p
    bad = report.model_copy(update={'request_id': uid(), 'agent': 'antigravity'})
    with pytest.raises(DecisionError):
        await o.report(p['id'], bad)
    a = (await o.get(p['id']))['jobs'][1]
    context = await o.assigned_context(p['id'], a['id'], 'antigravity')
    assert 'analyses' not in context['context']
    await deliver(o, p, a, antigravity)
    await o.tick()
    await o.tick()
    p = await o.get(p['id'])
    assert len(p['analyses']) == 2
    assert len([j for j in p['jobs'] if j['phase'] == 'critique']) == 1


async def test_escalation_human_resolution_requires_explicit_resume(system):
    app, o, repo, codex, antigravity = system
    codex.challenge = antigravity.challenge = True
    p = await start(system)
    p = await pump(system)
    assert p['state'] == 'paused'
    d = await app.state.decisions.get(p['decision_id'])
    assert d['round_count'] == 3 and d['state'] == 'ESCALATE'
    with pytest.raises(DecisionError):
        await o.control(p['id'], 'resume', ProjectControl(request_id=uid(), expected_revision=p['revision'], reason='Must resolve first'))
    await app.state.decisions.mutate('human', HumanResolution(request_id=uid(), expected_revision=d['revision'], directive='Implement the small login with validation', rationale='Operator selects the bounded design'), d['id'])
    await o.tick()
    assert (await o.get(p['id']))['state'] == 'paused'
    p = await o.control(p['id'], 'resume', ProjectControl(request_id=uid(), expected_revision=p['revision'], reason='Reviewed human resolution'))
    assert p['state'] == 'critique'
    codex.challenge = antigravity.challenge = False
    p = await pump(system, approve=True)
    assert p['state'] == 'complete'
    assert any(d['status'] == 'human_resolved' for d in p['memory']['content']['decisions'])


async def test_invalid_critique_reask_and_no_generic_agreement(system):
    app, o, repo, codex, antigravity = system
    codex.invalid_critique = antigravity.invalid_critique = True
    await start(system)
    p = await pump(system)
    d = await app.state.decisions.get(p['decision_id'])
    assert p['state'] == 'paused' and d['state'] == 'ESCALATE'
    assert d['invalid_count'] == 2 and d['round_count'] == 0
    assert d['resolution'] is None


async def test_failed_real_tests_exhaust_retry_budget(system):
    app, o, repo, codex, antigravity = system
    codex.fail_tests = True
    before = await GitWorkspace(repo).head('main')
    await start(system)
    p = await pump(system, approve=True)
    task = await app.state.tasks.get(p['tasks'][0]['id'])
    assert p['state'] == 'paused' and task['status'] == 'blocked' and task['retry_count'] == 3
    assert await GitWorkspace(repo).head('main') == before
    rows = await (await o.db.connection.execute('SELECT document FROM integration_attempts')).fetchall()
    assert len(rows) == 3 and all(not json.loads(r[0])['promoted'] for r in rows)
    assert all('FAILED' in json.loads(r[0])['test_log'] for r in rows)
    with pytest.raises(DecisionError):
        await o.control(p['id'], 'resume', ProjectControl(request_id=uid(), expected_revision=p['revision'], reason='Cannot reset retries'))


@pytest.mark.parametrize('kind', ['timeout', 'stall', 'tool'])
async def test_execution_limits_persist_and_block_terminally(system, kind):
    app, o, repo, codex, antigravity = system
    await start(system)
    p = await pump(system, until=lambda p: p['state'] == 'execution')
    j = next(j for j in p['jobs'] if j['phase'] == 'execution' and j['status'] == 'pending')
    if kind == 'timeout':
        p['attempts'][j['task_id']]['started_at'] = time.time() - 601
        await o.save(p)
        await o.tick()
    elif kind == 'stall':
        await o.usage(p['id'], Usage(agent=j['agent'], correlation_id=j['id'], event_id=uid(), kind='stall', detail='Fixture made no progress'))
    else:
        for i in range(40):
            t = await app.state.tasks.get(j['task_id'])
            if t['status'] == 'blocked':
                break
            u = Usage(agent=j['agent'], correlation_id=j['id'], event_id='tool-' + str(i), kind='tool')
            await o.usage(p['id'], u)
            assert await o.usage(p['id'], u) == {'duplicate': True}
    p = await o.get(p['id'])
    t = await app.state.tasks.get(j['task_id'])
    assert p['state'] == 'paused' and t['status'] == 'blocked'
    restarted = Orchestrator(app, {'codex': codex})
    await restarted.initialize()
    assert (await app.state.tasks.get(t['id']))['tool_calls'] == t['tool_calls']
    assert (await restarted.get(p['id']))['state'] == 'paused'


async def test_wrong_agent_stale_commit_and_out_of_scope_completion(system):
    app, o, repo, codex, antigravity = system
    await start(system)
    p = await pump(system, until=lambda p: p['state'] == 'execution')
    j = next(j for j in p['jobs'] if j['phase'] == 'execution' and j['status'] == 'pending')
    body = dict(request_id=uid(), correlation_id=j['id'], phase=j['phase'], task_id=j['task_id'], decision_id=j['decision_id'], commit=j['commit'], payload={'commit': 'a'*40, 'summary': 'not real'})
    for agent in ['antigravity', 'codex']:
        with pytest.raises(DecisionError):
            await o.report(p['id'], AgentReport(agent=agent, **body))
    t = await app.state.tasks.get(j['task_id'])
    path = Path(t['worktree_path'])
    (path / 'outside.txt').write_text('outside declared scope')
    ws = GitWorkspace(repo)
    await ws.git('add', 'outside.txt', cwd=path)
    await ws.git('commit', '-m', 'Out of scope fixture', cwd=path)
    body['payload']['commit'] = await ws.head(t['branch'])
    with pytest.raises(DecisionError):
        await o.report(p['id'], AgentReport(agent='codex', **body))
    assert (await app.state.tasks.get(t['id']))['status'] == 'in_progress'


async def test_restart_pauses_uncertain_execution_without_replay(system):
    app, o, repo, codex, antigravity = system
    await start(system)
    p = await pump(system, until=lambda p: p['state'] == 'execution')
    before = await GitWorkspace(repo).head('main')
    calls = len(codex.calls)
    restarted = Orchestrator(app, {'codex': codex})
    await restarted.initialize()
    recovered = await restarted.get(p['id'])
    assert recovered['state'] == 'paused' and recovered['uncertain']
    await restarted.tick()
    await restarted.dispatch()
    assert len(codex.calls) == calls and await GitWorkspace(repo).head('main') == before
    with pytest.raises(DecisionError):
        await restarted.control(p['id'], 'resume', ProjectControl(request_id=uid(), expected_revision=recovered['revision'], reason='No blind replay'))


async def test_real_promotion_followed_by_crash_is_not_replayed(system, monkeypatch):
    app, o, repo, codex, antigravity = system
    await start(system)
    p = await pump(system, until=lambda p: p['state'] == 'approval')
    a = next(a for a in await app.state.guard.list() if a['id'] == p['integration']['approval_id'])
    await app.state.guard.decide(a['id'], ApprovalDecision(fingerprint=a['fingerprint'], decision='approved', reason='Exact fixture tests inspected'))
    original = app.state.runner.integrate
    async def crash_after(*args):
        result = await original(*args)
        assert result['promoted']
        raise RuntimeError('Simulated loss after promotion')
    monkeypatch.setattr(app.state.runner, 'integrate', crash_after)
    await o.tick()
    main = await GitWorkspace(repo).head('main')
    p = await o.get(p['id'])
    assert p['state'] == 'paused' and p['uncertain']
    restarted = Orchestrator(app, {'codex': codex})
    await restarted.initialize()
    await restarted.tick()
    assert await GitWorkspace(repo).head('main') == main
    rows = await (await o.db.connection.execute('SELECT * FROM integration_attempts')).fetchall()
    assert len(rows) == 1


async def test_conflicting_start_validation_and_locked_source_endpoint(system):
    app, o, repo, codex, antigravity = system
    request = StartProject(request_id=uid(), goal='build a small login feature', repository=str(repo), test_command=[sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests'])
    await app.state.memory.set_source('PROJECT_SPEC.md', 'Older project must not enter new context')
    p = await o.start(request)
    rendered = (await app.state.memory.context('PROJECT_SPEC.md'))['content']
    assert p['goal'] in rendered and 'Older project must not enter new context' not in rendered
    assert await o.start(request) == p
    with pytest.raises(DecisionError):
        await o.start(request.model_copy(update={'request_id': uid(), 'goal': 'another project'}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        r = await client.put('/api/context/PROJECT_SPEC.md/source', json={'content': 'bypass agreed requirements'})
        assert r.status_code == 409
        r = await client.post('/api/tasks', json=dict(request_id=uid(), title='Bypass project', description='Bypass lifecycle', touched_files_scope=['x.txt'], acceptance_criteria=['x']))
        assert r.status_code == 409


@pytest.mark.parametrize('bad', ['cycle', 'missing', 'scope', 'empty', 'routing'])
async def test_invalid_plan_creates_no_tasks(system, bad):
    app, o, repo, codex, antigravity = system
    await start(system)
    p = await pump(system, until=lambda p: p['state'] == 'planning')
    j = next(j for j in p['jobs'] if j['phase'] == 'plan')
    data = await codex.request(j, str(repo))
    if bad == 'cycle': data['tasks'][1]['dependencies'] = ['form']
    elif bad == 'missing': data['tasks'][0]['dependencies'] = ['absent']
    elif bad == 'scope': data['tasks'][0]['touched_files_scope'] = ['../escape']
    elif bad == 'empty': data['tasks'][0]['acceptance_criteria'] = ['  ']
    else: data['tasks'][0]['assigned_agent'] = 'human'
    report = AgentReport(request_id=uid(), correlation_id=j['id'], agent=j['agent'], phase='plan', decision_id=j['decision_id'], payload=data)
    try:
        await o.report(p['id'], report)
    except DecisionError:
        pass
    assert await app.state.tasks.list() == []


async def test_production_execution_is_explicit_manual_handoff(system):
    app, o, repo, codex, antigravity = system
    o.fixture_execution = False
    await start(system)
    p = await pump(system)
    assert p['state'] == 'paused' and 'Manual execution required' in p['reason']
    assert not any(c['phase'] == 'execution' for c in codex.calls + antigravity.calls)
    p = await o.control(p['id'], 'resume', ProjectControl(request_id=uid(), expected_revision=p['revision'], reason='Operator inspected assigned scope', manual_handoff=True))
    await o.tick()
    p = await o.get(p['id'])
    assert p['state'] == 'execution'
    # Even a registered real Codex adapter is never dispatched for production edits.
    assert any(j['phase'] == 'execution' and j['status'] == 'pending' for j in p['jobs'])

async def test_exact_review_commit_and_legacy_review_cannot_authorize(system):
    from backend.models import MessageCreate
    app, o, repo, codex, antigravity = system
    await start(system)
    p = await pump(system, until=lambda p: p['state'] == 'review')
    j = next(j for j in p['jobs'] if j['phase'] == 'review' and j['status'] == 'pending')
    request = AgentReport(request_id=uid(), correlation_id=j['id'], agent=j['agent'], phase=j['phase'], task_id=j['task_id'], decision_id=j['decision_id'], commit='f'*40, payload={'verdict': 'APPROVED', 'feedback': 'wrong commit'})
    with pytest.raises(DecisionError):
        await o.report(p['id'], request)
    with pytest.raises(DecisionError):
        await o.report(p['id'], request.model_copy(update={'request_id': uid(), 'agent': 'codex', 'commit': j['commit']}))
    await o.db.create_message(MessageCreate(sender='antigravity', recipient='codex', type='review', content='approved'))
    await o.tick()
    assert not await app.state.guard.list()
    task = await app.state.tasks.get(j['task_id'])
    assert task['review']['verdict'] is None
    # An exact commit changing after review invalidates it through the M3 runner.
    await deliver(o, p, j, antigravity)
    task = await app.state.tasks.get(j['task_id'])
    path = Path(task['worktree_path'])
    (path / 'login.py').write_text((path / 'login.py').read_text() + '# new unreviewed revision\n')
    ws = GitWorkspace(repo)
    await ws.git('add', 'login.py', cwd=path)
    await ws.git('commit', '-m', 'Change after review', cwd=path)
    await o.tick()
    assert (await app.state.tasks.get(task['id']))['review'] is None
    assert not await app.state.guard.list()


async def test_exact_action_changed_fingerprint_and_denial_block_execution(system):
    app, o, repo, codex, antigravity = system
    await start(system)
    p = await pump(system, until=lambda p: p['state'] == 'approval')
    before = await GitWorkspace(repo).head('main')
    a = (await app.state.guard.list())[0]
    with pytest.raises(DecisionError):
        await app.state.guard.decide(a['id'], ApprovalDecision(fingerprint='0'*64, decision='approved', reason='Wrong fingerprint'))
    await app.state.guard.decide(a['id'], ApprovalDecision(fingerprint=a['fingerprint'], decision='denied', reason='Operator denied exact action'))
    await o.tick()
    p = await o.get(p['id'])
    assert p['state'] == 'paused'
    assert await GitWorkspace(repo).head('main') == before
    assert not await (await o.db.connection.execute('SELECT * FROM integration_attempts')).fetchall()


async def test_database_reopen_preserves_pending_analysis_and_request_replay(tmp_path):
    repo = await make_repo(tmp_path)
    db = tmp_path / 'state.sqlite'
    codex, antigravity = FakeCodex(), FakeAntigravity()
    app = create_app(db, adapters={'codex': codex}, orchestration_worker=False)
    request = StartProject(request_id=uid(), goal='build a small login feature', repository=str(repo), test_command=[sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests'])
    async with app.router.lifespan_context(app):
        o = app.state.orchestrator
        p = await o.start(request)
        p, report = await deliver(o, p, p['jobs'][0], codex)
        ident = p['id']
    app2 = create_app(db, adapters={'codex': codex}, orchestration_worker=False)
    async with app2.router.lifespan_context(app2):
        o = app2.state.orchestrator
        assert await o.get(ident) == p
        assert await o.report(ident, report) == p
        await o.dispatch()
        assert len(codex.calls) == 1
        assert len((await o.db.inbox('antigravity'))) == 1


async def test_explicit_pause_resume_cancel_and_http_validation(system):
    app, o, repo, codex, antigravity = system
    p = await start(system)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
        assert (await c.get('/api/projects')).json()['projects'][0]['id'] == p['id']
        assert p['id'] in (await c.get(f"/api/projects/{p['id']}/activation")).json()['prompt']
        paused = await c.post(f"/api/projects/{p['id']}/pause", json={'request_id': uid(), 'expected_revision': p['revision'], 'reason': 'Operator pause'})
        assert paused.status_code == 200 and paused.json()['state'] == 'paused'
        await o.tick()
        assert not codex.calls
        j = p['jobs'][0]
        invalid = await c.post(f"/api/projects/{p['id']}/reports", json={'request_id': uid(), 'agent': 'codex', 'phase': 'analysis', 'correlation_id': j['id'], 'payload': {'text': 'generic'}})
        assert invalid.status_code == 409
        p = paused.json()
        body = {'request_id': uid(), 'expected_revision': p['revision'], 'reason': 'Operator resume'}
        resumed = await c.post(f"/api/projects/{p['id']}/resume", json=body)
        assert resumed.status_code == 200
        assert (await c.post(f"/api/projects/{p['id']}/resume", json=body)).json() == resumed.json()
        invalid = await c.post(f"/api/projects/{p['id']}/reports", json={'request_id': uid(), 'agent': 'codex', 'phase': 'analysis', 'correlation_id': j['id'], 'payload': {'text': 'generic'}})
        assert invalid.status_code == 422
        p = resumed.json()
        cancelled = await c.post(f"/api/projects/{p['id']}/cancel", json={'request_id': uid(), 'expected_revision': p['revision'], 'reason': 'Operator cancellation'})
        assert cancelled.status_code == 200 and cancelled.json()['state'] == 'cancelled'
        await o.dispatch()
        await o.tick()
        assert not codex.calls


async def test_target_requires_explicit_clean_external_repository(system):
    app, o, repo, codex, antigravity = system
    for target, command in [(str(Path(__file__).resolve().parents[1]), [sys.executable]), (str(repo), ['missing-devmesh-executable'])]:
        with pytest.raises(DecisionError):
            await o.start(StartProject(request_id=uid(), goal='build login', repository=target, test_command=command))
    assert not await o.list()
    ws = GitWorkspace(repo)
    await ws.git('switch', 'main')
    with pytest.raises(DecisionError):
        await start(system)
    assert not await o.list()

async def test_uncertain_integration_result_pauses_before_retry(system, monkeypatch):
    app, o, repo, codex, antigravity = system
    await start(system)
    p = await pump(system, until=lambda p: p['state'] == 'approval')
    a = (await app.state.guard.list())[0]
    await app.state.guard.decide(a['id'], ApprovalDecision(fingerprint=a['fingerprint'], decision='approved', reason='Exact fixture action inspected'))
    called = []
    async def uncertain(*args):
        called.append(args)
        return {'promoted': False, 'candidate': None, 'test_log': 'Subprocess cleanup incomplete; operator inspection required', 'task': await app.state.tasks.get(p['tasks'][0]['id'])}
    monkeypatch.setattr(app.state.runner, 'integrate', uncertain)
    await o.tick()
    after = await o.get(p['id'])
    assert after['state'] == 'paused' and after['uncertain']
    await o.tick()
    assert len(called) == 1

async def test_concurrent_database_starts_cannot_mix_project_memory(tmp_path):
    repo = await make_repo(tmp_path)
    db = tmp_path / 'singleton.sqlite'
    first = create_app(db, adapters={}, orchestration_worker=False)
    second = create_app(db, adapters={}, orchestration_worker=False)
    async with first.router.lifespan_context(first), second.router.lifespan_context(second):
        def body(goal):
            return StartProject(request_id=uid(), goal=goal, repository=str(repo), test_command=[sys.executable, '-m', 'unittest'])
        results = await asyncio.gather(first.state.orchestrator.start(body('first goal')), second.state.orchestrator.start(body('second goal')), return_exceptions=True)
        assert sum(isinstance(r, dict) for r in results) == 1
        assert sum(isinstance(r, DecisionError) for r in results) == 1
        assert len(await first.state.orchestrator.list()) == 1

async def test_pause_during_submitted_read_only_turn_requires_inspection(system):
    app, o, repo, codex, antigravity = system
    p = await start(system)
    p['jobs'][0]['status'] = 'running'
    await o.save(p)
    p = await o.control(p['id'], 'pause', ProjectControl(request_id=uid(), expected_revision=p['revision'], reason='Operator stops during submitted turn'))
    assert p['uncertain'] and p['state'] == 'paused'
    with pytest.raises(DecisionError):
        await o.control(p['id'], 'resume', ProjectControl(request_id=uid(), expected_revision=p['revision'], reason='Must inspect submitted turn first'))
