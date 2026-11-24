"""Bounded autonomous queue/worker for domain runs.

Disabled by default. Requires ZOLAI_AGENT_AUTONOMOUS=1. No infinite loops
unless poll mode is explicitly used; --once processes at most one job.
All timestamps via zolai.shared.clock.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

from sqlalchemy import text as sa_text

from ...data.repositories import get_engine
from ...shared.clock import now, stamp
from .. import store

QUEUE_MAX_ATTEMPTS = max(1, int(os.environ.get("AGENT_QUEUE_MAX_ATTEMPTS", "3")))
QUEUE_LEASE_SECONDS = max(30, int(os.environ.get("AGENT_QUEUE_LEASE_SECONDS", "300")))


def _autonomous_enabled() -> bool:
    return os.environ.get("ZOLAI_AGENT_AUTONOMOUS", "").strip().lower() in {"1", "true", "yes", "on"}


def enqueue_domain_run(
    domain_run_id: int, *, priority: int = 0, max_attempts: int = QUEUE_MAX_ATTEMPTS
) -> dict[str, Any]:
    eng = get_engine()
    store.ensure_domain_agent_tables(eng)
    now_s = stamp()
    with eng.begin() as conn:
        conn.execute(
            sa_text(
                "INSERT OR IGNORE INTO agent_queue ("
                "domain_run_id, priority, available_at, attempts, max_attempts, "
                "lock_owner, lock_until, status, created_at, updated_at"
                ") VALUES ("
                ":domain_run_id, :priority, :available_at, 0, :max_attempts, "
                "'', NULL, 'queued', :created_at, :updated_at"
                ")"
            ),
            {
                "domain_run_id": int(domain_run_id),
                "priority": int(priority),
                "available_at": now_s,
                "max_attempts": max(1, int(max_attempts)),
                "created_at": now_s,
                "updated_at": now_s,
            },
        )
    return {"domain_run_id": int(domain_run_id), "status": "queued"}


def _future_lease(seconds: int = QUEUE_LEASE_SECONDS) -> str:
    from datetime import timedelta

    return stamp(now() + timedelta(seconds=max(30, int(seconds))))


def _claim_one(lock_owner: str) -> dict[str, Any] | None:
    eng = get_engine()
    store.ensure_domain_agent_tables(eng)
    now_s = stamp()
    lease_until = _future_lease()
    with eng.begin() as conn:
        row = conn.execute(
            sa_text(
                "SELECT id, domain_run_id, attempts, max_attempts FROM agent_queue "
                "WHERE status IN ('queued','failed') AND available_at <= :now "
                "AND (lock_until IS NULL OR lock_until <= :now) "
                "ORDER BY priority DESC, id ASC LIMIT 1"
            ),
            {"now": now_s},
        ).fetchone()
        if row is None:
            return None
        qid = int(row[0])
        attempts = int(row[2] or 0)
        max_attempts = int(row[3] or QUEUE_MAX_ATTEMPTS)
        if attempts >= max_attempts:
            conn.execute(
                sa_text(
                    "UPDATE agent_queue SET status='dead', lock_owner='', lock_until=NULL, updated_at=:u "
                    "WHERE id=:id AND status IN ('queued','failed')"
                ),
                {"id": qid, "u": now_s},
            )
            return None
        # Conditional claim guard: only claim if still available/unlocked.
        result = conn.execute(
            sa_text(
                "UPDATE agent_queue SET status='running', lock_owner=:owner, lock_until=:lu, "
                "attempts=attempts+1, updated_at=:u "
                "WHERE id=:id AND status IN ('queued','failed') "
                "AND (lock_until IS NULL OR lock_until <= :now)"
            ),
            {"owner": lock_owner, "lu": lease_until, "u": now_s, "id": qid, "now": now_s},
        )
        if int(getattr(result, "rowcount", 0) or 0) <= 0:
            return None
    return {
        "queue_id": qid,
        "domain_run_id": int(row[1]),
        "attempts": attempts + 1,
        "max_attempts": max_attempts,
        "lock_until": lease_until,
    }


def _complete(queue_id: int, *, status: str, error: str = "") -> None:
    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(
            sa_text(
                "UPDATE agent_queue SET status=:status, lock_owner='', lock_until=NULL, "
                "updated_at=:u WHERE id=:id"
            ),
            {"status": status, "u": stamp(), "id": int(queue_id)},
        )


def process_one(lock_owner: str | None = None) -> dict[str, Any]:
    owner = lock_owner or f"worker-{uuid.uuid4().hex[:8]}"
    job = _claim_one(owner)
    if job is None:
        return {"processed": False, "reason": "empty"}
    eng = get_engine()
    run = store.get_domain_run(int(job["domain_run_id"]), engine=eng)
    if run is None:
        _complete(int(job["queue_id"]), status="dead", error="domain run not found")
        return {"processed": True, "queue_id": job["queue_id"], "status": "dead", "error": "domain run not found"}
    if not _autonomous_enabled():
        _complete(int(job["queue_id"]), status="queued")
        return {"processed": False, "reason": "autonomous_disabled", "queue_id": job["queue_id"]}

    from .runner import execute_queued_domain_run

    try:
        result = execute_queued_domain_run(int(job["domain_run_id"]), engine=eng)
        status = str(result.get("status") or "failed")
        if status in {"succeeded", "manual", "blocked"}:
            qstatus = "succeeded" if status == "succeeded" else "manual"
            _complete(int(job["queue_id"]), status=qstatus)
        else:
            attempts = int(job.get("attempts") or 1)
            max_attempts = int(job.get("max_attempts") or QUEUE_MAX_ATTEMPTS)
            if attempts >= max_attempts:
                _complete(int(job["queue_id"]), status="dead", error=str(result.get("error") or "failed"))
                qstatus = "dead"
            else:
                # requeue with backoff
                with eng.begin() as conn:
                    conn.execute(
                        sa_text(
                            "UPDATE agent_queue SET status='failed', lock_owner='', lock_until=NULL, "
                            "available_at=:avail, updated_at=:u WHERE id=:id"
                        ),
                        {
                            "id": int(job["queue_id"]),
                            "u": stamp(),
                            "avail": stamp(now()),

                        },
                    )
                qstatus = "failed"
        return {
            "processed": True,
            "queue_id": job["queue_id"],
            "domain_run_id": job["domain_run_id"],
            "status": qstatus,
            "run_status": status,
        }
    except Exception as exc:
        attempts = int(job.get("attempts") or 1)
        max_attempts = int(job.get("max_attempts") or QUEUE_MAX_ATTEMPTS)
        qstatus = "dead" if attempts >= max_attempts else "failed"
        if qstatus == "failed":
            with eng.begin() as conn:
                conn.execute(
                    sa_text(
                        "UPDATE agent_queue SET status='failed', lock_owner='', lock_until=NULL, "
                        "available_at=:avail, updated_at=:u WHERE id=:id"
                    ),
                    {
                        "id": int(job["queue_id"]),
                        "u": stamp(),
                        "avail": stamp(now()),

                    },
                )
        else:
            _complete(int(job["queue_id"]), status="dead", error=str(exc))
        return {
            "processed": True,
            "queue_id": job["queue_id"],
            "domain_run_id": job["domain_run_id"],
            "status": qstatus,
            "error": str(exc),
        }


def run_worker(*, once: bool = False, poll_seconds: int = 5, max_jobs: int = 100) -> dict[str, Any]:
    if not _autonomous_enabled():
        return {"status": "disabled", "reason": "ZOLAI_AGENT_AUTONOMOUS not enabled"}
    owner = f"worker-{uuid.uuid4().hex[:8]}"
    processed = 0
    results = []
    poll_seconds = max(1, int(poll_seconds or 5))
    max_jobs = max(1, int(max_jobs or 100))
    while processed < max_jobs:
        r = process_one(owner)
        results.append(r)
        if r.get("processed"):
            processed += 1
            if once:
                break
            continue
        if once:
            break
        time.sleep(poll_seconds)
    return {"status": "ok", "owner": owner, "processed": processed, "results": results[:20]}
