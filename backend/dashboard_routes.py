"""Read-only hydration for data not exposed by the M1-M3 read routes."""
import json
from backend.security_guard import redact


def register(app):
    @app.get('/api/dashboard/snapshot')
    async def snapshot():
        db = app.state.db
        async with db.lock:
            messages = await (await db.connection.execute(
                'SELECT * FROM messages ORDER BY created_at, id')).fetchall()
            agents = await (await db.connection.execute(
                'SELECT name, last_seen FROM agents ORDER BY name')).fetchall()
            attempts = await (await db.connection.execute(
                'SELECT id, task_id, state, document FROM integration_attempts ORDER BY id')).fetchall()
        return redact({
            'messages': [db.message(row) for row in messages],
            'agents': [dict(row) for row in agents],
            'integration_attempts': [{**dict(row), 'document': json.loads(row['document'])}
                                     for row in attempts],
        })
