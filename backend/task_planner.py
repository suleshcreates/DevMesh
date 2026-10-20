"""Durable task transitions and transactional outbox. No scheduler."""
import hashlib
import json
from uuid import uuid4
from backend.database import now
from backend.decision_engine import DecisionError
from backend.security_guard import redact

MAX_TASK_RETRIES = 3
MAX_TOOL_CALLS_PER_TASK = 40
TASK_TIMEOUT_SECONDS = 600
FIELDS = ['id', 'title', 'description', 'assigned_agent', 'status', 'dependencies',
          'touched_files_scope', 'acceptance_criteria', 'branch', 'worktree_path',
          'review_feedback', 'retry_count', 'revision', 'history', 'review', 'tool_calls']
JSON_FIELDS = {'dependencies', 'touched_files_scope', 'acceptance_criteria', 'review_feedback', 'history', 'review'}
TRANSITIONS = {'planned': {'assigned', 'blocked'}, 'assigned': {'in_progress', 'blocked'},
               'in_progress': {'review', 'blocked'}, 'review': {'in_progress', 'testing', 'blocked'},
               'testing': {'in_progress', 'complete', 'blocked'}, 'blocked': set(), 'complete': set()}

def fail(detail, code=409):
    raise DecisionError(code, detail)

def move(task, status):
    if status not in TRANSITIONS[task['status']]:
        fail('Invalid task transition')
    task['status'] = status
    task['history'].append({'status': status, 'at': now()})

def unpack(row):
    d = dict(row)
    for key in JSON_FIELDS:
        d[key] = json.loads(d[key])
    return d

class TaskPlanner:
    def __init__(self, db):
        self.db = db

    async def initialize(self):
        async with self.db.lock:
            await self.db.connection.executescript('''
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
                    assigned_agent TEXT NOT NULL, status TEXT NOT NULL,
                    dependencies TEXT NOT NULL, touched_files_scope TEXT NOT NULL,
                    acceptance_criteria TEXT NOT NULL, branch TEXT, worktree_path TEXT,
                    review_feedback TEXT NOT NULL, retry_count INTEGER NOT NULL,
                    revision INTEGER NOT NULL, history TEXT NOT NULL, review TEXT NOT NULL,
                    tool_calls INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS task_requests (
                    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS serialization_tasks (
                    overlap_key TEXT PRIMARY KEY, task_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS integration_attempts (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, state TEXT NOT NULL,
                    document TEXT NOT NULL);
            ''')
            await self.db.connection.commit()

    async def _get(self, c, task_id):
        row = await (await c.execute('SELECT * FROM tasks WHERE id=?', (task_id,))).fetchone()
        if not row:
            fail('Task not found', 404)
        return unpack(row)

    async def get(self, task_id):
        async with self.db.lock:
            return await self._get(self.db.connection, task_id)

    async def list(self):
        async with self.db.lock:
            rows = await (await self.db.connection.execute('SELECT * FROM tasks ORDER BY id')).fetchall()
        return [unpack(r) for r in rows]

    async def save(self, c, task):
        task = redact(task)
        values = [json.dumps(task[k]) if k in JSON_FIELDS else task[k] for k in FIELDS]
        await c.execute(f"INSERT OR REPLACE INTO tasks ({','.join(FIELDS)}) VALUES ({','.join('?' for _ in FIELDS)})", values)

    async def message(self, c, recipient, subject, payload, sender='human', kind='message', requires_response=True):
        ident, conversation = str(uuid4()), str(uuid4())
        await c.execute('INSERT INTO conversations VALUES (?,?)', (conversation, now()))
        await c.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (ident, conversation, sender, recipient, kind, subject,
                         json.dumps(redact(payload)), int(requires_response), 0, now()))
        return ident

    async def dependencies_ready(self, c, task):
        for dep in task['dependencies']:
            if (await self._get(c, dep))['status'] != 'complete':
                fail('Task dependencies are incomplete')

    async def operation(self, key, payload, callback):
        ident = hashlib.sha256(key.encode()).hexdigest()
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        async with self.db.lock:
            c = self.db.connection
            try:
                await c.execute('BEGIN IMMEDIATE')
                prior = await (await c.execute('SELECT fingerprint,response FROM task_requests WHERE id=?', (ident,))).fetchone()
                if prior:
                    if fingerprint != prior[0]:
                        fail('Request ID conflict')
                    await c.rollback()
                    return json.loads(prior[1])
                limited = None
                if payload[0] in ('status', 'challenge', 'workspace', 'review-ready', 'feedback'):
                    t = await self._get(c, payload[1])
                    if t['status'] not in ('complete', 'blocked'):
                        if t['tool_calls'] >= MAX_TOOL_CALLS_PER_TASK:
                            move(t, 'blocked')
                            t['review'] = None
                            t['revision'] += 1
                            await self.message(c, 'human', 'HUMAN_APPROVAL_REQUIRED',
                                               {'task_id': t['id'], 'reason': 'MAX_TOOL_CALLS_PER_TASK'})
                            limited = t
                        else:
                            t['tool_calls'] += 1
                        await self.save(c, t)
                result = redact(limited if limited else await callback(c))
                await c.execute('INSERT INTO task_requests VALUES (?,?,?)', (ident, fingerprint, json.dumps(result)))
                await c.commit()
                return result
            except BaseException:
                await c.rollback()
                raise

    async def create_in(self, c, payload):
        from backend.git_workspace import validate_scope
        scopes = [validate_scope(s) for s in payload['touched_files_scope']]
        for dep in payload['dependencies']:
            await self._get(c, dep)
        rows = await (await c.execute('SELECT count(*) FROM tasks')).fetchone()
        task = dict(id=f'TASK-{rows[0]+1:03d}', title=payload['title'], description=payload['description'],
                    assigned_agent=payload.get('assigned_agent') or ('antigravity' if payload.get('category') in ('ui_ux', 'frontend', 'browser_qa') else 'codex'),
                    status='planned', dependencies=list(dict.fromkeys(payload['dependencies'])),
                    touched_files_scope=scopes, acceptance_criteria=payload['acceptance_criteria'],
                    branch=None, worktree_path=None, review_feedback=[], retry_count=0,
                    revision=1, history=[{'status': 'planned', 'at': now()}], review=None, tool_calls=0)
        await self.save(c, task)
        return task

    async def create(self, request):
        payload = request.model_dump()
        return await self.operation(request.request_id, ['create', payload], lambda c: self.create_in(c, payload))

    async def update(self, task_id, request):
        async def change(c):
            t = await self._get(c, task_id)
            if request.agent != t['assigned_agent']:
                fail('Only assigned agent may update task')
            if request.expected_revision != t['revision']:
                fail('Stale task revision')
            if request.status == t['status']:
                return t
            if request.status in ('review', 'testing', 'complete'):
                fail('Use review/integration workflow for protected transitions')
            if request.status in ('assigned', 'in_progress'):
                await self.dependencies_ready(c, t)
            move(t, request.status)
            if request.status in ('in_progress', 'blocked'):
                t['review'] = None
            t['revision'] += 1
            await self.save(c, t)
            return t
        return await self.operation(request.request_id, ['status', task_id, request.model_dump()], change)

    async def challenge(self, task_id, request):
        async def change(c):
            t = await self._get(c, task_id)
            recipient = 'antigravity' if request.agent == 'codex' else 'codex'
            ident = await self.message(c, recipient, 'API_CONTRACT_CHALLENGE',
                                      {'task_id': task_id, 'content': request.content}, request.agent)
            return {'message_id': ident, 'task_id': t['id']}
        return await self.operation(request.request_id, ['challenge', task_id, request.model_dump()], change)

    async def failure(self, c, task, feedback):
        task['retry_count'] += 1
        task['review_feedback'].append(redact(feedback))
        task['review'] = None
        move(task, 'blocked' if task['retry_count'] >= MAX_TASK_RETRIES else 'in_progress')
        if task['status'] == 'blocked':
            await self.message(c, 'human', 'HUMAN_APPROVAL_REQUIRED', {'task_id': task['id'], 'reason': 'MAX_TASK_RETRIES'})
        task['revision'] += 1
        await self.save(c, task)

    async def serialization(self, left, right, paths):
        key = json.dumps([sorted([left['id'], right['id']]), sorted(p.casefold() for p in paths)])
        async def create(c):
            prior = await (await c.execute('SELECT task_id FROM serialization_tasks WHERE overlap_key=?', (key,))).fetchone()
            if prior:
                return await self._get(c, prior[0])
            task = await self.create_in(c, dict(title='Serialize overlapping task changes',
                 description=f"Resolve overlap between {left['id']} and {right['id']}: " + ', '.join(paths),
                 dependencies=[], touched_files_scope=paths, acceptance_criteria=['Human defines order and scopes; peer reviews both changes.']))
            move(task, 'blocked')
            await self.save(c, task)
            await c.execute('INSERT INTO serialization_tasks VALUES (?,?)', (key, task['id']))
            await self.message(c, 'human', 'HUMAN_APPROVAL_REQUIRED', {'task_id': task['id'], 'reason': 'Overlapping branches require serialization'})
            return task
        return await self.operation('overlap-' + hashlib.sha256(key.encode()).hexdigest(), ['overlap', key], create)
