import asyncio
import uuid
from collections import defaultdict

from fastapi import WebSocket

MAX_CONNECTIONS_PER_SESSION = 3


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[uuid.UUID, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, session_id: uuid.UUID, websocket: WebSocket) -> bool:
        await websocket.accept()
        async with self._lock:
            connections = self._connections[session_id]
            if len(connections) >= MAX_CONNECTIONS_PER_SESSION:
                await websocket.close(code=1013, reason="too many session connections")
                return False
            connections.add(websocket)
        return True

    async def disconnect(self, session_id: uuid.UUID, websocket: WebSocket) -> None:
        async with self._lock:
            connections = self._connections.get(session_id)
            if not connections:
                return
            connections.discard(websocket)
            if not connections:
                self._connections.pop(session_id, None)


connection_manager = ConnectionManager()
