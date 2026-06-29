"""
WebSocket connection manager — live job-status broadcast.

The App Server keeps a set of open WebSocket connections.  Whenever the
Event Processor changes a job's status it calls broadcaster.publish(), which
fans out the payload to all connected clients (the WCC dashboard subscribes).

Thread safety: publish() is called from the EPS background asyncio task, so
it runs in the same event loop as the FastAPI server.  No locking required.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket
from loguru import logger


class EventBroadcaster:
    """
    Maintains a set of active WebSocket connections and broadcasts JSON
    messages to all of them.

    Usage (inside a FastAPI WebSocket endpoint)::

        @app.websocket("/api/v1/ws/events")
        async def ws_events(ws: WebSocket, broadcaster: EventBroadcaster = Depends(get_broadcaster)):
            await broadcaster.connect(ws)
            try:
                while True:
                    await ws.receive_text()   # keep alive / echo
            except WebSocketDisconnect:
                broadcaster.disconnect(ws)
    """

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)
        logger.debug("WS client connected ({} total)", len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)
        logger.debug("WS client disconnected ({} remaining)", len(self._connections))

    async def publish(self, payload: dict[str, Any]) -> None:
        """Fan out *payload* as JSON to all connected clients."""
        if not self._connections:
            return
        dead: set[WebSocket] = set()
        for ws in list(self._connections):
            try:
                await ws.send_json(payload)
            except Exception as exc:
                logger.debug("WS send failed ({}), removing client", exc)
                dead.add(ws)
        self._connections -= dead

    def publish_sync(self, payload: dict[str, Any]) -> None:
        """
        Thread-safe wrapper for calling publish() from a synchronous context
        (e.g. the EPS tick loop running in a threadpool executor).

        Schedules the async publish on the running event loop.
        """
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(
                loop.create_task,
                self.publish(payload),
            )
        except RuntimeError:
            # No running event loop (e.g. in tests without an API server)
            pass

    @property
    def connection_count(self) -> int:
        return len(self._connections)
