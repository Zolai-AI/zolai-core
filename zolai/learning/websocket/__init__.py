"""
WebSocket Support for Real-time Agent Discussions in Studio.

Enables real-time communication between agents and Studio UI.
"""

from .handlers import DiscussionHandler, MetricsHandler, TaskHandler
from .server import ConnectionManager, WebSocketServer, WSMessage

__all__ = [
    "WebSocketServer",
    "ConnectionManager",
    "WSMessage",
    "DiscussionHandler",
    "TaskHandler",
    "MetricsHandler",
]
