"""WebSocket connection manager for real-time graph updates."""

import logging
from typing import Any
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections and broadcasts."""

    def __init__(self):
        # Map: session_id -> WebSocket
        self.active_connections: dict[str, WebSocket] = {}
        # Map: session_id -> the project root this connection subscribed to,
        # or None for "user graph only". A connection with no entry never
        # subscribed (an older editor page, a non-editor client) and falls
        # back to its session's registered project, as before subscriptions.
        self.subscriptions: dict[str, str | None] = {}

    async def connect(self, websocket: WebSocket, session_id: str):
        """Accept and register a WebSocket connection."""
        await websocket.accept()
        self.active_connections[session_id] = websocket
        # A new connection starts unsubscribed, even under a reused session id.
        self.subscriptions.pop(session_id, None)
        logger.info(f"WebSocket connected: {session_id}")

    def disconnect(self, session_id: str):
        """Remove a WebSocket connection."""
        self.subscriptions.pop(session_id, None)
        if session_id in self.active_connections:
            del self.active_connections[session_id]
            logger.info(f"WebSocket disconnected: {session_id}")

    def subscribe(self, session_id: str, project_root: str | None):
        """Bind a connection to one project graph (None: user graph only).

        project_root must already be resolved the way REST resolves a
        project_path (safe_project_path); the /ws route does that.
        """
        self.subscriptions[session_id] = project_root

    def _watches(self, session_id: str, project_path: str, session_manager) -> bool:
        """Does this connection receive project-level changes for project_path?"""
        if session_id in self.subscriptions:
            return self.subscriptions[session_id] == project_path
        try:
            return session_manager.get_project_path(session_id) == project_path
        except Exception:
            # Session might be invalid
            return False

    async def send_personal(self, session_id: str, message: dict):
        """Send message to a specific session."""
        if session_id in self.active_connections:
            try:
                await self.active_connections[session_id].send_json(message)
            except Exception as e:
                logger.error(f"Error sending to {session_id}: {e}")
                self.disconnect(session_id)

    async def broadcast_to_project(
        self,
        project_path: str | None,
        message: dict,
        exclude_session: str | None = None,
        session_manager=None
    ):
        """
        Broadcast message to all sessions watching a project.

        Args:
            project_path: Project path to broadcast to (None = user graph only)
            message: Message to send
            exclude_session: Session ID to exclude from broadcast (typically the source)
            session_manager: Session manager to get project paths
        """
        if not session_manager:
            return

        sent = 0
        for session_id in list(self.active_connections.keys()):
            # Skip excluded session
            if session_id == exclude_session:
                continue

            # User-level changes go to everyone. A project-level change goes
            # only to connections watching that project, decided right before
            # each send: a connection that switched project while an earlier
            # send was awaited must not get the old project's change.
            if message.get("level") != "user":
                if not project_path or not self._watches(session_id, project_path, session_manager):
                    continue

            await self.send_personal(session_id, message)
            sent += 1

        if sent:
            logger.debug(f"Broadcast to {sent} sessions: {message.get('type')}")

    async def broadcast_all(self, message: dict):
        """Broadcast message to all connected sessions."""
        disconnected = []

        for session_id, connection in self.active_connections.items():
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error broadcasting to {session_id}: {e}")
                disconnected.append(session_id)

        # Clean up disconnected sessions
        for session_id in disconnected:
            self.disconnect(session_id)

    def count(self) -> int:
        """Return number of active connections."""
        return len(self.active_connections)
