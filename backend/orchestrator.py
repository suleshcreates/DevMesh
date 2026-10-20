"""Persisted single-project orchestration. M2 and M3 remain the gate owners."""
import asyncio
import hashlib
import json
import shutil
import sqlite3
import time
from pathlib import Path
from uuid import uuid4
from pydantic import ValidationError
from backend.database import now
from backend.decision_models import Proposal, CritiqueRequest, Critique
from backend.decision_engine import DecisionError
from backend.project_models import Analysis, Plan, ExecutionResult, ReviewResult, AgentReport
from backend.task_models import TaskStatus, ReviewFeedback
from backend.task_planner import fail, move, MAX_TOOL_CALLS_PER_TASK, TASK_TIMEOUT_SECONDS
from backend.git_workspace import GitWorkspace
from backend.security_guard import redact
from backend.review_runner import ReviewRunner
from backend.agent_adapters import CodexAdapter

TERMINAL = {'complete', 'cancelled'}

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

def opposite(agent):
    return 'antigravity' if agent == 'codex' else 'codex'

class Orchestrator:
    def __init__(self, app, adapters=None):
        self.s = app.state
        self.db = self.s.db
        self.lock = asyncio.Lock()
        self.adapters = {'codex': CodexAdapter(self.observe_tool)} if adapters is None else adapters
        # Dependency injection is available only in Python, never through REST/env.
        self.fixture_execution = adapters is not None
        self.worker = None

    async def initialize(self):
        async with self.db.lock:
            await self.db.connection.executescript('''
                CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, document TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS project_singleton ON projects((1));
                CREATE TABLE IF NOT EXISTS project_requests (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS project_usage (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL);
            ''')
            await self.db.connection.commit()
        projects = await self.list()
        if projects:
            p = projects[0]
            self.bind(p)
            if p['busy'] or any(j['status'] == 'running' for j in p['jobs']) or p['state'] == 'execution':
                p['uncertain'] = True
                await self.pause(p, 'Interrupted operation; inspect agent session, worktrees and refs. Automatic replay is forbidden.')

    def bind(self, p):
        self.s.runner = ReviewRunner(self.s.tasks, self.s.guard, p['repository'], p['test_command'])

    async def list(self):
        async with self.db.lock:
            rows = await (await self.db.connection.execute('SELECT document FROM projects')).fetchall()
        return [json.loads(r[0]) for r in rows]

    async def get(self, ident):
        for p in await self.list():
            if p['id'] == ident:
                return p
        fail('Project not found', 404)

    async def replay(self, key, payload):
        async with self.db.lock:
            row = await (await self.db.connection.execute('SELECT * FROM project_requests WHERE id=?', (key,))).fetchone()
        if row:
            if row['fingerprint'] != digest(payload):
                fail('Request ID conflict')
            return json.loads(row['response'])

    async def save(self, p, messages=(), receipt=None, response_job=None):
        p['revision'] += 1
        p['updated_at'] = now()
        async with self.db.lock:
            c = self.db.connection
            try:
                await c.execute('BEGIN IMMEDIATE')
                for agent, subject, payload, job in messages:
                    mid = await self.s.tasks.message(c, agent, subject, payload)
                    if job is not None:
                        job['message_id'] = mid
                if response_job is not None:
                    j = response_job
                    response_id = await self.s.tasks.message(c, 'human', 'DEVMESH_' + j['phase'].upper() + '_RESULT',
                        {'project_id': p['id'], 'correlation_id': j['id'], 'decision_id': j['decision_id'], 'task_id': j['task_id'], 'commit': j['commit'], 'payload': j['result']},
                        j['agent'], requires_response=False)
                    j['response_message_id'] = response_id
                    if j['phase'] == 'analysis':
                        p['analyses'][j['agent']]['request_message_id'] = j['message_id']
                        p['analyses'][j['agent']]['message_id'] = response_id
                await c.execute('INSERT INTO projects VALUES (?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document', (p['id'], json.dumps(redact(p))))
                if receipt:
                    key, payload = receipt
                    await c.execute('INSERT INTO project_requests VALUES (?,?,?)', (key, digest(payload), json.dumps(redact(p))))
                await c.commit()
            except BaseException:
                await c.rollback()
                raise
        async with self.s.changed:
            self.s.changed.notify_all()
        await self.s.ws.broadcast({'event': 'project.updated', 'data': {'id': p['id']}}, {'human', 'codex', 'antigravity'})

    async def start(self, request):
        async with self.lock:
            payload = ['start', request.model_dump()]
            prior = await self.replay(request.request_id, payload)
            if prior:
                return prior
            if await self.list():
                fail('This database already owns a project. Use another database for another project; memory isolation is not implemented.')
            root = Path(request.repository)
            implementation = Path(__file__).resolve().parents[1]
            if not root.is_absolute() or root.resolve().is_relative_to(implementation):
                fail('Select an explicit external execution repository; DevMesh itself is forbidden', 422)
            git = GitWorkspace(root)
            await git.ensure_ignored()
            common = (git.root / await git.git('rev-parse', '--git-common-dir')).resolve()
            implementation_git = GitWorkspace(implementation)
            own_common = (implementation / await implementation_git.git('rev-parse', '--git-common-dir')).resolve()
            if common == own_common:
                fail('The implementation repository and its worktrees cannot be execution targets', 422)
            if not request.goal.strip():
                fail('Project goal must not be blank', 422)
            if await git.git('status', '--porcelain', '--untracked-files=all'):
                fail('Execution repository must be clean')
            if 'refs/heads/main' in await git.registrations():
                fail('Switch target to an operator branch; main must not be checked out')
            if await git.git('rev-parse', 'HEAD') != await git.head('main'):
                fail('Target HEAD must match main before starting; unrelated baseline history is forbidden')
            command = request.test_command
            if any(not x.strip() or '\x00' in x for x in command):
                fail('Invalid test command argument array', 422)
            if not shutil.which(command[0]) and not (Path(command[0]).is_absolute() and Path(command[0]).is_file()):
                fail('Test executable unavailable', 422)
            if redact(command) != command or redact(str(root)) != str(root):
                fail('Secret-like execution configuration cannot be stored; select a non-secret command', 422)
            if await self.s.tasks.list():
                fail('Legacy tasks exist; use a fresh database to avoid mixing repositories or task memory')
            p = dict(id=str(uuid4()), goal=redact(request.goal), repository=str(root.resolve()), test_command=command,
                     execution_mode='fixture' if self.fixture_execution else 'manual', state='analysis', phase='analysis',
                     revision=0, reason=None, analyses={}, decision_id=None, decision_ids=[], memory=None, tasks=[], jobs=[],
                     attempts={}, progress={'complete': 0, 'required': 0}, busy=None, uncertain=False,
                     history=[{'state': 'analysis', 'at': now()}], created_at=now())
            messages = [self.job(p, a, 'analysis', {'goal': p['goal']}, Analysis.model_json_schema()) for a in ('codex', 'antigravity')]
            try:
                await self.save(p, messages, (request.request_id, payload))
            except sqlite3.IntegrityError:
                fail('A conflicting project already owns this database')
            self.bind(p)
            return p

    def job(self, p, agent, phase, context, schema, task=None, commit=None):
        j = dict(id=str(uuid4()), project_id=p['id'], agent=agent, phase=phase,
                 task_id=task, decision_id=p['decision_id'], commit=commit, context=context,
                 response_schema=schema, status='pending', created_at=time.time(), message_id=None)
        p['jobs'].append(j)
        # Queue is a durable outbox; polling never acknowledges it.
        return agent, 'DEVMESH_' + phase.upper(), j.copy(), j

    async def pause(self, p, reason, receipt=None):
        if p['state'] != 'paused':
            p['phase'] = p['state']
        p.update(state='paused', reason=reason)
        p['history'].append({'state': 'paused', 'reason': reason, 'at': now()})
        await self.save(p, [(a, 'PROJECT_PAUSED' if a != 'human' else 'HUMAN_APPROVAL_REQUIRED', {'project_id': p['id'], 'reason': reason}, None) for a in ('human', 'codex', 'antigravity')], receipt=receipt)

    async def control(self, ident, action, request):
        async with self.lock:
            payload = [ident, action, request.model_dump()]
            prior = await self.replay(request.request_id, payload)
            if prior:
                return prior
            p = await self.get(ident)
            if p['revision'] != request.expected_revision or p['state'] in TERMINAL:
                fail('Stale revision or terminal project')
            if action == 'cancel':
                p.update(state='cancelled', reason=request.reason)
                for j in p['jobs']:
                    if j['status'] != 'done':
                        j['status'] = 'cancelled'
            elif action == 'pause':
                if p['state'] != 'paused':
                    p['phase'] = p['state']
                p.update(state='paused', reason=request.reason)
                if any(j['status'] == 'running' or (j['phase'] == 'execution' and j['status'] == 'pending') for j in p['jobs']):
                    p['uncertain'] = True
            elif action == 'resume':
                if p['state'] != 'paused' or p['uncertain']:
                    fail('Uncertain operations require inspection and replacement work; cannot automatically resume')
                tasks = [await self.s.tasks.get(t['id']) for t in p['tasks']]
                if any(t['status'] == 'blocked' for t in tasks):
                    fail('Blocked tasks are terminal; plan replacement work in a fresh project database')
                if p['decision_id'] and (await self.s.decisions.get(p['decision_id']))['state'] == 'ESCALATE':
                    fail('Resolve the escalated decision through the operator route before resume')
                p.update(state=p['phase'], reason=None, manual_handoff=request.manual_handoff)
                if p['state'] == 'planning' and any(j['phase'] == 'plan' and j['status'] == 'invalid' for j in p['jobs']):
                    if not any(j['phase'] == 'plan' and j['status'] in ('pending', 'running') for j in p['jobs']):
                        message = self.job(p, 'codex', 'plan', {'memory': p['memory'], 'operator_reask': request.reason}, Plan.model_json_schema())
                        await self.save(p, [message])
            else:
                fail('Unsupported project action', 422)
            p['history'].append({'state': p['state'], 'reason': request.reason, 'at': now()})
            messages = [('antigravity', 'PROJECT_' + action.upper(), {'project_id': ident, 'state': p['state']}, None)]
            await self.save(p, messages, (request.request_id, payload))
            return p

    async def assigned_context(self, ident, correlation_id, agent):
        p = await self.get(ident)
        j = next((j for j in p['jobs'] if j['id'] == correlation_id and j['agent'] == agent), None)
        if not j or j['status'] not in ('pending', 'running') or p['state'] in TERMINAL | {'paused'}:
            fail('No current assignment for this agent/correlation')
        return j

    async def report(self, ident, request):
        async with self.lock:
            payload = [ident, request.model_dump()]
            prior = await self.replay(request.request_id, payload)
            if prior:
                return prior
            p = await self.get(ident)
            if p['state'] in TERMINAL | {'paused'} or p['busy']:
                fail('Project is not accepting responses')
            j = next((j for j in p['jobs'] if j['id'] == request.correlation_id), None)
            if not j or j['status'] not in ('pending', 'running'):
                fail('Stale or unknown correlation')
            for key in ('agent', 'phase', 'task_id', 'decision_id', 'commit'):
                if getattr(request, key) != j[key]:
                    fail('Wrong agent, phase, decision, task or reviewed commit')
            phase = j['phase']
            if phase == 'analysis':
                result = Analysis.model_validate(request.payload).model_dump()
                p['analyses'][j['agent']] = dict(result, message_id=j['message_id'], correlation_id=j['id'])
            elif phase == 'plan':
                try:
                    result = self.validate_plan(request.payload)
                except (ValidationError, ValueError, DecisionError):
                    j.update(status='invalid', result={'error': 'Invalid plan'})
                    await self.pause(p, 'Invalid plan: inspect the supplied schema/dependencies/scope; no tasks were created', receipt=(request.request_id, payload))
                    return p
                p['plan'] = result
            elif phase == 'critique':
                p['busy'] = 'critique'
                await self.save(p)
                d, _ = await self.s.decisions.mutate('critique', CritiqueRequest(
                    request_id=j['id'], agent=j['agent'], expected_revision=j['context']['decision']['revision'],
                    critique_json=json.dumps(request.payload)), p['decision_id'])
                result = {'decision_id': d['id'], 'revision': d['revision']}
            elif phase == 'execution':
                result = ExecutionResult.model_validate(request.payload).model_dump()
                t = await self.s.tasks.get(j['task_id'])
                if t['status'] != 'in_progress':
                    fail('Task is no longer executing')
                if time.time() - p['attempts'][t['id']]['started_at'] >= TASK_TIMEOUT_SECONDS:
                    await self.block(p, t, 'TASK_TIMEOUT_SECONDS')
                    fail('Execution timed out')
                await self.s.runner.workspace.check_scope(t)
                if await self.s.runner.workspace.head(t['branch']) != result['commit']:
                    fail('Completion commit does not match assigned branch')
                if not await self.s.runner.workspace.changed(t['branch']):
                    fail('Completion requires actual scoped changes')
                p['busy'] = 'review-ready'
                await self.save(p)
                async with self.s.runner.lock:
                    await self.s.runner.review_ready(t['id'], j['id'])
            elif phase == 'review':
                result = ReviewResult.model_validate(request.payload).model_dump()
                p['busy'] = 'review-feedback'
                await self.save(p)
                async with self.s.runner.lock:
                    await self.s.runner.feedback(j['task_id'], ReviewFeedback(request_id=j['id'], agent=j['agent'], commit=j['commit'], **result))
            else:
                fail('Unsupported phase', 422)
            j.update(status='done', result=redact(result))
            p['busy'] = None
            await self.save(p, receipt=(request.request_id, payload), response_job=j)
            return p

    def validate_plan(self, payload):
        from backend.git_workspace import validate_scope
        plan = Plan.model_validate(payload).model_dump()['tasks']
        keys = [t['key'] for t in plan]
        if len(set(keys)) != len(keys):
            raise ValueError('Duplicate plan keys')
        ordered, pending = [], list(plan)
        while pending:
            ready = [t for t in pending if all(d in [x['key'] for x in ordered] for d in t['dependencies'])]
            if not ready:
                raise ValueError('Missing or cyclic plan dependencies')
            for t in ready:
                if not all(x.strip() for x in t['acceptance_criteria']) or not t['title'].strip() or not t['description'].strip():
                    raise ValueError('Empty task contract')
                for scope in t['touched_files_scope']:
                    validate_scope(scope)
                ordered.append(t)
                pending.remove(t)
        return ordered

    async def block(self, p, t, reason):
        async with self.db.lock:
            if t['status'] not in ('blocked', 'complete'):
                move(t, 'blocked')
                t['review'] = None
                t['revision'] += 1
                t['review_feedback'].append(reason)
                await self.s.tasks.save(self.db.connection, t)
                await self.db.connection.commit()
        await self.pause(p, reason)

    async def usage(self, ident, request):
        async with self.lock:
            p = await self.get(ident)
            j = next((j for j in p['jobs'] if j['id'] == request.correlation_id), None)
            key = request.correlation_id + ':' + request.event_id
            payload = request.model_dump()
            async with self.db.lock:
                row = await (await self.db.connection.execute('SELECT fingerprint FROM project_usage WHERE id=?', (key,))).fetchone()
            if row:
                if row[0] != digest(payload):
                    fail('Usage event conflict')
                return {'duplicate': True}
            if not j or j['agent'] != request.agent or not j['task_id'] or j['status'] not in ('pending', 'running') or p['state'] in TERMINAL | {'paused'}:
                fail('Stale usage event or wrong agent')
            async with self.db.lock:
                c = self.db.connection
                t = await self.s.tasks._get(c, j['task_id'])
                if t['status'] in ('blocked', 'complete'):
                    fail('Terminal task')
                # Counter, terminal limit state and event deduplication commit together.
                t['tool_calls'] += 1
                reason = ('MAX_TOOL_CALLS_PER_TASK' if t['tool_calls'] >= MAX_TOOL_CALLS_PER_TASK
                          else request.kind + ': ' + redact(request.detail) if request.kind in ('error', 'stall') else None)
                if reason:
                    move(t, 'blocked')
                    t['review'] = None
                    t['revision'] += 1
                    t['review_feedback'].append(reason)
                await self.s.tasks.save(c, t)
                await c.execute('INSERT INTO project_usage VALUES (?,?)', (key, digest(payload)))
                await c.commit()
            p['attempts'].setdefault(t['id'], {})['last_progress'] = {'kind': request.kind, 'detail': redact(request.detail), 'at': now()}
            if reason:
                await self.pause(p, reason)
            else:
                await self.save(p)
            return {'tool_calls': t['tool_calls']}

    async def tick(self):
        async with self.lock:
            projects = await self.list()
            if not projects:
                return
            p = projects[0]
            if p['state'] in TERMINAL | {'paused'}:
                return
            if p['busy']:
                p['uncertain'] = True
                await self.pause(p, 'Incomplete operation reservation; inspect before replacement work. No replay.')
                return
            try:
                # Shares M3's lock with legacy task endpoints; no racing promotions.
                async with self.s.runner.lock:
                    await self.advance(p)
            except Exception as exc:
                p['uncertain'] = bool(p['busy'])
                await self.pause(p, 'Operation stopped: ' + type(exc).__name__ + '; inspect the persisted operation and task context')

    async def advance(self, p):
        state = p['state']
        if state == 'analysis':
            if len(p['analyses']) != 2:
                return
            p['busy'] = 'create-decision'
            await self.save(p)
            a = p['analyses']['codex']
            d, _ = await self.s.decisions.mutate('propose', Proposal(request_id=p['id'], agent='codex', title=p['goal'][:500], content=a['content'], rationale=a['rationale']))
            p.update(decision_id=d['id'], decision_ids=[d['id']], state='critique', busy=None)
            p['analyses']['codex']['decision_id'] = d['id']
            await self.save(p)
        elif state == 'critique':
            d = await self.s.decisions.get(p['decision_id'])
            if d['state'] == 'ESCALATE':
                await self.pause(p, 'Decision escalated; resolve in ApprovalCenter, then explicitly resume')
            elif d['state'] == 'RECORD' and len(p['decision_ids']) == 1:
                # Each independently authored analysis gets an opposite-agent critique
                # under the existing engine. Neither proposal is silently overwritten.
                p['busy'] = 'create-peer-decision'
                await self.save(p)
                a = p['analyses']['antigravity']
                peer, _ = await self.s.decisions.mutate('propose', Proposal(request_id=p['id'] + '-peer', agent='antigravity', title=p['goal'][:480] + ' / peer design', content=a['content'], rationale=a['rationale']))
                p['decision_ids'].append(peer['id'])
                p['analyses']['antigravity']['decision_id'] = peer['id']
                p.update(decision_id=peer['id'], busy=None)
                await self.save(p)
            elif d['state'] == 'RECORD':
                # Immutable project-specific sources; historical M2 records are not requirements.
                content = {'project_id': p['id'], 'goal': p['goal'], 'decision': d, 'decisions': [await self.s.decisions.get(ident) for ident in p['decision_ids']]}
                if not all(record['state'] == 'RECORD' for record in content['decisions']):
                    fail('Both design decisions must resolve before memory lock')
                p['memory'] = {'version': digest(content), 'content': content}
                p['state'] = 'planning'
                await self.save(p, [self.job(p, 'codex', 'plan', {'memory': p['memory']}, Plan.model_json_schema())])
                await self.s.memory.regenerate()
            elif not any(j['phase'] == 'critique' and j['status'] in ('pending', 'running') for j in p['jobs']):
                agent = opposite(d['proposals'][-1]['agent'])
                await self.save(p, [self.job(p, agent, 'critique', {'decision': d, 'independent_analyses': p['analyses'], 'design_decisions': [await self.s.decisions.get(ident) for ident in p['decision_ids']]}, Critique.model_json_schema())])
        elif state == 'planning':
            if 'plan' not in p:
                return
            mapping = {}
            # Whole-plan validation precedes one atomic task-and-project transaction.
            async with self.db.lock:
                c = self.db.connection
                try:
                    await c.execute('BEGIN IMMEDIATE')
                    for spec in p['plan']:
                        data = dict(spec, dependencies=[mapping[k] for k in spec['dependencies']])
                        t = await self.s.tasks.create_in(c, data)
                        mapping[spec['key']] = t['id']
                        p['tasks'].append({'id': t['id'], 'key': spec['key'], 'memory_version': p['memory']['version']})
                    p.update(state='tasks', busy=None)
                    p['progress']['required'] = len(p['tasks'])
                    await c.execute('UPDATE projects SET document=? WHERE id=?', (json.dumps(redact(p)), p['id']))
                    await c.commit()
                except BaseException:
                    await c.rollback()
                    raise
            await self.save(p)
        elif state in ('tasks', 'execution', 'review', 'approval'):
            await self.advance_tasks(p)
        await self.s.memory.context('TASKS.md')

    async def advance_tasks(self, p):
        tasks = [await self.s.runner.refresh(await self.s.tasks.get(link['id'])) for link in p['tasks']]
        stale = False
        for job in p['jobs']:
            if job['phase'] == 'review' and job['status'] in ('pending', 'running'):
                task = next(t for t in tasks if t['id'] == job['task_id'])
                if not task['review'] or task['review']['commit'] != job['commit']:
                    job['status'] = 'stale'
                    stale = True
        if stale:
            await self.save(p)
        p['progress'] = {'complete': sum(t['status'] == 'complete' for t in tasks), 'required': len(tasks)}
        if any(t['status'] == 'blocked' for t in tasks):
            await self.pause(p, 'A required task is terminal blocked; inspect retry, tool or safety limit')
            return
        if tasks and all(t['status'] == 'complete' for t in tasks):
            async with self.db.lock:
                rows = await (await self.db.connection.execute("SELECT document FROM integration_attempts WHERE state='finished'")).fetchall()
            promoted = {json.loads(r[0])['task']['id'] for r in rows if json.loads(r[0]).get('promoted')}
            if not all(t['id'] in promoted for t in tasks):
                fail('Completion requires promotion gates for every task')
            p.update(state='complete', reason='Every required task passed peer review, configured tests and guarded promotion')
            p['history'].append({'state': 'complete', 'reason': p['reason'], 'at': now()})
            await self.save(p, [(a, 'PROJECT_COMPLETE', {'project_id': p['id']}, None) for a in ('human', 'codex', 'antigravity')])
            return
        t = next((t for t in tasks if t['status'] != 'complete' and all(next(x for x in tasks if x['id'] == dep)['status'] == 'complete' for dep in t['dependencies'])), None)
        if not t:
            fail('No eligible task')
        if t['status'] == 'planned':
            p['busy'] = 'workspace'
            await self.save(p)
            t = await self.s.tasks.update(t['id'], TaskStatus(request_id=str(uuid4()), agent=t['assigned_agent'], expected_revision=t['revision'], status='assigned'))
            t = await self.s.runner.workspace_for(t['id'], str(uuid4()))
            # Dependency tasks start at tested main, not the operator branch's old HEAD.
            await self.s.runner.workspace.git('merge', '--ff-only', 'main', cwd=Path(t['worktree_path']))
            p.update(busy=None, state='tasks')
            await self.save(p)
        elif t['status'] in ('assigned', 'in_progress'):
            active = next((j for j in p['jobs'] if j['task_id'] == t['id'] and j['phase'] == 'execution' and j['status'] in ('pending', 'running')), None)
            if active:
                if time.time() - p['attempts'][t['id']]['started_at'] >= TASK_TIMEOUT_SECONDS:
                    await self.block(p, t, 'TASK_TIMEOUT_SECONDS')
                return
            if not self.fixture_execution and not p.get('manual_handoff'):
                p['state'] = 'tasks'
                await self.pause(p, 'Manual execution required. Inspect assigned worktree and scope, resume with manual_handoff, perform assigned edits manually, and report the exact commit. Automated IDE execution is disabled.')
                return
            if t['status'] == 'assigned':
                t = await self.s.tasks.update(t['id'], TaskStatus(request_id=str(uuid4()), agent=t['assigned_agent'], expected_revision=t['revision'], status='in_progress'))
            p['attempts'].setdefault(t['id'], {'executions': []})['executions'].append(
                {'started_at': time.time(), 'retry_count': t['retry_count'], 'memory_version': p['memory']['version']})
            p['attempts'][t['id']]['started_at'] = time.time()
            p.update(state='execution', manual_handoff=False)
            await self.save(p, [self.job(p, t['assigned_agent'], 'execution', {'task': t, 'memory': p['memory'], 'mode': p['execution_mode'], 'instruction': 'Production manual handoff: no automatic edits/commands. Never run integration, deployment, deletion or out-of-scope actions.'}, ExecutionResult.model_json_schema(), t['id'])])
        elif t['status'] == 'review':
            if t['review']['verdict'] != 'APPROVED':
                if not any(j['phase'] == 'review' and j['task_id'] == t['id'] and j['commit'] == t['review']['commit'] and j['status'] in ('pending', 'running') for j in p['jobs']):
                    p['state'] = 'review'
                    await self.save(p, [self.job(p, t['review']['reviewer'], 'review', {'task': t, 'memory': p['memory']}, ReviewResult.model_json_schema(), t['id'], t['review']['commit'])])
                return
            key = t['id'] + '-' + t['review']['commit'][:16] + '-' + str(t['retry_count'])
            if p.get('integration', {}).get('key') != key:
                # A durable reservation also covers approval creation. A crash never creates a second action silently.
                p['busy'] = 'approval-request'
                await self.save(p)
                action = (await self.s.runner.integration_action(t['id'], key))['action']
                a = await self.s.guard.request(action)
                p.update(state='approval', busy=None, integration={'key': key, 'approval_id': a['id'], 'task_id': t['id']})
                await self.save(p)
                return
            a = next(a for a in await self.s.guard.list() if a['id'] == p['integration']['approval_id'])
            if a['state'] == 'pending':
                return
            if a['state'] != 'approved':
                await self.pause(p, 'Exact-action approval denied or consumed; inspect before proceeding')
                return
            p['busy'] = 'integration'
            await self.save(p)
            main_before = await self.s.runner.workspace.head('main')
            result = await self.s.runner.integrate(t['id'], key, a['id'])
            if not result.get('promoted'):
                main_after = await self.s.runner.workspace.head('main')
                log = result.get('test_log', '')
                if main_after != main_before or any(word in log.lower() for word in ('cleanup', 'interrupted', 'timeouterror')):
                    p['uncertain'] = True
                    await self.pause(p, 'Uncertain integration side effect; inspect main, candidate and process state. No automatic replay.')
                    return
            p.update(busy=None, state='tasks')
            await self.save(p)
            if result['task']['status'] == 'blocked':
                await self.pause(p, 'MAX_TASK_RETRIES or execution budget exhausted')

    async def observe_ack(self, message_id, agent):
        from backend.project_models import Usage
        projects = await self.list()
        if not projects or projects[0]['state'] in TERMINAL | {'paused'}:
            return
        p = projects[0]
        job = next((j for j in p['jobs'] if j['message_id'] == str(message_id) and j['agent'] == agent and j['task_id'] and j['status'] in ('pending', 'running')), None)
        if job:
            await self.usage(p['id'], Usage(agent=agent, correlation_id=job['id'], event_id='ack-' + str(message_id), kind='tool'))

    async def observe_tool(self, job, event):
        from backend.project_models import Usage
        from backend.codex_connector import ConnectorError
        event_id = 'protocol-' + event['thread_id'] + '-' + event['turn_id'] + '-' + event['item_id']
        if job['task_id']:
            result = await self.usage(job['project_id'], Usage(agent=job['agent'], correlation_id=job['id'], event_id=event_id, kind='tool'))
            if result.get('tool_calls', 0) >= MAX_TOOL_CALLS_PER_TASK:
                raise ConnectorError('tool_budget_exhausted', True)
        else:
            async with self.db.lock:
                await self.db.connection.execute('INSERT OR IGNORE INTO project_usage VALUES (?,?)', (job['id'] + ':' + event_id, digest(event)))
                await self.db.connection.commit()

    async def dispatch(self):
        async with self.lock:
            projects = await self.list()
            if not projects or projects[0]['state'] in TERMINAL | {'paused'}:
                return
            p = projects[0]
            j = next((j for j in p['jobs'] if j['status'] == 'pending' and j['agent'] in self.adapters
                      and (j['phase'] != 'execution' or self.fixture_execution)), None)
            if not j:
                return
            j['status'] = 'running'
            await self.save(p)
        try:
            # This is explicit acceptance by an owned adapter, not an inbox read.
            await self.db.acknowledge(j['message_id'], j['agent'])
            await self.observe_ack(j['message_id'], j['agent'])
            if (await self.get(p['id']))['state'] in TERMINAL | {'paused'}:
                return
            payload = await self.adapters[j['agent']].request(j, p['repository'])
            await self.report(p['id'], AgentReport(request_id=j['id'], correlation_id=j['id'], agent=j['agent'], phase=j['phase'], task_id=j['task_id'], decision_id=j['decision_id'], commit=j['commit'], payload=payload))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            async with self.lock:
                latest = await self.get(p['id'])
                if latest['state'] not in TERMINAL | {'paused'}:
                    # Read-only inference isn't repeated either: submitted turns can be uncertain.
                    latest['uncertain'] = True
                    await self.pause(latest, 'Agent request failed: ' + type(exc).__name__ + '; inspect correlation/session before replacement work')

    async def run(self):
        # Separate dispatcher lets timeout checks continue during an agent request.
        dispatch = None
        try:
            while True:
                await self.tick()
                if dispatch is None or dispatch.done():
                    if dispatch is not None:
                        dispatch.result()
                    dispatch = asyncio.create_task(self.dispatch())
                await asyncio.sleep(.5)
        finally:
            if dispatch is not None:
                dispatch.cancel()
                await asyncio.gather(dispatch, return_exceptions=True)

    def activation(self, p):
        return (f"Use devmesh-bridge for project {p['id']}; your role is antigravity. "
                "Loop on await_next_task with agent_name antigravity and timeout_seconds 45; no_task means poll again. "
                "Get each DEVMESH assignment with get_assignment_context using its project/correlation and agent. "
                "Explicitly acknowledge accepted messages; reading alone is not acknowledgement. "
                "Use the exact correlation, phase, task, decision and reviewed commit in report_project_result. "
                "Analysis and review are read-only. Do not request peer analysis until both analyses are collected. "
                "Use the assigned memory version, worktree, branch, scope and acceptance criteria. "
                "Production execution is a manual handoff: do not automatically edit files or run commands. "
                "Return structured payloads matching response_schema, never generic agreement text. "
                "Never approve operator actions or use legacy submit_review for integration. "
                "Report errors, stalls and observable tool event IDs through report_project_usage. "
                "Stop on PROJECT_COMPLETE, cancellation or explicit safety/human pause. Never claim background work without a running operation. "
                "If the IDE stops or exhausts its own step budget, the operator must re-paste this activation prompt.")
