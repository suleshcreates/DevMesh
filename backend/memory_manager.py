"""Disposable Markdown projections. SQLite is always authoritative."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
from backend.security_guard import redact

DOCUMENTS = ('PROJECT_SPEC.md', 'ARCHITECTURE.md', 'API_CONTRACT.md',
             'DECISIONS.md', 'TASKS.md', 'KNOWN_ISSUES.md')
SOURCE_DOCUMENTS = ('PROJECT_SPEC.md', 'ARCHITECTURE.md', 'API_CONTRACT.md', 'KNOWN_ISSUES.md')

def validate_name(name):
    if name not in DOCUMENTS:
        raise ValueError('Unsupported document name')
    return name

class MemoryManager:
    def __init__(self, db, directory):
        self.db, self.directory = db, Path(directory)
        self.lock = asyncio.Lock()

    async def set_source(self, name, content):
        if name not in SOURCE_DOCUMENTS:
            raise ValueError('Document does not accept source content')
        async with self.db.lock:
            await self.db.connection.execute('INSERT INTO memory_sources VALUES (?,?) ON CONFLICT(name) DO UPDATE SET content=excluded.content', (name, redact(content)))
            await self.db.connection.commit()

    async def render(self, name):
        validate_name(name)
        async with self.db.lock:
            rows = await (await self.db.connection.execute('SELECT document FROM decisions ORDER BY number')).fetchall()
            source = await (await self.db.connection.execute('SELECT content FROM memory_sources WHERE name=?', (name,))).fetchone()
        text = f'# {name[:-3].replace("_", " ")}\n\nGenerated from SQLite; edits are not imported.\n\n'
        if name == 'TASKS.md':
            from backend.task_planner import unpack
            async with self.db.lock:
                exists = await (await self.db.connection.execute("SELECT name FROM sqlite_master WHERE name='tasks'")).fetchone()
                tasks = await (await self.db.connection.execute('SELECT * FROM tasks ORDER BY id')).fetchall() if exists else []
            if not tasks:
                return text + 'No task planning records exist.\n'
            return redact(text + '\n'.join(json.dumps(unpack(r), indent=2) for r in tasks) + '\n')
        text += (source[0] if source else 'No source content recorded.') + '\n'
        if name in ('DECISIONS.md', 'ARCHITECTURE.md'):
            decisions = [json.loads(r[0]) for r in rows]
            if name == 'ARCHITECTURE.md':
                decisions = [d for d in decisions if d['state'] == 'RECORD']
            for d in decisions:
                text += f'\n## {d["id"]}: {d["title"]}\n\nStatus: {d["status"]}\n\n'
                text += json.dumps(d, indent=2, ensure_ascii=False) + '\n'
        return redact(text)

    def write_atomic(self, name, text):
        validate_name(name)
        self.directory.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.directory, delete=False) as handle:
                temp = handle.name
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.directory / name)
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)

    async def context(self, name):
        validate_name(name)
        async with self.lock:
            content = await self.render(name)
            generated = True
            try:
                self.write_atomic(name, content)
            except OSError:
                generated = False
            return {'doc_name': name, 'content': content, 'generated': generated}

    async def regenerate(self):
        return [await self.context(name) for name in DOCUMENTS]
