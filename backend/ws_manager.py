import asyncio
from backend.security_guard import redact

class WebSocketManager:
    def __init__(self):
        self.clients = {}

    async def connect(self, socket, agent=None):
        await socket.accept()
        self.clients[socket] = agent

    def disconnect(self, socket):
        self.clients.pop(socket, None)

    async def broadcast(self, event, agents):
        safe = redact(event)
        async def send(socket):
            try:
                await asyncio.wait_for(socket.send_json(safe), 1)
            except Exception:
                self.disconnect(socket)
                try:
                    await asyncio.wait_for(socket.close(code=1013), 1)
                except Exception:
                    pass
        await asyncio.gather(*(send(s) for s, a in list(self.clients.items())
                               if a is None or a in agents))
