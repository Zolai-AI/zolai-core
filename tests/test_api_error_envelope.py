"""The raw-exception-text guard — a response must never carry ``str(exc)``.

What this file enforces
-----------------------
An ``HTTPException`` whose ``detail`` embeds the exception text (``str(e)``, an
f-string interpolating the handler's ``except ... as e`` name, or the same
inside a ``detail={...}`` literal) hands the caller SQL, filesystem paths,
driver internals and tracebacks. Several of the routes below are **public**,
so an anonymous caller was reading them.

The same leak has a second envelope: a handler that catches, logs nothing,
and returns ``{"error": str(e)}`` inside an otherwise ordinary **200** body.
``body_leaking_sites`` scans for that shape — exception text (or a subprocess
``stderr``, which in the common case *is* a traceback) reaching a ``return``
value, a response constructor (``JSONResponse`` / ``jsonable_encoder`` / …),
a dict/list value written from a handler (``jobs[id]["error"] = str(e)``),
or a container mutation (``errors.append(str(e))``). Status codes and
response *shapes* are part of the callers' contract, so the fix at every site
was to keep the shape and swap only the message value.

The scan is an AST walk over ``zolai/api/*.py`` — not a grep — so a renamed
variable or a nested ``detail`` literal is caught too. Exception bindings are
resolved **per enclosing function**, so a processed ``HTTPException.detail``
echo (server.py's exception handler, whose ``exc`` is a parameter rather than
a handler binding) is not a false positive.

``ALLOWLIST``
-------------
Narrow ``except ValueError`` handlers whose message this repository authors
itself: a string literal, or the caller's own identifier. Each entry records
*why* it is safe and asserts, in
:func:`test_allowlist_entries_are_still_present`, that its source line still
carries the documented code — so deleting or retyping a handler surfaces here
instead of silently inheriting an exemption. Do not widen it: a new entry must
name a raise site inside this repository and say which one. One entry
(``legacy_gemini_chat_error``) is a **known, deliberately deferred leak** in
``server.py`` and is documented as such, not as "safe".

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

from zolai.api.desktop_router import router as desktop_router
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
        path="admin_users_router.py",
        code="user_not_found",
        marker='detail=_detail("user_not_found"',
        reason=(
            "_http_from maps session_auth's LookupError to 404 and falls back to str(exc) only "
            "when no username reached it; the raise site is zolai/api/session_auth.py's "
            "\"unknown user\" lookup, a literal. Kept as-is because it is the documented ce04c72 "
            "response contract, not an accidental leak."
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
    AllowlistEntry(
        path="server.py",
        code="legacy_gemini_chat_error",
        marker='zolai_response=f"Gemini error: {str(e)}"',
        reason=(
            "KNOWN LEAK, DELIBERATELY DEFERRED (2026-10-18): the legacy POST /chat/gemini "
            "handler returns the raw exception text inside a 200 ZolaiChatResponse "
            "(zolai/api/server.py, `except Exception as e:` above the return). server.py is "
            "shared — 3 pre-existing I001 errors plus another session's MCP hunks — and the "
            "standing directive for this pass is to not touch it, so this one site is the "
            "reported follow-up rather than a silent exemption. The marker pins the exact "
            "expression: if the handler is fixed, this entry stops matching exactly one "
            "violation and `test_allowlist_entries_are_still_present` fails, forcing the "
            "entry's deletion."
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
        # Every descendant, not just a literal: `_detail(str(exc))` hides the
        # raw text behind a helper call, and that was a real site.
        for child in ast.walk(node):
            if child is node:
                reason = _embeds_exception(node, names)
            else:
                reason = _embeds_exception(child, names)
            if reason:
                hits.append((lineno, f"{prefix}{reason}", snippet))

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


# ── the 200-body scan ────────────────────────────────────────────────────
#
# Same defect, different envelope: the handler keeps HTTP 200 and the
# response *shape* callers depend on, but the message value is the raw
# exception (``{"error": str(e)}``), a stored write that a status endpoint
# later echoes (``jobs[id]["error"] = str(e)``), or a container mutation
# (``errors.append(str(e))``).

#: Exception types whose ``str()``/detail is a *processed* envelope, not raw
#: text — echoing one is not this defect.
_BODY_RESERVED = frozenset({"HTTPException"})

#: Constructors that serialise straight into a response body.
_RESPONSE_CTORS = frozenset(
    {"JSONResponse", "PlainTextResponse", "HTMLResponse", "RedirectResponse", "Response", "jsonable_encoder"}
)

#: Container-mutation methods that park exception text where a later
#: ``return`` can pick it up without ever naming the exception.
_MUTATORS = frozenset({"append", "extend", "update", "add"})


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _enclosing_scope(node: ast.AST, parents: dict[ast.AST, ast.AST], tree: ast.AST) -> ast.AST:
    """The innermost function containing ``node`` (the module as fallback)."""
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
    return tree


def _scope_bindings(scope: ast.AST, tree: ast.AST) -> set[str]:
    """``except ... as name`` bindings visible inside ``scope``.

    Scoped to the *enclosing function* rather than the whole module (which is
    what the HTTPException-detail scan uses) so that an unrelated handler's
    ``except Exception as exc`` elsewhere in a large module cannot make a
    parameter named ``exc`` — server.py's ``http_exception_handler``, whose
    ``content={"detail": exc.detail}`` is FastAPI's processed envelope at the
    original error status — look like raw exception text.
    """
    names: set[str] = set()
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        statements: list[ast.AST] = list(scope.body)
    else:
        statements = [tree]
    for statement in statements:
        for node in ast.walk(statement):
            if isinstance(node, ast.ExceptHandler) and node.name:
                type_node = node.type
                if isinstance(type_node, ast.Name) and type_node.id in _BODY_RESERVED:
                    continue
                if isinstance(type_node, ast.Tuple) and any(
                    isinstance(item, ast.Name) and item.id in _BODY_RESERVED
                    for item in type_node.elts
                ):
                    continue
                names.add(node.name)
    return names


def _unit_reasons(unit: ast.AST, names: set[str]) -> list[str]:
    """Why this body value carries exception text, or ``[]`` when clean.

    Deep checks — ``str(exc)``, an f-string, ``exc.args``-style access, a
    subprocess ``stderr`` — run over the whole value; a *bare* exception name
    counts only in an actual value position (the value itself, or a dict/list
    element), so ``isinstance(exc, ProviderError)`` inside a conditional never
    trips it.
    """
    reasons: set[str] = set()
    for child in ast.walk(unit):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == "str":
            if child.args and isinstance(child.args[0], ast.Name) and child.args[0].id in names:
                reasons.add("str(exception)")
            elif any(isinstance(arg, ast.Starred) for arg in child.args):
                reasons.add("str(*args)")
        elif isinstance(child, ast.JoinedStr):
            for item in ast.walk(child):
                if isinstance(item, ast.FormattedValue):
                    inner = item.value
                    if isinstance(inner, ast.Name) and inner.id in names:
                        reasons.add("f-string interpolates the handler's exception name")
                    elif (
                        isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Name)
                        and inner.func.id == "str"
                        and inner.args
                        and isinstance(inner.args[0], ast.Name)
                        and inner.args[0].id in names
                    ):
                        reasons.add("f-string interpolates str(exception)")
        elif isinstance(child, ast.Attribute):
            if child.attr == "stderr":
                # A failed script's stderr *is* a traceback, path and all.
                reasons.add("subprocess stderr (a traceback in the common case)")
            elif isinstance(child.value, ast.Name) and child.value.id in names:
                # exc.args / exc.with_traceback — attribute access on the
                # exception object itself is the raw message by another route.
                reasons.add("attribute access on the exception object")
        if isinstance(child, (ast.Dict, ast.List, ast.Set, ast.Tuple)):
            elements = child.values if isinstance(child, ast.Dict) else child.elts
            for element in elements:
                if isinstance(element, ast.Name) and element.id in names:
                    reasons.add("bare exception name as a dict/list value")
    if isinstance(unit, ast.Name) and unit.id in names:
        reasons.add("bare exception name as the value")
    return sorted(reasons)


def body_leaking_sites(path: Path) -> list[tuple[int, str, str]]:
    """``(lineno, reason, snippet)`` for every 200-body value carrying exc text.

    ``snippet`` is the whitespace-normalised source of the whole statement
    (return / assignment / mutation / constructor call), so an allowlist
    marker can pin the exact code it was granted for.
    """
    source = path.read_text()
    tree = ast.parse(source)
    parents = _parent_map(tree)
    scope_cache: dict[ast.AST, set[str]] = {}
    hits: list[tuple[int, str, str]] = []

    def record(statement: ast.AST, unit: ast.AST, prefix: str) -> None:
        scope = _enclosing_scope(unit, parents, tree)
        if scope not in scope_cache:
            scope_cache[scope] = _scope_bindings(scope, tree)
        # Not short-circuiting on an empty binding set: the stderr rule must
        # fire even in a function that never binds an exception name — a
        # script's stderr reaches the body on its own merits.
        names = scope_cache[scope]
        snippet = " ".join((ast.get_source_segment(source, statement) or "").split())
        for reason in _unit_reasons(unit, names):
            hits.append((statement.lineno, f"{prefix}{reason}", snippet))

    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and node.value is not None:
            record(node, node.value, "body=")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, (ast.Subscript, ast.Attribute)):
                    record(node, node.value, "write=")
                    break
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, (ast.Subscript, ast.Attribute)):
            record(node, node.value, "write=")
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            func = call.func
            if isinstance(func, ast.Attribute) and func.attr in _MUTATORS:
                for arg in call.args:
                    record(node, arg, "mutate=")
            elif isinstance(func, ast.Name) and func.id in _RESPONSE_CTORS:
                for arg in list(call.args) + [kw.value for kw in call.keywords]:
                    record(node, arg, f"{func.id}():")
    return sorted(set(hits))


def test_no_raw_exception_text_in_http_exception_detail() -> None:
    """Fail on any raw exception text reaching an HTTPException detail."""
    violations: list[str] = []
    for path in sorted(API_DIR.glob("*.py")):
        for lineno, reason, snippet in leaking_sites(path):
            if _exempt(path, snippet) is None:
                violations.append(f"{path.name}:{lineno}: {reason}")
    assert not violations, "raw exception text reaches a response:\n" + "\n".join(violations)


def test_no_raw_exception_text_in_200_body() -> None:
    """Fail on raw exception text reaching a 200-body value anywhere in ``zolai/api``.

    This is the desktop-surface defect: ``{"error": str(e)}`` (or a stored
    write / container mutation feeding one) inside an ordinary ``200`` —
    same leak as the HTTPException-detail scan, different envelope.
    """
    violations: list[str] = []
    for path in sorted(API_DIR.glob("*.py")):
        for lineno, reason, snippet in body_leaking_sites(path):
            if _exempt(path, snippet) is None:
                violations.append(f"{path.name}:{lineno}: {reason}")
    assert not violations, "raw exception text reaches a 200 body:\n" + "\n".join(violations)


def test_body_scan_catches_the_pre_change_pattern(tmp_path: Path) -> None:
    """Non-vacuity of the 200-body scan — it flags the code that shipped before the fix.

    The three forms are the ones actually found in ``desktop_router.py`` /
    ``pipeline.py`` (a dict-return, a stored write, a subprocess stderr); a
    scan that could not see these would pass vacuously on any source.
    """
    source = '''async def db_query(sql: str):
    """Routes a caller's SQL."""
    try:
        conn = get_db()
        cur = conn.cursor()
        return {"columns": [d[0] for d in cur.description], "count": 1}
    except Exception as e:
        return {"error": str(e)}

async def job_status(job_id: str):
    try:
        run_job(job_id)
    except Exception as e:
        jobs[job_id]["error"] = str(e)
    return {"job_id": job_id}

def run_script(name: str):
    r = subprocess.run([name], capture_output=True, text=True)
    if r.returncode != 0:
        return {"error": f"Script failed: {r.stderr[:500]}"}
    return {"ok": True}
'''
    scan_target = tmp_path / "synthetic_router.py"
    scan_target.write_text(source)
    hits = body_leaking_sites(scan_target)
    reasons = {reason for _, reason, _ in hits}
    assert len(hits) == 3, f"expected exactly the 3 planted leaks, got: {hits}"
    assert any(r.startswith("body=") and "str(exception)" in r for r in reasons), reasons
    assert any(r.startswith("write=") and "str(exception)" in r for r in reasons), reasons
    assert any("subprocess stderr" in r for r in reasons), reasons
    # and the exemption lookup must not rescue an unlisted file
    assert all(_exempt(scan_target, snippet) is None for _, _, snippet in hits)


def test_allowlist_entries_are_still_present() -> None:
    """Every exemption must still cover exactly one real violation.

    Both scans are consulted: an entry granted against an HTTPException
    ``detail`` must keep covering one detail violation, and one granted
    against a 200 body must keep covering one body violation — if the code
    behind it is fixed (or duplicated), the count leaves 1 and this fails,
    forcing the entry's re-review.
    """
    unused: list[str] = []
    for entry in ALLOWLIST:
        path = API_DIR / entry.path
        if not path.is_file():
            unused.append(f"{entry.path}: file no longer exists")
            continue
        matched = sorted({
            lineno
            for scan in (leaking_sites(path), body_leaking_sites(path))
            for lineno, _, snippet in scan
            if entry.marker in snippet
        })
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


# ── the desktop surface: 200 bodies converted in place ───────────────────


@pytest.fixture()
def desktop_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """``/desktop`` routes only — no database, no lifespan, no migrations.

    ``ZOLAI_API_AUTH`` is pinned to ``warn`` so this file still passes when
    another suite flips it: the desktop router's ``require_scope`` reads the
    mode per request, and warn is the default dual-accept posture.
    """
    monkeypatch.setenv("ZOLAI_API_AUTH", "warn")
    application = FastAPI()
    application.include_router(desktop_router)  # prefix already /desktop
    # Deliberately NOT a context manager: entering it would run the lifespan.
    return TestClient(application, raise_server_exceptions=False)


def _boom_get_db(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError(LEAKY)


@pytest.mark.parametrize(
    ("path", "operation", "message"),
    [
        (
            "/desktop/tables",
            "desktop.db_tables",
            "The table list could not be read. Retry in a few seconds.",
        ),
        (
            "/desktop/dict/stats",
            "desktop.dict_stats",
            "Dictionary statistics could not be read. Retry in a few seconds.",
        ),
        (
            "/desktop/bible/books",
            "desktop.bible_books",
            "The Bible book list could not be read. Retry in a few seconds.",
        ),
    ],
)
def test_desktop_200_error_hides_the_exception_but_logs_it(
    path: str,
    operation: str,
    message: str,
    desktop_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Converted handlers keep HTTP 200 and the single-key ``error`` shape."""
    monkeypatch.setattr("zolai.api.desktop_router.get_db", _boom_get_db)

    with caplog.at_level(logging.ERROR):
        response = desktop_client.get(path)

    # status and shape: the response is still 200 with exactly {"error": <text>}
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"] == message

    # the message carries no exception text, SQL, path or traceback
    text = response.text
    assert LEAKY not in text
    assert "SELECT" not in text
    assert "no such column" not in text
    assert "zolai.db" not in text
    assert "/srv/zolai" not in text
    assert "Traceback" not in text
    assert "RuntimeError" not in text

    # the operator keeps the whole truth: raw text + traceback + operation
    assert LEAKY in caplog.text
    assert "Traceback" in caplog.text
    assert operation in caplog.text


def test_desktop_script_failure_hides_the_exception_but_logs_it(
    desktop_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``run_script``'s generic handler — the shared path behind ~30 routes."""
    monkeypatch.setattr(
        "zolai.api.desktop_router.resolve_script", lambda name: Path("/tmp/fake.py")
    )

    def _raise(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(LEAKY)

    monkeypatch.setattr("zolai.api.desktop_router.subprocess.run", _raise)

    with caplog.at_level(logging.ERROR):
        response = desktop_client.get("/desktop/dict/non-zolai")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"] == (
        "The check_non_zolai.py script could not be run. Check the server log for details."
    )
    assert LEAKY not in response.text
    assert "Traceback" not in response.text

    assert LEAKY in caplog.text
    assert "Traceback" in caplog.text
    assert "desktop.run_script" in caplog.text
    # the script name reaches the log as a structured context field (never echoed)
    assert any(
        getattr(record, "script", None) == "check_non_zolai.py"
        for record in caplog.records
    )


def test_desktop_script_stderr_is_logged_not_returned(
    desktop_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed script's stderr *is* a traceback — it goes to the log whole."""
    monkeypatch.setattr(
        "zolai.api.desktop_router.resolve_script", lambda name: Path("/tmp/fake.py")
    )
    raw_stderr = (
        "Traceback (most recent call last):\n"
        '  File "/srv/zolai/scripts/check_non_zolai.py", line 4, in <module>\n'
        "sqlite3.OperationalError: no such table: dictionary\n"
    )
    monkeypatch.setattr(
        "zolai.api.desktop_router.subprocess.run",
        lambda *_a, **_k: types.SimpleNamespace(returncode=1, stderr=raw_stderr, stdout=""),
    )

    with caplog.at_level(logging.ERROR):
        response = desktop_client.get("/desktop/dict/non-zolai")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"] == (
        "The check_non_zolai.py script failed. Check the server log for details."
    )
    assert "Traceback" not in response.text
    assert "/srv/zolai" not in response.text
    assert "sqlite3" not in response.text

    assert raw_stderr in caplog.text
    assert "desktop.run_script" in caplog.text


# ── success payloads are byte-identical to the pre-change code ───────────
#
# The conversion touched only ``except`` branches; these three endpoints
# assert the exact pre-change JSON so any accidental success-path drift fails.


class _FakeCursor:
    """Canned rows for one endpoint — no database anywhere near the test."""

    def __init__(self, *, fetchall_seq: tuple = (), fetchone_seq: tuple = ()) -> None:
        self.description = None
        self._fetchall = list(fetchall_seq)
        self._fetchone = list(fetchone_seq)
        self._fa = 0
        self._fo = 0

    def execute(self, *_args: Any, **_kwargs: Any) -> _FakeCursor:
        return self

    def fetchall(self) -> list:
        rows = self._fetchall[self._fa] if self._fa < len(self._fetchall) else []
        self._fa += 1
        return rows

    def fetchone(self) -> Any:
        row = self._fetchone[self._fo]
        self._fo += 1
        return row

    def close(self) -> None:
        pass


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def close(self) -> None:
        pass


@pytest.mark.parametrize(
    ("path", "cursor", "expected"),
    [
        (
            "/desktop/tables",
            _FakeCursor(
                fetchall_seq=[(("dictionary",), ("bible_verses",))],
                fetchone_seq=[(10,), (10,)],
            ),
            {
                "tables": [
                    {"table": "dictionary", "rows": 10},
                    {"table": "bible_verses", "rows": 10},
                ],
                "total": 2,
            },
        ),
        (
            "/desktop/dict/stats",
            _FakeCursor(
                fetchall_seq=[(("noun", 60), ("verb", 40))],
                fetchone_seq=[(100,), (50,), (90,)],
            ),
            {
                "total": 100,
                "with_myanmar": 50,
                "with_english": 90,
                "pos_breakdown": {"noun": 60, "verb": 40},
            },
        ),
        (
            "/desktop/bible/books",
            _FakeCursor(
                fetchall_seq=[
                    (
                        ("GEN", "Genesis", 31, 1, 50),
                        ("EXO", "Exodus", 40, 1, 40),
                    )
                ],
            ),
            {
                "books": [
                    {
                        "abbr": "GEN",
                        "name": "Genesis",
                        "verses": 31,
                        "min_chapter": 1,
                        "max_chapter": 50,
                    },
                    {
                        "abbr": "EXO",
                        "name": "Exodus",
                        "verses": 40,
                        "min_chapter": 1,
                        "max_chapter": 40,
                    },
                ],
                "total": 2,
            },
        ),
    ],
)
def test_desktop_success_payloads_unchanged(
    path: str,
    cursor: _FakeCursor,
    expected: dict[str, Any],
    desktop_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact pre-change payloads — status, keys and values, not just "no error"."""
    monkeypatch.setattr(
        "zolai.api.desktop_router.get_db", lambda: _FakeConn(cursor)
    )
    response = desktop_client.get(path)
    assert response.status_code == 200
    assert response.json() == expected
