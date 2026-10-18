"""The raw-exception-text guard — a response must never carry ``str(exc)``.

What this file enforces
-----------------------
An ``HTTPException`` whose ``detail`` embeds the exception text (``str(e)``, an
f-string interpolating the handler's ``except ... as e`` name, or the same
inside a ``detail={...}`` literal) hands the caller SQL, filesystem paths,
driver internals and tracebacks. Several of the routes below are **public**,
so an anonymous caller was reading them.

The scan is an AST walk over ``zolai/api/*.py`` — not a grep — so a renamed
variable or a nested ``detail`` literal is caught too.

``ALLOWLIST``
-------------
Narrow ``except ValueError`` handlers whose message this repository authors
itself: a string literal, or the caller's own identifier. Each entry records
*why* it is safe and asserts, in
:func:`test_allowlist_entries_are_still_present`, that its source line still
carries the documented code — so deleting or retyping a handler surfaces here
instead of silently inheriting an exemption. Do not widen it: a new entry must
name a raise site inside this repository and say which one.

Behavioural coverage (:func:`test_*` below) proves the envelope on real
requests: the response keeps its status code and gains a human-readable
``message``, the raw text is absent from the body, and it *is* in the log.
"""
from __future__ import annotations

import ast
import logging
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zolai.api.errors import api_error, internal_error_envelope, internal_http_error
from zolai.api.foundation_router import router as foundation_router

API_DIR = Path(__file__).resolve().parents[1] / "zolai" / "api"


@dataclass(frozen=True)
class AllowlistEntry:
    """One documented exemption, pinned to the *source text* it was granted for.

    The exemption is matched by ``marker`` rather than a line number, so moving
    the handler up the file cannot silently transfer it to different code — and
    :func:`test_allowlist_entries_are_still_present` fails when an entry no
    longer corresponds to a real violation, so exemptions cannot quietly
    accumulate.
    """

    path: str
    code: str
    marker: str
    reason: str


ALLOWLIST: tuple[AllowlistEntry, ...] = (
    AllowlistEntry(
        path="agent_router.py",
        code="invalid_goal",
        marker='"error": "invalid_goal"',
        reason=(
            "`except ValueError` on run_agent_goal(), whose only raise site is "
            "zolai/agent/orchestrator.py:208 `raise ValueError(\"goal must not be empty\")` — a "
            "literal, no I/O, no caller data. Widening the handler to Exception would invalidate "
            "this entry (the marker assertion below fails) because it would then carry driver text."
        ),
    ),
    AllowlistEntry(
        path="learning_agents_router.py",
        code="agent_conflict",
        marker='"error": "agent_conflict"',
        reason=(
            "`except ValueError` on agent_management.create_agent(); the reachable raise sites are "
            "agent_registry.py:595/597 (`\"agent_id is required\"`, "
            "`f\"agent already registered: {agent_id!r}\"`) — a literal plus the caller's own "
            "identifier. No SQL, path or driver text can reach it."
        ),
    ),
    AllowlistEntry(
        path="learning_agents_router.py",
        code="invalid_tuning",
        marker='"error": "invalid_tuning"',
        reason=(
            "`except ValueError` on agent_management.update_agent(); same in-repo raise sites "
            "(agent_registry.py:261/270/284) whose messages interpolate only the caller's own "
            "role/agent_id."
        ),
    ),
    AllowlistEntry(
        path="learning_tools_router.py",
        code="model_conflict",
        marker='"error": "model_conflict"',
        reason=(
            "`except ValueError` on ModelManager.delete_model(); the only raise site is "
            "zolai/learning/finetuning/model_manager.py:203 "
            "`raise ValueError(\"Cannot delete active model without force=True\")` — a literal."
        ),
    ),
    AllowlistEntry(
        path="learning_tools_router.py",
        code="no_feedback_signals",
        marker='"error": "no_feedback_signals"',
        reason=(
            "`except ValueError` on the training pipeline; the only raise site is "
            "zolai/learning/finetuning/pipeline.py:152 "
            "`raise ValueError(\"No feedback signals available for training\")` — a literal."
        ),
    ),
)


# ── the scan ────────────────────────────────────────────────────────────

_RESERVED_HTTP = frozenset({"HTTPException"})


def _exc_names(tree: ast.AST) -> set[str]:
    """Every name bound by an ``except ... as name:`` handler in the module."""
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler) and n.name}


def _is_str_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str"


def _embeds_exception(node: ast.AST, names: set[str]) -> str | None:
    """Why ``node`` leaks exception text, or ``None`` when it is clean."""
    if isinstance(node, ast.JoinedStr):
        for value in ast.walk(node):
            if isinstance(value, ast.FormattedValue):
                inner = value.value
                if isinstance(inner, ast.Name) and inner.id in names:
                    return "f-string interpolates the handler's exception name"
                if _is_str_call(inner):
                    return "f-string interpolates str(exception)"
        return None
    if _is_str_call(node):
        if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id in names:
            return "str(exception)"
        if any(isinstance(a, ast.Starred) for a in node.args):
            return "str(*args)"
    return None


def _detail_values(call: ast.Call) -> list[ast.AST]:
    return [kw.value for kw in call.keywords if kw.arg == "detail"]


def leaking_sites(path: Path) -> list[tuple[int, str, str]]:
    """``(lineno, reason, snippet)`` for every detail carrying exception text.

    ``snippet`` is the whitespace-normalised source of the whole
    ``HTTPException(...)`` call, so an exemption can name the code it was
    granted for even when that code sits on a continuation line.
    """
    source = path.read_text()
    tree = ast.parse(source)
    names = _exc_names(tree)
    hits: list[tuple[int, str, str]] = []

    def walk_detail(node: ast.AST, lineno: int, snippet: str, prefix: str) -> None:
        reason = _embeds_exception(node, names)
        if reason:
            hits.append((lineno, f"{prefix}{reason}", snippet))
        if isinstance(node, (ast.Dict, ast.List, ast.Tuple, ast.Set)):
            for child in ast.walk(node):
                if child is node:
                    continue
                child_reason = _embeds_exception(child, names)
                if child_reason:
                    hits.append((lineno, f"{prefix}literal contains {child_reason}", snippet))

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id in _RESERVED_HTTP:
            segment = ast.get_source_segment(source, node) or ""
            snippet = " ".join(segment.split())
            for detail in _detail_values(node):
                walk_detail(detail, node.lineno, snippet, "detail=")
    return sorted(set(hits))


def _exempt(path: Path, snippet: str) -> AllowlistEntry | None:
    """The entry exempting this exact ``HTTPException`` call, if one exists."""
    for entry in ALLOWLIST:
        if entry.path == path.name and entry.marker in snippet:
            return entry
    return None


def test_no_raw_exception_text_in_http_exception_detail() -> None:
    """Fail on any raw exception text reaching an HTTPException detail."""
    violations: list[str] = []
    for path in sorted(API_DIR.glob("*.py")):
        for lineno, reason, snippet in leaking_sites(path):
            if _exempt(path, snippet) is None:
                violations.append(f"{path.name}:{lineno}: {reason}")
    assert not violations, "raw exception text reaches a response:\n" + "\n".join(violations)


def test_allowlist_entries_are_still_present() -> None:
    """Every exemption must still cover exactly one real violation."""
    unused: list[str] = []
    for entry in ALLOWLIST:
        path = API_DIR / entry.path
        if not path.is_file():
            unused.append(f"{entry.path}: file no longer exists")
            continue
        matched = [
            lineno for lineno, _, snippet in leaking_sites(path) if entry.marker in snippet
        ]
        if len(matched) != 1:
            unused.append(
                f"{entry.path} [{entry.code}]: {len(matched)} matching site(s) for marker "
                f"{entry.marker!r} — re-review, then update or delete this entry"
            )
    assert not unused, "stale allowlist entries:\n" + "\n".join(unused)


# ── the envelope itself ──────────────────────────────────────────────────


def test_api_error_is_the_house_envelope() -> None:
    """``error`` first, then the human ``message``; extra keys survive."""
    assert api_error("boom", "Try again.") == {"error": "boom", "message": "Try again."}
    assert api_error("boom", "Try again.", item_id=7) == {
        "error": "boom",
        "message": "Try again.",
        "item_id": 7,
    }


def test_internal_http_error_logs_the_raw_and_hides_it(caplog: pytest.LogCaptureFixture) -> None:
    """The helper's whole point: logged in full, answered in part."""
    raw = "sqlite3.OperationalError: no such table: dictionary (/srv/zolai/data/zolai.db)"
    logger = logging.getLogger("guard-test")
    with caplog.at_level(logging.ERROR):
        try:
            raise RuntimeError(raw)
        except RuntimeError as caught:
            # called exactly as the routers call it: inside the handler
            exc = internal_http_error(
                caught,
                logger=logger,
                status_code=503,
                operation="guard.self_test",
                code="unavailable",
                message="The thing is unavailable right now. Retry in a few seconds.",
                item_id=7,
            )
    assert exc.status_code == 503
    assert exc.detail == {
        "error": "unavailable",
        "message": "The thing is unavailable right now. Retry in a few seconds.",
    }
    assert "item_id" not in exc.detail  # log context is never echoed back
    assert raw in caplog.text  # the operator keeps the whole truth
    assert "Traceback" in caplog.text
    assert "guard.self_test" in caplog.text


def test_internal_error_envelope_survives_a_reserved_log_key(caplog: pytest.LogCaptureFixture) -> None:
    """A context key that collides with a LogRecord field is dropped, not fatal."""
    with caplog.at_level(logging.ERROR):
        try:
            raise ValueError("boom")
        except ValueError as caught:
            envelope = internal_error_envelope(
                caught,
                logger=logging.getLogger("guard-test-reserved"),
                operation="guard.reserved",
                code="boom",
                message="Retry.",
                **{"name": "shadow", "args": "shadow"},
            )
    assert envelope == {"error": "boom", "message": "Retry."}
    assert "guard.reserved failed (boom): ValueError: boom" in caplog.text


# ── behavioural proof on real requests ───────────────────────────────────

LEAKY = "SELECT * FROM dictionary -- /srv/zolai/data/zolai.db: no such column: zolai"


@pytest.fixture()
def client() -> TestClient:
    """Foundation router only — no database, no lifespan, no migrations."""
    application = FastAPI()
    application.include_router(foundation_router, prefix="/api/v1")
    # Deliberately NOT a context manager: entering it would run the lifespan.
    return TestClient(application, raise_server_exceptions=False)


class _BoomRepo:
    def __getattr__(self, name: str) -> Any:
        def _raise(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError(LEAKY)

        return _raise


@pytest.fixture()
def broken_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every foundation repository call fails the way a broken DB would."""
    monkeypatch.setattr(
        "zolai.api.foundation_router._get_repos",
        lambda: {"foundation_review_queue": _BoomRepo()},
    )


def test_error_body_is_human_readable_and_leak_free(
    client: TestClient, broken_repos: None, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        response = client.get("/api/v1/foundation/review/queue")
    assert response.status_code == 500  # unchanged: an unexpected failure was always 500

    detail = response.json()["detail"]
    assert detail["error"] == "review_queue_unavailable"
    assert detail["message"] == (
        "The review queue could not be read. Retry in a few seconds; if it keeps failing, the "
        "ingestion pipeline needs a look."
    )
    body = response.text
    assert "SELECT" not in body
    assert "zolai.db" not in body
    assert "/srv/zolai" not in body
    assert "Traceback" not in body
    assert "RuntimeError" not in body

    # the raw text still reaches the operator, with the traceback
    assert LEAKY in caplog.text
    assert "foundation.review_queue.list" in caplog.text
    assert "Traceback" in caplog.text


@pytest.mark.parametrize(
    ("path", "body", "operation", "code"),
    [
        (
            "/api/v1/foundation/analyze/corpus",
            {"text": "Pasian in leitung a piangsak hi."},
            "foundation.analyze.corpus",
            "analysis_failed",
        ),
        (
            "/api/v1/foundation/search/ranked",
            {"query": "pasian"},
            "foundation.search.ranked",
            "search_failed",
        ),
        (
            "/api/v1/foundation/progress/error-categorize",
            {"original": "pathian", "corrected": "pasian"},
            "foundation.progress.error_categorize",
            "categorization_failed",
        ),
    ],
)
def test_converted_post_endpoints_hide_the_exception(
    path: str,
    body: dict[str, str],
    operation: str,
    code: str,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The analysis/search endpoints leak through their engine, not the repositories."""
    module, attr, factory = {
        "/api/v1/foundation/analyze/corpus": (
            "zolai.foundation.corpus", "get_corpus_analyzer", lambda: _Boom()
        ),
        "/api/v1/foundation/search/ranked": (
            "zolai.learning.online_search", "get_online_search", lambda: _Boom()
        ),
        "/api/v1/foundation/progress/error-categorize": (
            "zolai.learning.progress", "ProgressTracker", _Boom
        ),
    }[path]
    stub = types.ModuleType(module)
    setattr(stub, attr, factory)
    monkeypatch.setitem(sys.modules, module, stub)

    with caplog.at_level(logging.ERROR):
        response = client.post(path, json=body)

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["error"] == code
    assert isinstance(detail["message"], str) and len(detail["message"]) > 20
    assert LEAKY not in response.text
    assert "Traceback" not in response.text
    assert LEAKY in caplog.text
    assert operation in caplog.text


class _Boom:
    def __getattr__(self, name: str) -> Any:
        def _raise(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError(LEAKY)

        return _raise

    @staticmethod
    def categorize_correction(original: str, corrected: str) -> dict[str, str]:
        raise RuntimeError(LEAKY)


# ── status codes and success shapes are untouched ────────────────────────


class _OnePendingItem:
    """A review repo that answers exactly one pending item."""

    def get_pending(self, limit: int = 1000) -> list[dict[str, Any]]:
        return [{"id": 1, "fact_type": "word", "fact_key": "word:pasian", "priority": 0, "status": "pending"}]

    def get_by_id(self, item_id: int) -> dict[str, Any] | None:
        if item_id != 1:
            return None
        return {"id": 1, "fact_type": "word", "fact_key": "word:pasian", "status": "pending"}


@pytest.fixture()
def one_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "zolai.api.foundation_router._get_repos",
        lambda: {"foundation_review_queue": _OnePendingItem()},
    )


def test_success_shape_unchanged(client: TestClient, one_pending: None) -> None:
    body = client.get("/api/v1/foundation/review/queue").json()
    assert set(body) == {"items", "total", "page", "page_size", "total_pages"}
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert body["total_pages"] == 1
    assert body["items"][0]["fact_key"] == "word:pasian"


def test_missing_review_item_is_still_404(client: TestClient, one_pending: None) -> None:
    """A genuine 404 stays a 404 — the conversion does not flatten statuses."""
    response = client.get("/api/v1/foundation/review/4242")
    assert response.status_code == 404
    assert response.json()["detail"] == "Review item 4242 not found"


def test_bad_input_is_still_400(client: TestClient, one_pending: None) -> None:
    response = client.post("/api/v1/foundation/search/cross-lingual", json={"query": "x", "source_lang": "fr"})
    assert response.status_code == 400
    assert response.json()["detail"] == "source_lang must be 'en' or 'zo'"
