"""Transactional, caller-driven deliberation. No agent execution or debate loop."""
import json
import hashlib
from uuid import uuid4
from pydantic import ValidationError
from backend.database import now
from backend.security_guard import redact
from backend.decision_models import Critique

MAX_CRITIQUE_ROUNDS = 3
TRANSITIONS = {'NEW': {'ANALYSIS'}, 'ANALYSIS': {'EXCHANGE'},
               'EXCHANGE': {'CRITIQUE'}, 'CRITIQUE': {'RESOLVE'},
               'RESOLVE': {'EXCHANGE', 'RECORD', 'ESCALATE'},
               'ESCALATE': {'RESOLVE'}, 'RECORD': set()}

class DecisionError(Exception):
    def __init__(self, code, detail):
        self.code, self.detail = code, detail

def transition(d, state):
    if state not in TRANSITIONS[d['state']]:
        raise DecisionError(409, 'Invalid state transition')
    d['state'] = state
    d['history'].append({'state': state, 'at': now()})

class DecisionEngine:
    def __init__(self, db):
        self.db = db

    async def initialize(self):
        async with self.db.lock:
            await self.db.connection.executescript('''
                CREATE TABLE IF NOT EXISTS decisions (
                    number INTEGER PRIMARY KEY AUTOINCREMENT, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS decision_requests (
                    request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS memory_sources (
                    name TEXT PRIMARY KEY, content TEXT NOT NULL);
            ''')
            await self.db.connection.commit()

    async def list(self, open_only=False):
        async with self.db.lock:
            rows = await (await self.db.connection.execute('SELECT document FROM decisions ORDER BY number')).fetchall()
        values = [json.loads(r[0]) for r in rows]
        return [d for d in values if not open_only or d['state'] != 'RECORD']

    async def get(self, decision_id):
        for d in await self.list():
            if d['id'] == decision_id:
                return d
        raise DecisionError(404, 'Decision not found')

    async def mutate(self, action, request, decision_id=None):
        # Store only sanitized fingerprints and responses, never raw invalid JSON.
        request_key = hashlib.sha256(request.request_id.encode()).hexdigest()
        payload = redact(request.model_dump())
        fingerprint = json.dumps([action, decision_id, payload], sort_keys=True)
        async with self.db.lock:
            c = self.db.connection
            try:
                await c.execute('BEGIN IMMEDIATE')
                prior = await (await c.execute('SELECT fingerprint,response FROM decision_requests WHERE request_id=?', (request_key,))).fetchone()
                if prior:
                    if prior[0] != fingerprint:
                        raise DecisionError(409, 'Request ID already used')
                    await c.rollback()
                    return json.loads(prior[1]), []
                if action == 'propose':
                    cursor = await c.execute("INSERT INTO decisions(document) VALUES ('{}')")
                    number = cursor.lastrowid
                    d = dict(id=f'DEC-{number:03d}', state='NEW', status='open', revision=1,
                             title=payload['title'], round_count=0, invalid_count=0,
                             proposals=[dict(agent=payload['agent'], content=payload['content'], rationale=payload['rationale'])],
                             critiques=[], history=[{'state': 'NEW', 'at': now()}], resolution=None,
                             rationale=None, human_resolution=None)
                    for state in ('ANALYSIS', 'EXCHANGE'):
                        transition(d, state)
                    event = 'DECISION_PROPOSED'
                else:
                    rows = await (await c.execute('SELECT number,document FROM decisions')).fetchall()
                    found = [(r[0], json.loads(r[1])) for r in rows if json.loads(r[1])['id'] == decision_id]
                    if not found:
                        raise DecisionError(404, 'Decision not found')
                    number, d = found[0]
                    if request.expected_revision != d['revision']:
                        raise DecisionError(409, 'Stale decision revision')
                    if action == 'human':
                        if d['state'] != 'ESCALATE':
                            raise DecisionError(409, 'Human resolution requires escalation')
                        transition(d, 'RESOLVE')
                        d['human_resolution'] = dict(directive=payload['directive'], rationale=payload['rationale'])
                        d.update(status='human_resolved', resolution=payload['directive'], rationale=payload['rationale'])
                        transition(d, 'RECORD')
                        event = 'DECISION_HUMAN_RESOLVED'
                    elif action == 'critique':
                        if d['state'] != 'EXCHANGE':
                            raise DecisionError(409, 'Decision is not awaiting critique')
                        if request.agent == d['proposals'][-1]['agent']:
                            raise DecisionError(409, 'A proposal requires critique from the other agent')
                        transition(d, 'CRITIQUE')
                        try:
                            critique = Critique.model_validate_json(request.critique_json)
                        except ValidationError:
                            d['invalid_count'] += 1
                            d['critiques'].append(dict(agent=request.agent, invalid=True, round=d['round_count'] + 1))
                            transition(d, 'RESOLVE')
                            if d['invalid_count'] == 1:
                                transition(d, 'EXCHANGE')
                                event = 'CRITIQUE_REASK'
                            else:
                                transition(d, 'ESCALATE')
                                d['status'] = 'escalated_to_human'
                                event = 'HUMAN_APPROVAL_REQUIRED'
                        else:
                            d['round_count'] += 1
                            clean = redact(critique.model_dump())
                            d['critiques'].append(dict(agent=request.agent, round=d['round_count'], payload=clean))
                            transition(d, 'RESOLVE')
                            blocking = any(r.blocking for r in critique.identified_risks)
                            if critique.verdict == 'AGREE' and not blocking:
                                d.update(status='agreed', resolution=d['proposals'][-1]['content'], rationale=clean['tradeoff_analysis'])
                                transition(d, 'RECORD')
                                event = 'DECISION_AGREED'
                            else:
                                if critique.counter_proposal and critique.counter_proposal.strip():
                                    d['proposals'].append(dict(agent=request.agent, content=clean['counter_proposal'], rationale=clean['tradeoff_analysis']))
                                if d['round_count'] >= MAX_CRITIQUE_ROUNDS:
                                    transition(d, 'ESCALATE')
                                    d['status'] = 'escalated_to_human'
                                    event = 'HUMAN_APPROVAL_REQUIRED'
                                else:
                                    transition(d, 'EXCHANGE')
                                    event = 'CRITIQUE_REQUIRED'
                    else:
                        raise DecisionError(409, 'Invalid action')
                    d['revision'] += 1
                d = redact(d)
                await c.execute('UPDATE decisions SET document=? WHERE number=?', (json.dumps(d), number))
                # Same transaction as the decision: recovery uses the existing durable inbox.
                recipients = {'codex', 'antigravity'}
                if event == 'HUMAN_APPROVAL_REQUIRED':
                    recipients.add('human')
                messages = []
                for recipient in sorted(recipients):
                    conversation = str(uuid4())
                    await c.execute('INSERT INTO conversations VALUES (?,?)', (conversation, now()))
                    message = dict(id=str(uuid4()), conversation_id=conversation, sender='human', recipient=recipient,
                                   type='message', subject=event, content=json.dumps({'event': event, 'decision': d}),
                                   requires_response=d['state'] != 'RECORD', delivered=False, created_at=now())
                    message = redact(message)
                    keys = list(message)
                    await c.execute(f"INSERT INTO messages ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})", list(message.values()))
                    messages.append(message)
                await c.execute('INSERT INTO decision_requests VALUES (?,?,?)', (request_key, fingerprint, json.dumps(d)))
                await c.commit()
                return d, messages
            except BaseException:
                await c.rollback()
                raise
