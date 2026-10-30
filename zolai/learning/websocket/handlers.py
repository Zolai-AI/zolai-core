"""
WebSocket Handlers for Agent Discussions, Tasks, and Metrics.

Discussions, tasks and their messages are persisted through
:class:`~zolai.learning.agents.agent_registry.AgentRegistry` (SQLite) and every
request is answered with a response frame.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.shared.clock import stamp

from ..agents.agent_registry import get_agent_registry
from .server import ConnectionManager, WSMessage

logger = logging.getLogger(__name__)


async def _reply(connection_manager: ConnectionManager, connection_id: str,
                 message_type: str, payload: dict, sender_id: str = "server") -> None:
    """Send a response frame to the requesting connection."""
    if connection_manager is None:
        return
    await connection_manager.send_personal(connection_id, WSMessage(
        message_type=message_type,
        payload=payload,
        sender_id=sender_id,
        target_id=connection_id,
    ))


class _RegistryBackedHandler:
    """Shared plumbing: a registry bound to the caller's engine."""

    def __init__(self, engine: Engine = None, registry=None):
        self.registry = registry or get_agent_registry(engine)
        self.engine = engine or self.registry.engine

    def _query(self, sql: str, params: dict = None) -> list:
        """Run a read query against the handler's engine."""
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(text(sql), params or {}).mappings()]

    def _execute(self, sql: str, params: dict = None):
        """Run a write statement against the handler's engine."""
        with self.engine.begin() as conn:
            return conn.execute(text(sql), params or {})

    async def handle_message(self, connection_id: str, message: WSMessage,
                            connection_manager) -> None:
        """Route a message to the matching ``_<verb>`` handler."""
        handler = getattr(self, f"_{message.message_type}", None)
        if handler is None:
            logger.warning("Unhandled message type: %s", message.message_type)
            return
        await handler(connection_id, message, connection_manager)


class DiscussionHandler(_RegistryBackedHandler):
    """Handles real-time agent discussions (persisted in SQLite)."""

    def __init__(self, engine: Engine = None, registry=None,
                 team_manager=None, discussion_facilitator=None):
        super().__init__(engine, registry)
        self.team_manager = team_manager
        self.discussion_facilitator = discussion_facilitator
        self.active_discussions: dict = {}

    # ── Routing ────────────────────────────────────────────────────────────

    async def _start_discussion(self, connection_id: str, message: WSMessage,
                                connection_manager) -> None:
        """Start a new discussion and notify the team."""
        payload = message.payload
        topic = payload.get("topic", "")
        team_id = payload.get("team_id", "default")
        initiator_id = message.sender_id or "anonymous"
        initial_message = payload.get("initial_message", "")

        participants = list(payload.get("participants", []))
        if initiator_id not in participants:
            participants.append(initiator_id)

        discussion_id = self.registry.record_discussion(
            topic, team_id=team_id, participants=participants,
        )
        if initial_message:
            self.registry.record_message(
                discussion_id, from_agent=initiator_id, content=initial_message,
            )
        self.active_discussions[discussion_id] = {
            "topic": topic,
            "team_id": team_id,
            "participants": set(participants),
        }

        if self.discussion_facilitator is not None:
            await self.discussion_facilitator.start_discussion(
                team_id=team_id,
                topic=topic,
                initiator_id=initiator_id,
                initial_message=initial_message,
            )

        await self._notify_team(team_id, "discussion_started", {
            "discussion_id": discussion_id,
            "topic": topic,
            "initiator": initiator_id,
            "message": initial_message,
        })
        await _reply(connection_manager, connection_id, "discussion_started", {
            "discussion_id": discussion_id,
            "topic": topic,
            "status": "active",
        })

    async def _discussion_message(self, connection_id: str, message: WSMessage,
                                  connection_manager) -> None:
        """Add a message to an active discussion."""
        payload = message.payload
        discussion_id = payload.get("discussion_id", "")
        content = payload.get("content", "")
        sender = message.sender_id or "anonymous"

        if discussion_id not in self.active_discussions:
            await _reply(connection_manager, connection_id, "error", {
                "error": f"Unknown discussion: {discussion_id}",
            })
            return

        message_id = self.registry.record_message(
            discussion_id,
            from_agent=sender,
            content=content,
            to_agent=payload.get("to_agent"),
            message_type=payload.get("type", "discussion"),
            metadata={"connection_id": connection_id},
        )
        self.active_discussions[discussion_id]["participants"].add(sender)

        await _reply(connection_manager, connection_id, "discussion_message_ack", {
            "discussion_id": discussion_id,
            "message_id": message_id,
        })

    async def _end_discussion(self, connection_id: str, message: WSMessage,
                              connection_manager) -> None:
        """End a discussion, recording the consensus."""
        payload = message.payload
        discussion_id = payload.get("discussion_id", "")
        team_id = payload.get("team_id", "default")
        consensus = payload.get("consensus", "")

        if discussion_id not in self.active_discussions:
            await _reply(connection_manager, connection_id, "error", {
                "error": f"Unknown discussion: {discussion_id}",
            })
            return

        record = self.active_discussions.pop(discussion_id)
        self.mark_discussion_closed(discussion_id, consensus=consensus)

        if self.discussion_facilitator is not None and consensus:
            await self.discussion_facilitator.conclude_discussion(
                topic=record["topic"],
                facilitator_id=message.sender_id or "facilitator",
                consensus=consensus,
            )

        await self._notify_team(team_id, "discussion_ended", {
            "discussion_id": discussion_id,
            "topic": record["topic"],
            "consensus": consensus,
        })
        await _reply(connection_manager, connection_id, "discussion_ended", {
            "discussion_id": discussion_id,
            "consensus": consensus,
        })

    async def _get_consensus(self, connection_id: str, message: WSMessage,
                             connection_manager) -> None:
        """Return the recorded consensus for a discussion."""
        discussion_id = message.payload.get("discussion_id", "")
        record = self.get_discussion(discussion_id)
        if record is None:
            await _reply(connection_manager, connection_id, "error", {
                "error": f"Unknown discussion: {discussion_id}",
            })
            return

        consensus = record.get("consensus") or ""
        if not consensus and self.discussion_facilitator is not None:
            consensus = await self.discussion_facilitator.build_consensus(
                record.get("topic", "")
            )

        await _reply(connection_manager, connection_id, "consensus_result", {
            "discussion_id": discussion_id,
            "topic": record.get("topic", ""),
            "consensus": consensus,
        })

    async def _get_discussion_status(self, connection_id: str, message: WSMessage,
                                     connection_manager) -> None:
        """Return the status of a discussion."""
        discussion_id = message.payload.get("discussion_id", "")
        record = self.get_discussion(discussion_id)
        if record is None:
            await _reply(connection_manager, connection_id, "error", {
                "error": f"Unknown discussion: {discussion_id}",
            })
            return

        await _reply(connection_manager, connection_id, "discussion_status", {
            "discussion_id": discussion_id,
            "status": record.get("status", "active"),
            "message_count": self.count_messages(discussion_id),
        })

    async def _notify_team(self, team_id: str, event_type: str, payload: dict) -> None:
        """Hook for team-wide fan-out; wired by the hosting application."""
        logger.debug("team event %s for %s: %s", event_type, team_id, payload)

    # ── Registry reads/updates not covered by AgentRegistry ────────────────

    def get_discussion(self, discussion_id: str) -> dict | None:
        """Fetch one discussion row."""
        rows = self._query(
            "SELECT * FROM agent_discussions WHERE discussion_id = :did",
            {"did": discussion_id},
        )
        return rows[0] if rows else None

    def mark_discussion_closed(self, discussion_id: str, consensus: str = "") -> None:
        """Close a discussion and record its consensus (update-only)."""
        self._execute(
            """
            UPDATE agent_discussions
            SET status = 'closed', consensus = :consensus, updated_at = :now
            WHERE discussion_id = :did
            """,
            {
                "did": discussion_id,
                "consensus": consensus,
                "now": stamp(),
            },
        )

    def count_messages(self, discussion_id: str) -> int:
        """Number of messages recorded for a discussion."""
        rows = self._query(
            "SELECT COUNT(*) AS cnt FROM discussion_messages WHERE discussion_id = :did",
            {"did": discussion_id},
        )
        return rows[0]["cnt"] if rows else 0


class TaskHandler(_RegistryBackedHandler):
    """Handles real-time task updates (persisted in SQLite)."""

    def __init__(self, engine: Engine = None, registry=None, orchestrator=None):
        super().__init__(engine, registry)
        self.orchestrator = orchestrator

    def get_task(self, task_id: str) -> dict | None:
        """Fetch one task row."""
        rows = self._query(
            "SELECT * FROM agent_tasks WHERE task_id = :tid", {"tid": task_id}
        )
        return rows[0] if rows else None

    def update_task_status(self, task_id: str, status: str) -> None:
        """Update a task's status (update-only, never deletes)."""
        self._execute(
            "UPDATE agent_tasks SET status = :status, updated_at = :now WHERE task_id = :tid",
            {"tid": task_id, "status": status, "now": stamp()},
        )

    async def _submit_task(self, connection_id: str, message: WSMessage,
                           connection_manager) -> None:
        """Submit a new task via WebSocket."""
        payload = message.payload
        agent_id = payload.get("agent_id", "") or message.sender_id or "unassigned"
        task_payload = payload.get("payload", {})

        if self.orchestrator is not None:
            task_id = await self.orchestrator.submit_task(
                task_type=payload.get("type", ""),
                payload=task_payload,
                priority=payload.get("priority", 5),
            )
        else:
            task_id = self.registry.record_task(
                agent_id,
                task_type=payload.get("type", ""),
                status="pending",
                payload=task_payload,
                team_id=payload.get("team_id"),
            )

        await _reply(connection_manager, connection_id, "task_submitted", {
            "task_id": task_id,
            "status": "queued",
        })

    async def _get_task_status(self, connection_id: str, message: WSMessage,
                               connection_manager) -> None:
        """Get status of a task."""
        task_id = message.payload.get("task_id", "")
        task = self.get_task(task_id)
        if task is None:
            await _reply(connection_manager, connection_id, "error", {
                "error": f"Unknown task: {task_id}",
            })
            return

        await _reply(connection_manager, connection_id, "task_status", {
            "task_id": task_id,
            "status": task.get("status", "pending"),
            "result": task.get("result"),
            "error": task.get("error"),
        })

    async def _cancel_task(self, connection_id: str, message: WSMessage,
                           connection_manager) -> None:
        """Cancel a pending task."""
        task_id = message.payload.get("task_id", "")
        task = self.get_task(task_id)
        if task is None:
            await _reply(connection_manager, connection_id, "error", {
                "error": f"Unknown task: {task_id}",
            })
            return

        self.update_task_status(task_id, "cancelled")
        await _reply(connection_manager, connection_id, "task_cancelled", {
            "task_id": task_id,
            "status": "cancelled",
        })

    async def _subscribe_tasks(self, connection_id: str, message: WSMessage,
                               connection_manager) -> None:
        """Subscribe to task updates for an agent."""
        agent_id = message.payload.get("agent_id", "")
        tasks = self.registry.list_tasks(agent_id=agent_id, limit=message.payload.get("limit", 50))
        await _reply(connection_manager, connection_id, "task_updates", {
            "agent_id": agent_id,
            "tasks": tasks,
        })


class MetricsHandler(_RegistryBackedHandler):
    """Handles real-time metrics streaming."""

    def __init__(self, engine: Engine = None, registry=None, orchestrator=None):
        super().__init__(engine, registry)
        self.orchestrator = orchestrator

    async def _subscribe_metrics(self, connection_id: str, message: WSMessage,
                                 connection_manager) -> None:
        """Subscribe to real-time metrics updates."""
        if connection_manager is not None:
            connection_manager.subscribe(connection_id, message.payload.get("topics", ["metrics"]))
        await _reply(connection_manager, connection_id, "metrics_subscribed", {
            "topics": sorted(connection_manager.get_subscriptions(connection_id))
            if connection_manager else [],
        })

    async def _get_system_status(self, connection_id: str, message: WSMessage,
                                 connection_manager) -> None:
        """Get current system status."""
        if self.orchestrator is not None:
            status = self.orchestrator.get_system_status()
        else:
            agents = self.registry.get_all()
            status = {
                "agents_total": len(agents),
                "agents_enabled": sum(1 for a in agents if a.enabled),
                "recent_tasks": len(self.registry.list_tasks(limit=100)),
            }

        await _reply(connection_manager, connection_id, "system_status", status)

    async def _get_agent_metrics(self, connection_id: str, message: WSMessage,
                                 connection_manager) -> None:
        """Get metrics for a specific agent."""
        agent_id = message.payload.get("agent_id", "")
        agent = self.registry.get(agent_id)
        if agent is None:
            await _reply(connection_manager, connection_id, "error", {
                "error": f"Unknown agent: {agent_id}",
            })
            return

        await _reply(connection_manager, connection_id, "agent_metrics", {
            "agent_id": agent_id,
            "name": agent.name,
            "role": getattr(agent.role, "value", agent.role),
            "enabled": agent.enabled,
            "tasks": len(self.registry.list_tasks(agent_id=agent_id, limit=1000)),
        })
