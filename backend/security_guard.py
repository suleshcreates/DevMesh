"""Payload-only redaction. Never inspect credential files or the environment."""
import re

MASK = "[REDACTED]"
PATTERNS = [
    re.compile(r"-----BEGIN (?:[A-Z0-9 ]* )?PRIVATE KEY-----[\s\S]*?(?:-----END (?:[A-Z0-9 ]* )?PRIVATE KEY-----|$)"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]+|AKIA[A-Z0-9]{16})\b"),
    # Treat every shell/.env assignment as sensitive, including multiline quotes.
    re.compile(r"""(?m)(\b(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*)(?:"[^"]*"|'[^']*'|[^\r\n]*)"""),
    re.compile(r"""(?i)(["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|authorization)["']?\s*:\s*)(?:"[^"]*"|'[^']*'|[^,\r\n}]+)"""),
    re.compile(r"(?i)\bBearer\s+\S+"),
]

def redact(value):
    if isinstance(value, str):
        for i, pattern in enumerate(PATTERNS):
            value = pattern.sub((lambda m: m[1] + MASK) if i in (3, 4) else MASK, value)
        return value
    if isinstance(value, dict):
        return {redact(k): MASK if re.search(r"(?i)(api.?key|token|secret|password|authorization)", str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value

# This gate is deliberately conservative. It is not an OS sandbox.
import hashlib
import json
from uuid import uuid4
from pathlib import Path

def action_fingerprint(action):
    # Digest the exact raw action, not its redacted equivalent.
    return hashlib.sha256(json.dumps(action, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def destructive_reasons(action):
    text = ' '.join(action['arguments'])
    reasons = []
    if re.search(r'(?i)\b(drop|truncate|deploy|destroy|format|remove-item|rmdir|rd|del|erase)\b|\brm\s+.*-[a-z]*[rf]|git\s+(reset|clean|push.*--force)', text):
        reasons.append('Potentially destructive command')
    target = action.get('target')
    if action['kind'] == 'deploy':
        reasons.append('Deployment')
    if re.search(r'(?i)\.git[/\\]config', text):
        reasons.append('Git configuration edit')
    if target:
        path = Path(target).resolve()
        if not path.is_relative_to(Path(action['worktree']).resolve()):
            reasons.append('Write outside assigned worktree')
        if '/.git/config' in str(path).replace('\\', '/').lower():
            reasons.append('Git configuration edit')
    return reasons

class ApprovalGuard:
    """Single-use exact-action permits; immutable redacted audit trail."""
    def __init__(self, db):
        self.db = db

    async def initialize(self):
        async with self.db.lock:
            await self.db.connection.executescript('''
                CREATE TABLE IF NOT EXISTS approval_requests (
                    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, document TEXT NOT NULL,
                    state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,
                    event TEXT NOT NULL, document TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_logs
                    BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_logs
                    BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
            ''')
            await self.db.connection.commit()

    async def audit(self, c, ident, event, data):
        from backend.database import now
        await c.execute('INSERT INTO audit_logs(request_id,event,document) VALUES (?,?,?)',
                        (redact(ident), redact(event), json.dumps(redact({'at': now(), 'data': data}))))

    async def request(self, action):
        ident, fingerprint = str(uuid4()), action_fingerprint(action)
        async with self.db.lock:
            c = self.db.connection
            await c.execute('INSERT INTO approval_requests VALUES (?,?,?,?)',
                            (ident, fingerprint, json.dumps(redact(action)), 'pending'))
            await self.audit(c, ident, 'pending', {'action': action, 'reasons': destructive_reasons(action)})
            await c.commit()
        return {'id': ident, 'fingerprint': fingerprint, 'state': 'pending', 'action': redact(action)}

    async def list(self):
        async with self.db.lock:
            rows = await (await self.db.connection.execute('SELECT * FROM approval_requests')).fetchall()
        return [{**dict(r), 'document': json.loads(r['document'])} for r in rows]

    async def decide(self, ident, request):
        from backend.task_planner import fail
        async with self.db.lock:
            c = self.db.connection
            try:
                await c.execute('BEGIN IMMEDIATE')
                row = await (await c.execute('SELECT * FROM approval_requests WHERE id=?', (ident,))).fetchone()
                if not row:
                    fail('Approval request not found', 404)
                if row['fingerprint'] != request.fingerprint:
                    fail('Approval fingerprint mismatch')
                if row['state'] == request.decision:
                    await c.rollback()
                    return {'id': ident, 'state': row['state']}
                if row['state'] != 'pending':
                    fail('Approval already decided or consumed')
                await c.execute('UPDATE approval_requests SET state=? WHERE id=?', (request.decision, ident))
                await self.audit(c, ident, request.decision, {'reason': request.reason, 'identity': 'unauthenticated local operator'})
                await c.commit()
                return {'id': ident, 'state': request.decision}
            except BaseException:
                await c.rollback()
                raise

    async def execute(self, ident, action, callback):
        from backend.task_planner import fail
        fingerprint = action_fingerprint(action)
        allowed = False
        async with self.db.lock:
            c = self.db.connection
            try:
                await c.execute('BEGIN IMMEDIATE')
                row = await (await c.execute('SELECT * FROM approval_requests WHERE id=?', (ident,))).fetchone()
                allowed = bool(row and row['fingerprint'] == fingerprint and row['state'] == 'approved')
                await self.audit(c, ident or 'missing', 'execution_attempt', {'fingerprint': fingerprint, 'allowed': allowed})
                if allowed:
                    await c.execute("UPDATE approval_requests SET state='consumed' WHERE id=?", (ident,))
                await c.commit()
            except BaseException:
                await c.rollback()
                raise
        if not allowed:
            fail('Exact action requires an unused explicit operator approval')
        try:
            result = await callback()
        except BaseException as exc:
            async with self.db.lock:
                await self.audit(c, ident, 'execution_failed', {'error_type': type(exc).__name__})
                await c.commit()
            raise
        async with self.db.lock:
            await self.audit(c, ident, 'execution_outcome', result)
            await c.commit()
        return result
