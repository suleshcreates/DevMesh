"""SQLite owns delivery state; all writes are sanitized at this boundary."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import aiosqlite
from backend.security_guard import redact

def now():
    return datetime.now(timezone.utc).isoformat()

class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = asyncio.Lock()

    async def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = await aiosqlite.connect(self.path)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS agents (
                name TEXT PRIMARY KEY, last_seen TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id),
                sender TEXT NOT NULL, recipient TEXT NOT NULL,
                type TEXT NOT NULL, subject TEXT NOT NULL, content TEXT NOT NULL,
                requires_response INTEGER NOT NULL, delivered INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS inbox ON messages(recipient, delivered, created_at);
        """)
        await self.connection.commit()

    async def close(self):
        await self.connection.close()

    @staticmethod
    def message(row):
        result = dict(row)
        for key in ("delivered", "requires_response"):
            result[key] = bool(result[key])
        return result

    async def create_message(self, request):
        data = redact(request.model_dump(mode="json"))
        data.update(id=str(uuid4()), conversation_id=data["conversation_id"] or str(uuid4()),
                    created_at=now(), delivered=False)
        async with self.lock:
            try:
                await self.connection.execute("INSERT OR IGNORE INTO conversations VALUES (?, ?)",
                                              (data["conversation_id"], data["created_at"]))
                keys = list(data)
                await self.connection.execute(
                    f"INSERT INTO messages ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                    [data[k] for k in keys])
                await self.connection.commit()
            except BaseException:
                await self.connection.rollback()
                raise
        return data

    async def inbox(self, agent):
        async with self.lock:
            async with self.connection.execute(
                "SELECT * FROM messages WHERE recipient=? AND delivered=0 ORDER BY created_at, id",
                (agent,)) as cursor:
                return [self.message(row) for row in await cursor.fetchall()]

    async def acknowledge(self, message_id, agent):
        async with self.lock:
            async with self.connection.execute("SELECT * FROM messages WHERE id=? AND recipient=?",
                                               (str(message_id), agent)) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None, False
            changed = not row["delivered"]
            if changed:
                await self.connection.execute("UPDATE messages SET delivered=1 WHERE id=?", (str(message_id),))
                await self.connection.commit()
            data = self.message(row)
            data["delivered"] = True
            return data, changed

    async def heartbeat(self, agent):
        data = redact({"name": agent, "last_seen": now()})
        async with self.lock:
            await self.connection.execute(
                "INSERT INTO agents VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen",
                (data["name"], data["last_seen"]))
            await self.connection.commit()
        return data
