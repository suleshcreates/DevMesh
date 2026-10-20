"""M3 uses only disposable Git repositories, SQLite, and harmless Python commands."""
import asyncio
import json
import sys
from uuid import uuid4
from pathlib import Path
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from backend.database import Database
from backend.decision_engine import DecisionError, DecisionEngine
from backend.task_planner import TaskPlanner
from backend.task_models import TaskCreate, TaskStatus, ReviewFeedback, Challenge, ApprovalDecision
from backend.security_guard import ApprovalGuard, action_fingerprint, destructive_reasons
from backend.review_runner import ReviewRunner
from backend.git_workspace import GitWorkspace, GitError, process
from backend.memory_manager import MemoryManager
from backend.main import create_app


def ident():
    return str(uuid4())

def request(**overrides):
    data = dict(request_id=ident(), title='Implement calculation', description='Calculate a result',
                touched_files_scope=['a.txt'], acceptance_criteria=['Calculation is correct'])
    data.update(overrides)
    return TaskCreate(**data)

@pytest_asyncio.fixture
async def system(tmp_path):
    repo = tmp_path / 'repo with spaces'
    repo.mkdir()
    code, _, _ = await process(['git', 'init', '-b', 'main'], repo)
    assert code == 0
    git = GitWorkspace(repo)
    (repo / '.gitignore').write_text('/.worktrees/\n')
    (repo / 'base.txt').write_text('base\n')
    await git.git('add', '.')
    await git.git('commit', '-m', 'base')
    await git.git('switch', '-c', 'development')
    db = Database(tmp_path / 'db.sqlite')
    await db.open()
    planner = TaskPlanner(db)
    guard = ApprovalGuard(db)
    await planner.initialize()
    await guard.initialize()
    runner = ReviewRunner(planner, guard, repo, [sys.executable, '-c', 'print("tests passed")'])
    try:
        yield planner, guard, runner, git
    finally:
        await db.close()

async def working(system, **kwargs):
    p, g, r, git = system
    t = await p.create(request(**kwargs))
    for state in ('assigned', 'in_progress'):
        t = await p.update(t['id'], TaskStatus(request_id=ident(), agent=t['assigned_agent'], expected_revision=t['revision'], status=state))
    t = await r.workspace_for(t['id'], ident())
    return t

async def commit(r, t, name='a.txt', content='change\n'):
    path = Path(t['worktree_path'])
    (path / name).parent.mkdir(parents=True, exist_ok=True)
    (path / name).write_text(content)
    await r.workspace.git('add', '--', name, cwd=path)
    await r.workspace.git('commit', '-m', 'task change', cwd=path)

async def reviewed(system, **kwargs):
    p, g, r, git = system
    t = await working(system, **kwargs)
    await commit(r, t, t['touched_files_scope'][0])
    t = await r.review_ready(t['id'], ident())
    return await r.feedback(t['id'], ReviewFeedback(request_id=ident(), agent=t['review']['reviewer'],
                          commit=t['review']['commit'], verdict='APPROVED', feedback='Peer verified changes'))

async def permit(g, action):
    a = await g.request(action)
    await g.decide(a['id'], ApprovalDecision(fingerprint=a['fingerprint'], decision='approved', reason='Inspected exact action in disposable repository'))
    return a['id']

async def integrate(system, t, key=None):
    p, g, r, git = system
    key = key or ident()
    action = (await r.integration_action(t['id'], key))['action']
    approval = await permit(g, action)
    return await r.integrate(t['id'], key, approval)

async def test_persistence_dependencies_routing_and_memory(system, tmp_path):
    p, g, r, git = system
    first = await p.create(request(category='frontend'))
    assert first['assigned_agent'] == 'antigravity'
    second = await p.create(request(dependencies=[first['id']]))
    assert second['assigned_agent'] == 'codex'
    with pytest.raises(DecisionError):
        await p.update(second['id'], TaskStatus(request_id=ident(), agent='codex', expected_revision=1, status='assigned'))
    with pytest.raises(DecisionError):
        await p.create(request(dependencies=['TASK-999']))
    await DecisionEngine(p.db).initialize()
    memory = MemoryManager(p.db, tmp_path / 'memory')
    text = (await memory.context('TASKS.md'))['content']
    assert first['id'] in text and second['id'] in text
    (tmp_path / 'memory' / 'TASKS.md').write_text('fabricated')
    assert (await memory.context('TASKS.md'))['content'] == text
    db2 = Database(p.db.path)
    await db2.open()
    try:
        other = TaskPlanner(db2)
        await other.initialize()
        assert await other.get(first['id']) == first
    finally:
        await db2.close()

@pytest.mark.parametrize('category,agent', [('backend','codex'),('database','codex'),('logic','codex'),('debugging','codex'),('unit_tests','codex'),('ui_ux','antigravity'),('frontend','antigravity'),('browser_qa','antigravity')])
async def test_default_routing(system, category, agent):
    assert (await system[0].create(request(category=category)))['assigned_agent'] == agent

async def test_transitions_and_challenge(system):
    p, g, r, git = system
    t = await p.create(request())
    for state in ('complete', 'review', 'testing', 'in_progress'):
        with pytest.raises(DecisionError):
            await p.update(t['id'], TaskStatus(request_id=ident(), agent='codex', expected_revision=1, status=state))
    for agent in ('codex', 'antigravity'):
        body = Challenge(request_id=ident(), agent=agent, content='API_CONTRACT_CHALLENGE: preserve current contract until resolved')
        a = await p.challenge(t['id'], body)
        assert await p.challenge(t['id'], body) == a
    assert len(await p.db.inbox('antigravity')) == len(await p.db.inbox('codex')) == 1

async def test_two_worktrees_existing_and_scope(system):
    p, g, r, git = system
    t = await working(system)
    u = await working(system, category='frontend', touched_files_scope=['b.txt'])
    await commit(r, t)
    await commit(r, u, 'b.txt', 'second\r\n')
    assert await git.check_scope(t) == ['a.txt']
    assert await git.check_scope(u) == ['b.txt']
    assert (await r.workspace_for(t['id'], ident()))['worktree_path'] == t['worktree_path']
    for bad in ('../escape', 'C:/outside', '/absolute', '.git/config', 'b.txt'):
        with pytest.raises(DecisionError):
            git.write_path(t, bad)
    await commit(r, t, 'outside.txt')
    with pytest.raises(DecisionError):
        await git.check_scope(t)

async def test_overlap_idempotent_serialization(system):
    p, g, r, git = system
    t = await working(system)
    u = await working(system, touched_files_scope=['b.txt'])
    await commit(r, t)
    await commit(r, u, 'a.txt')
    for _ in range(2):
        with pytest.raises(DecisionError):
            await git.overlap(t, p)
    tasks = await p.list()
    assert len(tasks) == 3
    assert tasks[-1]['status'] == 'blocked'

async def test_review_authority_redaction_invalidation(system):
    p, g, r, git = system
    t = await working(system)
    await commit(r, t, content='password=sk-sensitive123456789\n')
    t = await r.review_ready(t['id'], ident())
    assert 'sk-sensitive' not in json.dumps(t)
    assert len(await p.db.inbox('antigravity')) == 1
    assert (await r.review_ready(t['id'], ident())) == t
    for agent, sha in [('codex', t['review']['commit']), ('antigravity', '0'*40)]:
        with pytest.raises(DecisionError):
            await r.feedback(t['id'], ReviewFeedback(request_id=ident(), agent=agent, commit=sha, verdict='APPROVED', feedback='Reviewed'))
    await r.feedback(t['id'], ReviewFeedback(request_id=ident(), agent='antigravity', commit=t['review']['commit'], verdict='APPROVED', feedback='Reviewed'))
    await commit(r, t, content='new commit\n')
    refreshed = await r.refresh(await p.get(t['id']))
    assert refreshed['review'] is None and refreshed['status'] == 'in_progress'
    with pytest.raises(DecisionError):
        await r.integrate(t['id'], ident(), 'missing')

async def test_promotion_and_duplicate_integration(system):
    p, g, r, git = system
    before = await git.head('main')
    t = await reviewed(system)
    key = ident()
    result = await integrate(system, t, key)
    assert result['promoted'] and result['task']['status'] == 'complete'
    assert await git.head('main') == result['candidate'] != before
    assert await r.integrate(t['id'], key, 'not-used-again') == result
    assert (await p.get(t['id']))['retry_count'] == 0

async def test_missing_review_and_missing_operator_approval(system):
    p, g, r, git = system
    t = await working(system)
    await commit(r, t)
    before = await git.head('main')
    t = await r.review_ready(t['id'], ident())
    with pytest.raises(DecisionError):
        await r.integrate(t['id'], ident(), 'missing')
    t = await r.feedback(t['id'], ReviewFeedback(request_id=ident(), agent='antigravity', commit=t['review']['commit'], verdict='APPROVED', feedback='Reviewed'))
    with pytest.raises(DecisionError):
        await r.integrate(t['id'], ident(), 'missing')
    assert await git.head('main') == before

async def test_failed_tests_retries_and_duplicates(system):
    p, g, r, git = system
    r.test_command = [sys.executable, '-c', 'print("sk-testsecret123456789"); raise SystemExit(1)']
    t = await reviewed(system)
    before = await git.head('main')
    for count in range(1, 4):
        key = ident()
        result = await integrate(system, t, key)
        assert result['task']['retry_count'] == count
        assert not result['promoted']
        assert 'sk-testsecret' not in json.dumps(result)
        assert await r.integrate(t['id'], key, 'duplicate') == result
        assert await git.head('main') == before
        if count < 3:
            t = await r.review_ready(t['id'], ident())
            t = await r.feedback(t['id'], ReviewFeedback(request_id=ident(), agent='antigravity', commit=t['review']['commit'], verdict='APPROVED', feedback='Reviewed again'))
    assert result['task']['status'] == 'blocked'
    assert await p.db.inbox('human')

async def test_changes_requested_is_single_failure(system):
    p, g, r, git = system
    t = await working(system)
    await commit(r, t)
    t = await r.review_ready(t['id'], ident())
    body = ReviewFeedback(request_id=ident(), agent='antigravity', commit=t['review']['commit'], verdict='CHANGES_REQUESTED', feedback='Add boundary handling')
    a = await r.feedback(t['id'], body)
    assert await r.feedback(t['id'], body) == a
    assert a['retry_count'] == 1 and a['status'] == 'in_progress'

@pytest.mark.parametrize('target', ['main', 'task', 'candidate'])
async def test_concurrent_ref_change_prevents_promotion(system, monkeypatch, target):
    import backend.review_runner as module
    p, g, r, git = system
    t = await reviewed(system)
    before = await git.head('main')
    original = module.process
    changed = None
    async def fake(args, path, **kwargs):
        nonlocal changed
        result = await original(args, path, **kwargs)
        if target == 'task':
            await commit(r, t, content='changed during testing\n')
            changed = await git.head(t['branch'])
        elif target == 'candidate':
            await git.git('commit', '--allow-empty', '-m', 'concurrent candidate', cwd=path)
        else:
            changed = await git.git('commit-tree', (await git.git('rev-parse', before + '^{tree}')), '-p', before, '-m', 'concurrent main')
            await git.git('update-ref', 'refs/heads/main', changed, before)
        return result
    monkeypatch.setattr(module, 'process', fake)
    result = await integrate(system, t)
    assert not result['promoted']
    assert await git.head('main') == (changed if target == 'main' else before)

async def test_approval_binding_audit_redaction_and_denial(system):
    p, g, r, git = system
    action = dict(kind='command', arguments=['echo', 'password=sk-secret123456789'], repository=str(git.root), worktree=str(git.root / '.worktrees' / 'test'), scope=['a.txt'], target=None)
    a = await g.request(action)
    called = []
    async def execute():
        called.append(True)
        return {'output': 'password=sk-secret123456789'}
    with pytest.raises(DecisionError):
        await g.execute(a['id'], action, execute)
    await g.decide(a['id'], ApprovalDecision(fingerprint=a['fingerprint'], decision='approved', reason='Specific local action'))
    for field, value in [('arguments', ['echo','changed']), ('scope',['b.txt']), ('repository',str(git.root.parent)), ('worktree',str(git.root))]:
        with pytest.raises(DecisionError):
            await g.execute(a['id'], {**action, field: value}, execute)
    await g.execute(a['id'], action, execute)
    with pytest.raises(DecisionError):
        await g.execute(a['id'], action, execute)
    assert called == [True]
    rows = await (await p.db.connection.execute('SELECT * FROM audit_logs')).fetchall()
    assert 'sk-secret' not in str([dict(row) for row in rows])
    for sql in ('UPDATE audit_logs SET event=\'changed\'', 'DELETE FROM audit_logs'):
        with pytest.raises(Exception):
            await p.db.connection.execute(sql)
        await p.db.connection.rollback()
    a = await g.request(action)
    await g.decide(a['id'], ApprovalDecision(fingerprint=a['fingerprint'], decision='denied', reason='Do not execute'))
    with pytest.raises(DecisionError):
        await g.execute(a['id'], action, execute)

@pytest.mark.parametrize('args', [['DROP','TABLE','records'],['rm','-rf','data'],['Remove-Item','-Recurse','data'],['deploy','production'],['git','reset','--hard']])
def test_destructive_detection(args):
    assert destructive_reasons(dict(kind='command', arguments=args, target=None))

async def test_recovery_blocks_uncertain_attempt(system):
    p, g, r, git = system
    t = await reviewed(system)
    await p.db.connection.execute('INSERT INTO integration_attempts VALUES (?,?,?,?)', ('interrupted',t['id'],'running','{}'))
    await p.db.connection.commit()
    await r.recover()
    await r.recover()
    assert (await p.get(t['id']))['status'] == 'blocked'
    assert len(await p.db.inbox('human')) == 1

def test_rest_schemas_and_generated_tasks(tmp_path):
    with TestClient(create_app(tmp_path / 'api.sqlite')) as client:
        t = client.post('/api/tasks', json=request().model_dump()).json()
        assert t['status'] == 'planned'
        assert t['id'] in client.get('/api/context/TASKS.md').json()['content']
        assert client.get('/api/tasks/' + t['id']).json() == t
        assert client.post('/api/tasks', json={**request().model_dump(), 'status':'complete'}).status_code == 422
        assert client.post('/api/tasks/'+t['id']+'/status', json=dict(request_id=ident(),agent='codex',expected_revision=1,status='complete')).status_code == 409
        assert client.get('/api/tasks/TASK-999').status_code == 404
        assert client.post('/api/approvals', json={'approved':True}).status_code == 422
        assert client.get('/api/audit-logs').json() == {'audit_logs':[]}

async def test_tool_budget_is_persisted_and_replay_is_free(system):
    p, g, r, git = system
    t = await working(system)
    async with p.db.lock:
        t['tool_calls'] = 39
        await p.save(p.db.connection, t)
        await p.db.connection.commit()
    b = Challenge(request_id=ident(), agent='codex', content='Confirm API contract')
    first = await p.challenge(t['id'], b)
    assert await p.challenge(t['id'], b) == first
    assert (await p.get(t['id']))['tool_calls'] == 40
    limited = await p.challenge(t['id'], Challenge(request_id=ident(), agent='antigravity', content='Needs human decision'))
    assert limited['status'] == 'blocked'
    assert len(await p.db.inbox('human')) == 1

async def test_approval_is_invalidated_by_different_reviewed_commit(system):
    p, g, r, git = system
    t = await reviewed(system)
    key = ident()
    a = (await r.integration_action(t['id'], key))['action']
    approval = await permit(g, a)
    await commit(r, t, content='another reviewed change\n')
    await r.refresh(await p.get(t['id']))
    t = await r.review_ready(t['id'], ident())
    t = await r.feedback(t['id'], ReviewFeedback(request_id=ident(), agent='antigravity', commit=t['review']['commit'], verdict='APPROVED', feedback='Reviewed new commit'))
    with pytest.raises(DecisionError):
        await r.integrate(t['id'], key, approval)

async def test_destructive_actions_never_execute_before_exact_approval(system):
    p, g, r, git = system
    calls = []
    async def harmless_callback():
        calls.append(True)
        return {'simulated': True}
    cases = [
        dict(kind='command', arguments=['DROP', 'TABLE', 'data']),
        dict(kind='command', arguments=['rm', '-rf', 'data']),
        dict(kind='write', arguments=['replace'], target=str(git.root / '.git' / 'config')),
        dict(kind='write', arguments=['write'], target=str(git.root.parent / 'outside.txt')),
        dict(kind='deploy', arguments=['deploy', 'production']),
    ]
    for case in cases:
        action = dict(repository=str(git.root), worktree=str(git.root / '.worktrees' / 'TASK-001'), scope=['a.txt'], target=None)
        action.update(case)
        a = await g.request(action)
        assert destructive_reasons(action)
        with pytest.raises(DecisionError):
            await g.execute(a['id'], action, harmless_callback)
        assert calls == []
    async with p.db.lock:
        rows = await (await p.db.connection.execute('SELECT event FROM audit_logs')).fetchall()
    assert sum(row[0] == 'execution_attempt' for row in rows) == len(cases)

@pytest.mark.parametrize('scope', ['../a', 'C:\\a', 'a/../b', '.git/config', '.worktrees/a', 'CON', 'a./x', 'a /x', 'a\x00b'])
async def test_windows_scope_rejection(system, scope):
    with pytest.raises(DecisionError):
        await system[0].create(request(touched_files_scope=[scope]))

async def test_intentional_overlap_needs_declared_order(system):
    p, g, r, git = system
    t = await working(system)
    u = await working(system)
    await commit(r, t, content='one\n')
    await commit(r, u, content='two\n')
    with pytest.raises(DecisionError):
        await git.overlap(t, p)
    assert len(await p.list()) == 3

async def test_existing_branch_and_invalid_repository(system, tmp_path):
    p, g, r, git = system
    t = await p.create(request())
    await git.git('branch', 'feat/codex-' + t['id'])
    for state in ('assigned', 'in_progress'):
        t = await p.update(t['id'], TaskStatus(request_id=ident(), agent='codex', expected_revision=t['revision'], status=state))
    t = await r.workspace_for(t['id'], ident())
    assert await git.head(t['branch'])
    with pytest.raises((DecisionError, GitError)):
        await GitWorkspace(tmp_path / 'missing').validate()
    with pytest.raises(GitError):
        await git.git('show-ref', '--verify', 'refs/heads/missing')

async def test_checked_out_main_and_dirty_workspace_block_integration(system):
    p, g, r, git = system
    t = await reviewed(system)
    before = await git.head('main')
    await git.git('switch', 'main')
    with pytest.raises(DecisionError):
        await integrate(system, t)
    assert await git.head('main') == before
    await git.git('switch', 'development')
    Path(t['worktree_path'], 'a.txt').write_text('dirty\n')
    with pytest.raises(DecisionError):
        await integrate(system, t)
    assert await git.head('main') == before

async def test_timeout_cleans_subprocess_and_records_failure(system, monkeypatch):
    import backend.review_runner as module
    p, g, r, git = system
    t = await reviewed(system)
    before = await git.head('main')
    async def timeout(*args, **kwargs):
        raise TimeoutError
    monkeypatch.setattr(module, 'process', timeout)
    result = await integrate(system, t)
    assert not result['promoted'] and result['task']['retry_count'] == 1
    assert await git.head('main') == before

async def test_real_harmless_child_timeout(tmp_path):
    with pytest.raises(TimeoutError):
        await process([sys.executable, '-c', 'import time; time.sleep(10)'], tmp_path, timeout=.1)

async def test_casefold_overlap_is_idempotent_from_either_branch(system):
    p, g, r, git = system
    t = await working(system, touched_files_scope=['A.txt'])
    u = await working(system, touched_files_scope=['b.txt'])
    await commit(r, t, 'A.txt')
    await commit(r, u, 'a.txt')
    for task in (t, u, t):
        with pytest.raises(DecisionError):
            await git.overlap(task, p)
    assert len(await p.list()) == 3

async def test_dependency_assignment_after_successful_predecessor(system):
    p, g, r, git = system
    predecessor = await reviewed(system)
    t = await p.create(request(dependencies=[predecessor['id']], touched_files_scope=['b.txt']))
    body = TaskStatus(request_id=ident(), agent='codex', expected_revision=t['revision'], status='assigned')
    with pytest.raises(DecisionError):
        await p.update(t['id'], body)
    assert (await integrate(system, predecessor))['promoted']
    assert (await p.update(t['id'], body))['status'] == 'assigned'

async def test_rest_approval_and_integration_contract(system):
    p, g, r, git = system
    with TestClient(create_app(p.db.path, git.root, r.test_command)) as client:
        def post(path, body):
            response = client.post(path, json=body)
            assert response.status_code in (200, 201), response.text
            return response.json()
        t = post('/api/tasks', request().model_dump())
        for state in ('assigned', 'in_progress'):
            t = post(f'/api/tasks/{t["id"]}/status', dict(request_id=ident(), agent='codex', expected_revision=t['revision'], status=state))
        t = post(f'/api/tasks/{t["id"]}/workspace', dict(request_id=ident()))
        await commit(r, t)
        t = post(f'/api/tasks/{t["id"]}/review', dict(request_id=ident()))
        t = post(f'/api/tasks/{t["id"]}/feedback', dict(request_id=ident(), agent='antigravity', commit=t['review']['commit'], verdict='APPROVED', feedback='REST peer test'))
        key = ident()
        planned = post(f'/api/tasks/{t["id"]}/integration-action', dict(request_id=key))
        a = post('/api/approvals', planned['action'])
        assert client.post(f'/api/tasks/{t["id"]}/integrate', json=dict(request_id=key, approval_id=a['id'])).status_code == 409
        post(f'/api/approvals/{a["id"]}/decision', dict(fingerprint=a['fingerprint'], decision='approved', reason='Inspected disposable test'))
        result = post(f'/api/tasks/{t["id"]}/integrate', dict(request_id=key, approval_id=a['id']))
        assert result['promoted'] and result['task']['status'] == 'complete'
        assert post(f'/api/tasks/{t["id"]}/integrate', dict(request_id=key, approval_id=a['id'])) == result
        assert 'complete' in client.get('/api/context/TASKS.md').json()['content']
        events = [row['event'] for row in client.get('/api/audit-logs').json()['audit_logs']]
        assert {'pending', 'approved', 'execution_attempt', 'execution_outcome'} <= set(events)
