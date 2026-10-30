"""
WebSocket Server for Real-time Agent Communication.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState

from zolai.shared.clock import now, stamp

logger = logging.getLogger(__name__)


@dataclass
class WSMessage:
    """WebSocket message structure."""

    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    message_type: str = ""  # message, agent_status, task_update, discussion, metrics, ping
    payload: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=now)
    sender_id: str = ""
    target_id: str | None = None  # None = broadcast

    def to_json(self) -> str:
        return json.dumps({
            "message_id": self.message_id,
            "type": self.message_type,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
            "sender": self.sender_id,
            "target": self.target_id,
        })

    @classmethod
    def from_json(cls, data: str) -> WSMessage:
        data = json.loads(data)
        return cls(
            message_id=data.get("message_id", str(uuid.uuid4())),
            message_type=data.get("type", ""),
            payload=data.get("payload", {}),
            timestamp=datetime.fromisoformat(data.get("timestamp", stamp())),
            sender_id=data.get("sender", ""),
            target_id=data.get("target"),
        )


class ConnectionManager:
    """Manages WebSocket connections."""

    def __init__(self):
        self.active_connections: dict = {}
        self.agent_connections: dict = {}  # agent_id -> connection_ids
        self.user_connections: dict = {}  # user_id -> connection_ids
        self.topic_subscriptions: dict = {}  # topic -> connection_ids
        self.connection_metadata: dict = {}

    async def connect(
        self,
        websocket: WebSocket,
        connection_id: str = None,
        agent_id: str = None,
        user_id: str = None,
    ) -> str:
        """Accept a new WebSocket connection."""
        await websocket.accept()

        conn_id = connection_id or str(uuid.uuid4())
        self.active_connections[conn_id] = websocket
        self.connection_metadata[conn_id] = {
            "connected_at": stamp(),
            "agent_id": agent_id,
            "user_id": user_id,
            "topics": set(),
            "last_ping": stamp(),
        }

        if agent_id:
            self.agent_connections.setdefault(agent_id, set()).add(conn_id)

        if user_id:
            self.user_connections.setdefault(user_id, set()).add(conn_id)

        logger.info(f"WebSocket connected: {conn_id} (agent: {agent_id}, user: {user_id})")
        return conn_id

    def disconnect(self, connection_id: str):
        """Disconnect a WebSocket."""
        meta = self.connection_metadata.get(connection_id, {})
        # Release topic subscriptions while the metadata row still exists.
        for topic in list(meta.get("topics", ())):
            self.unsubscribe(connection_id, topic)

        if connection_id in self.active_connections:
            ws = self.active_connections.pop(connection_id)
            if ws.client_state != WebSocketState.DISCONNECTED:
                asyncio.create_task(ws.close())

        self.connection_metadata.pop(connection_id, {})
        agent_id = meta.get("agent_id")
        user_id = meta.get("user_id")

        if agent_id and agent_id in self.agent_connections:
            self.agent_connections[agent_id].discard(connection_id)
            if not self.agent_connections[agent_id]:
                del self.agent_connections[agent_id]

        if user_id and user_id in self.user_connections:
            self.user_connections[user_id].discard(connection_id)
            if not self.user_connections[user_id]:
                del self.user_connections[user_id]

        logger.info(f"WebSocket disconnected: {connection_id}")

    # ── Topic subscriptions ────────────────────────────────────────────────

    def subscribe(self, connection_id: str, topics) -> set:
        """Subscribe a connection to topics; returns the resulting topic set."""
        if connection_id not in self.connection_metadata:
            return set()

        current = self.connection_metadata[connection_id]["topics"]
        for topic in topics:
            if not topic:
                continue
            current.add(topic)
            self.topic_subscriptions.setdefault(topic, set()).add(connection_id)
        return current

    def unsubscribe(self, connection_id: str, topics) -> set:
        """Unsubscribe a connection from topics; returns the remaining set."""
        meta = self.connection_metadata.get(connection_id)
        if not meta:
            return set()

        current = meta["topics"]
        for topic in topics:
            current.discard(topic)
            subscribers = self.topic_subscriptions.get(topic)
            if subscribers:
                subscribers.discard(connection_id)
                if not subscribers:
                    del self.topic_subscriptions[topic]
        return current

    def get_subscriptions(self, connection_id: str) -> set:
        """Topics a connection is subscribed to."""
        meta = self.connection_metadata.get(connection_id)
        return set(meta["topics"]) if meta else set()

    def get_topic_subscribers(self, topic: str) -> set:
        """Connections subscribed to a topic."""
        return set(self.topic_subscriptions.get(topic, set()))

    # ── Sending ────────────────────────────────────────────────────────────

    async def send_personal(self, connection_id: str, message: WSMessage):
        """Send message to a specific connection."""
        ws = self.active_connections.get(connection_id)
        if ws and ws.client_state == WebSocketState.CONNECTED:
            try:
                await ws.send_text(message.to_json())
            except Exception as e:
                logger.error(f"Failed to send to {connection_id}: {e}")

    async def broadcast_to_agents(self, agent_ids: list, message: WSMessage):
        """Broadcast message to all connections of specified agents."""
        for agent_id in agent_ids:
            for conn_id in self.get_agent_connections(agent_id):
                await self.send_personal(conn_id, message)

    async def broadcast_to_users(self, user_ids: list, message: WSMessage):
        """Broadcast message to all connections of specified users."""
        for user_id in user_ids:
            for conn_id in self.get_user_connections(user_id):
                await self.send_personal(conn_id, message)

    async def broadcast_all(self, message: WSMessage):
        """Broadcast to all connected clients."""
        for conn_id in list(self.active_connections.keys()):
            await self.send_personal(conn_id, message)

    async def broadcast_to_topic(self, topic: str, message: WSMessage):
        """Broadcast to every connection subscribed to ``topic``."""
        for conn_id in self.get_topic_subscribers(topic):
            await self.send_personal(conn_id, message)

    def get_agent_connections(self, agent_id: str) -> set:
        """Connection ids belonging to an agent."""
        return set(self.agent_connections.get(agent_id, set()))

    def get_user_connections(self, user_id: str) -> set:
        """Connection ids belonging to a user."""
        return set(self.user_connections.get(user_id, set()))

    def get_connection_count(self) -> int:
        return len(self.active_connections)

    def get_agent_connection_count(self, agent_id: str) -> int:
        return len(self.agent_connections.get(agent_id, set()))

    def get_user_connection_count(self, user_id: str) -> int:
        return len(self.user_connections.get(user_id, set()))


class WebSocketServer:
    """WebSocket server for real-time agent communication."""

    def __init__(self):
        self.manager = ConnectionManager()
        self.handlers: dict = {}
        self.register_default_handlers()

    def register_default_handlers(self):
        """Register default message handlers."""
        self.handlers["ping"] = self._handle_ping
        self.handlers["subscribe"] = self._handle_subscribe
        self.handlers["unsubscribe"] = self._handle_unsubscribe

    def register_handler(self, message_type: str, handler: Callable):
        """Register a custom message handler."""
        self.handlers[message_type] = handler

    async def handle_connection(
        self,
        websocket: WebSocket,
        agent_id: str = None,
        user_id: str = None,
    ):
        """Handle a WebSocket connection lifecycle."""
        connection_id = await self.manager.connect(websocket, agent_id=agent_id, user_id=user_id)

        try:
            # Send welcome message
            await self.manager.send_personal(connection_id, WSMessage(
                message_type="welcome",
                payload={"connection_id": connection_id},
                sender_id="server",
                target_id=connection_id,
            ))

            # Message loop
            while True:
                try:
                    data = await websocket.receive_text()
                    message = WSMessage.from_json(data)
                    await self._route_message(connection_id, message)
                except WebSocketDisconnect:
                    break
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    await self._send_error(connection_id, str(e))
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"Connection error: {e}")
        finally:
            self.manager.disconnect(connection_id)

    async def _send_error(self, connection_id: str, detail: str):
        """Send an error frame back to a connection."""
        await self.manager.send_personal(connection_id, WSMessage(
            message_type="error",
            payload={"error": detail},
            sender_id="server",
            target_id=connection_id,
        ))

    async def _route_message(self, connection_id: str, message: WSMessage):
        """Route message to appropriate handler."""
        handler = self.handlers.get(message.message_type)
        if handler:
            try:
                await handler(connection_id, message)
            except Exception as e:
                logger.error(f"Handler error for {message.message_type}: {e}")
                await self._send_error(connection_id, f"Handler error: {e}")
        else:
            # Unknown message type
            logger.warning(f"Unknown message type: {message.message_type}")

    async def _handle_ping(self, connection_id: str, message: WSMessage):
        """Handle ping message."""
        meta = self.manager.connection_metadata.get(connection_id)
        if meta is not None:
            meta["last_ping"] = stamp()

        await self.manager.send_personal(connection_id, WSMessage(
            message_type="pong",
            payload={"server_time": stamp()},
            sender_id="server",
            target_id=connection_id,
        ))

    async def _handle_subscribe(self, connection_id: str, message: WSMessage):
        """Handle subscription to topics."""
        topics = message.payload.get("topics", [])
        current = self.manager.subscribe(connection_id, topics)
        await self.manager.send_personal(connection_id, WSMessage(
            message_type="subscribed",
            payload={"topics": sorted(current)},
            sender_id="server",
            target_id=connection_id,
        ))

    async def _handle_unsubscribe(self, connection_id: str, message: WSMessage):
        """Handle unsubscription from topics."""
        topics = message.payload.get("topics", [])
        current = self.manager.unsubscribe(connection_id, topics)
        await self.manager.send_personal(connection_id, WSMessage(
            message_type="unsubscribed",
            payload={"topics": sorted(current)},
            sender_id="server",
            target_id=connection_id,
        ))

    def get_stats(self) -> dict:
        """Get server statistics."""
        return {
            "total_connections": len(self.manager.active_connections),
            "agent_connections": {
                agent: len(conns) for agent, conns in self.manager.agent_connections.items()
            },
            "user_connections": {
                user: len(conns) for user, conns in self.manager.user_connections.items()
            },
            "topics": {
                topic: len(conns) for topic, conns in self.manager.topic_subscriptions.items()
            },
        }
