"""Public/member/admin/superadmin RBAC (P2) — roles, ``PUBLIC_ROUTES``, route classes.

Four roles. :func:`role_for` is the **one** resolver every route, middleware
check and tool tier consults, and it reads two different things depending on
what the record is:

- ``anonymous``   — no credential (or one that did not resolve)
- ``member``      — a credential without admin power
- ``admin``       — an account whose ``users.role`` is ``admin``, or a key
  granting ``apikey:manage`` / ``settings:write`` / ``user:manage``
- ``superadmin``  — an account whose ``users.role`` is ``superadmin``, or a key
  granting ``role:manage`` / ``*``

**Accounts are read from the row, keys from their scopes.** A session or OAuth
record carries the ``role`` field built from ``users.role``
(:func:`zolai.api.session_auth.record_from_user`), and that value is
authoritative for it — which is what makes "promote this account" mean
something. A raw API key has no account behind it, so its role is the widest
scope it holds. ``user:manage`` deliberately tops out at ``admin``: managing
accounts is not the same power as granting roles.

Granting a role is rank-ordered and guarded in exactly one writer,
:func:`zolai.api.session_auth.set_user_role` — see
``docs/admin/permissions.md`` §5 for the invariants (no self-demotion, no
removing the last superadmin, nothing below superadmin may touch an admin).
The first superadmin cannot come from the HTTP surface by construction; it is
made by ``zolai user set-role … superadmin``.

``PUBLIC_ROUTES`` is the **single source consulted by both**
:func:`zolai.api.auth_middleware.ApiKeyMiddleware` **and**
:func:`zolai.api.auth.require_scope` — that is what guarantees
``ZOLAI_API_AUTH=enforce`` never 401s the public dictionary/search/word reads
or ``POST /api/v1/assistant/chat``.

Route classes (also the declaration table the completeness guard walks):

- **public**  — anonymous OK, in *any* mode
- **member**  — needs a key (401 anon in enforce; dual-accept in warn)
- **admin**   — strict (401 anon in warn *and* enforce) + scope-gated
- **unclassified** — a bug: a new route joined the surface without declaring
  which class it belongs to (``tests/test_rbac_public_matrix.py`` fails).
"""

from __future__ import annotations

import os
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from . import auth

# ── Roles ────────────────────────────────────────────────────────────────────

ROLE_ANONYMOUS = "anonymous"
ROLE_MEMBER = "member"
ROLE_ADMIN = "admin"
ROLE_SUPERADMIN = "superadmin"

#: Ordered least → greatest privilege.
ROLES: tuple[str, ...] = (ROLE_ANONYMOUS, ROLE_MEMBER, ROLE_ADMIN, ROLE_SUPERADMIN)
_RANK = {name: idx for idx, name in enumerate(ROLES)}

#: Scopes whose holder may grant, revoke and approve roles
#: (docs/admin/permissions.md §3).  ``*`` subsumes ``role:manage``, so a
#: wildcard key is a superadmin key — and both are refused at the minting
#: surface for anyone below superadmin.
SUPERADMIN_SCOPES: frozenset[str] = frozenset({"*", "role:manage"})

#: Scopes whose holder is an admin but **not** a superadmin
#: (docs/admin/permissions.md §3).  Account management, key minting and
#: provider settings stop here: none of them may change who is an admin.
ADMIN_SCOPES: frozenset[str] = frozenset(
    {"apikey:manage", "settings:write", "user:manage"}
)


def role_for(record: dict[str, Any] | None) -> str:
    """Role for a resolved credential record (``None`` ⇒ anonymous).

    Two sources of truth, in this order, because they describe two different
    kinds of credential:

    1. ``record["role"]`` — the ``users.role`` value carried by a session or
       OAuth record.  An account's role is what its row says; a promotion takes
       effect on the holder's next request, and a demotion likewise.
    2. The record's scopes — an API key has no account behind it, so the widest
       scope it holds *is* its role.

    An unrecognised explicit role is ignored rather than trusted: the scope
    reading then applies, and a record with no admin scope is a member.  Never
    raises — a hand-edited row must not 500 the API.
    """
    if not record:
        return ROLE_ANONYMOUS
    explicit = str(record.get("role") or "").strip().lower()
    if explicit in ROLES:
        return explicit
    scopes = set(record.get("scopes") or [])
    if scopes & SUPERADMIN_SCOPES:
        return ROLE_SUPERADMIN
    if scopes & ADMIN_SCOPES:
        return ROLE_ADMIN
    return ROLE_MEMBER


def rank_of(record: dict[str, Any] | None) -> int:
    """Privilege rank of a credential record (0 = anonymous)."""
    return _RANK[role_for(record)]


def has_role(record: dict[str, Any] | None, minimum: str) -> bool:
    """Whether ``record`` meets the ``minimum`` role rank."""
    return _RANK[role_for(record)] >= _RANK[minimum]


def person_identity(record: dict[str, Any] | None) -> tuple[int, str, str] | None:
    """``(user_id, username, role)`` when the credential is a **signed-in account**.

    ``None`` otherwise — for an anonymous caller, and, just as importantly, for
    a machine credential.  An API key has no account behind it (``user_id`` is
    ``None``), and an OAuth access token is held by a client process rather than
    the human who consented.  Routes that attribute a row to a person (the
    role-request queue, "my contributions") consult this one predicate and raise
    their own 403 sentence, so *what makes a person* is defined once while each
    surface keeps its own wording.
    """
    if not isinstance(record, dict) or not record:
        return None
    if str(record.get("auth_source") or "") != "session":
        return None
    user_id = record.get("user_id")
    if user_id is None:
        return None
    username = str(record.get("username") or record.get("name") or "")
    return int(user_id), username, role_for(record)


# ── Public routes ────────────────────────────────────────────────────────────
#
# Exact entries: ``(method, path)`` — method ``None`` = every method.


def _norm(method: str | None) -> str | None:
    return method.upper() if method else None


PUBLIC_ROUTES: tuple[tuple[str | None, str], ...] = (
    (None, "/health"),
    # ``(None, "/metrics")`` was removed 2026-10-21 (P0 ops-surface gate):
    # the Prometheus exposition dump listed every metric family, process
    # detail and route label for anonymous callers. ``/metrics`` now rides
    # ``OPS_ADMIN_PREFIXES`` below — admin tier, strict in warn *and*
    # enforce. ``/health`` stays public as the anonymous liveness probe.
    (None, "/api/v1/health"),
    # Identity probe — never 401s, so Studio can gate its own UI.
    ("GET", "/api/v1/auth/me"),
    # core#15 — the unversioned status echo (``serve_frontend`` in
    # ``zolai/api/server.py``). Declared rather than inherited: the middleware
    # gate is now default-deny, so an anonymous ``GET /`` would 401 under
    # ``enforce`` and take the MCP discovery host's root with it.
    ("GET", "/"),
    # Username + password sign-in/out. Public by necessity: a login cannot
    # require the credential it is asking for, so under ``enforce`` these two
    # must stay open or sign-in becomes impossible (chicken-and-egg). Both are
    # rate limited per IP **and** per username, and 404 when
    # ``ZOLAI_AUTH_SESSIONS=off``.
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
    # Public assistant (P4) — anonymous chat stays open under enforce.
    ("POST", "/api/v1/assistant/chat"),
    # Public assistant feedback (P4) — anonymous feedback stays open under enforce.
    ("POST", "/api/v1/assistant/feedback"),
    # MCP tool surface (stateless JSON-RPC). Public like the assistant: an
    # anonymous client may list and call the retrieval-only tool set, and a
    # presented key *widens* that set via the caller's role — the tool scope is
    # derived server-side from the resolved key, never from a message field.
    # The stricter anonymous bucket is declared in PUBLIC_CHAT_PATHS below.
    # Both the versioned path and the bare alias the public MCP host
    # (mcp.zolai.space -> :8001/mcp) proxies are the same handler.
    ("POST", "/api/v1/mcp"),
    ("POST", "/mcp"),
    # The transport's method probes are public too: a client asks ``GET`` to
    # learn whether this is a streamable-HTTP server, and it must get the
    # binding's ``405`` rather than an ``enforce``-mode ``401`` that hides the
    # answer.  Neither route reads or writes anything.
    ("GET", "/api/v1/mcp"),
    ("DELETE", "/api/v1/mcp"),
    ("GET", "/mcp"),
    ("DELETE", "/mcp"),
    # RFC 9728 protected-resource metadata for the versioned MCP path.  Public
    # by definition: it is what a ``401 WWW-Authenticate: resource_metadata=…``
    # points a client at *before* it holds any token, so gating it would make
    # discovery circular.  Returns the issuer URL and audience — no data, no
    # credential, nothing writable.  The bare and ``/mcp``-relative siblings are
    # the same handler and fall outside this table because they are not
    # ``/api/v1``-prefixed (``test_every_api_v1_route_is_classified`` only walks
    # that prefix).
    ("GET", "/api/v1/mcp/.well-known/oauth-protected-resource"),
    # Anonymous Studio reads (plan §B table).
    ("GET", "/api/v1/search"),
    ("POST", "/api/v1/search"),
    ("POST", "/api/v1/rag"),
    # `GET /api/v1/foundation/stats` was removed from this table (core#18):
    # it dumps raw/staging/canonical counts, review pending/resolved and
    # `cost_summary` — ops telemetry, not a Studio read.  `classify_route`
    # consults PUBLIC_ROUTES *before* MEMBER_PREFIXES, so this one exact
    # entry (not the `/api/v1/foundation` prefix below) was the whole hole:
    # it served anonymous in every mode.  Falling through to the prefix makes
    # it member-class — 401 anonymous under `enforce`, dual-accept in
    # `warn`.  `VALID_ACTIONS` untouched; `role_for` unchanged.
    ("GET", "/api/v1/knowledge/version"),
    ("GET", "/api/v1/knowledge/statistics"),
    # Public provider catalog (Phase B) — enabled rows, zero secrets, so a
    # client can see what it may target before it asks for a chat/run.
    ("GET", "/api/v1/providers"),
    # Public contributions — anyone may submit dictionary improvements.
    ("POST", "/api/v1/contributions"),
    # Multi-agent learning, read side + the orchestrator itself. Learning is
    # a read/ask surface like /rag and /search, so it stays open; the *tuning*
    # side (create/update/delete/enable/disable/skill assignment) is admin and
    # declared in ADMIN_METHOD_ROUTES.
    ("GET", "/api/v1/learning/overview"),
    ("POST", "/api/v1/learning/run"),
    # Learning *tools* reads (zolai/api/learning_tools_router.py). Same shape as
    # /learning/overview: a read, nothing written. The multi-verb prefixes live
    # in PUBLIC_PREFIXES just below; the mutating halves of those same prefixes
    # are method-scoped admins in ADMIN_METHOD_ROUTES.
    ("POST", "/api/v1/learning/corpus/check"),  # per-verse quality probe
)

#: Prefix entries: ``(method, prefix)`` — ``/api/v1/word`` covers every
#: sub-resource (``/forms``, ``/evidence``, ``/related``, …).
PUBLIC_PREFIXES: tuple[tuple[str | None, str], ...] = (
    ("GET", "/api/v1/word"),
    ("GET", "/api/v1/lexicon"),
    ("POST", "/api/v1/analyze"),
    # Read-only views of the agent registry: /agents, /agents/{id},
    # /skills, /skills/{id}/agents. Only GET — the mutating verbs on these
    # same prefixes are admin (see ADMIN_METHOD_ROUTES).
    ("GET", "/api/v1/learning/agents"),
    ("GET", "/api/v1/learning/skills"),
    ("GET", "/api/v1/learning/assistants"),
    ("GET", "/api/v1/learning/bindings"),
    # Learning tools (zolai/api/learning_tools_router.py) — a read or a pure
    # computation over caller-supplied input, never a write:
    #
    #   /consensus/*  GET + POST — weighted tally, discussion analysis and
    #                   agent re-scoring are all in-memory arithmetic.
    #   /metrics/*    GET — the dashboard window, the metric catalog, raw
    #                   samples and the renderers (json / prometheus / csv /
    #                   influx-line / grafana), which return the string in the
    #                   response body and write nothing. POST /metrics/collect
    #                   (samples rows) and the InfluxDB push (a destination)
    #                   are admin.
    #   /corpus/*     GET — health, coverage stats and stored quality reports.
    #                   POST /corpus/expand (thousands of rows) and
    #                   POST /corpus/validate (kicks off a run and persists its
    #                   report) are admin.
    ("GET", "/api/v1/learning/consensus"),
    ("POST", "/api/v1/learning/consensus"),
    ("GET", "/api/v1/learning/metrics"),
    ("GET", "/api/v1/learning/corpus"),
    # core#15 — OAuth 2.1 for the MCP connector, which lives outside
    # ``/api/v1`` (``zolai/api/oauth_router.py``, mounted at the root). Under
    # the default-deny gate these would 401 anonymously in ``enforce`` and the
    # flow could never start: RFC 8414 §3 / RFC 9728 discovery must answer with
    # no credential, and ``POST /oauth/register`` (DCR), ``/oauth/authorize``
    # and ``/oauth/token`` are how a client *obtains* one.  ``/oauth`` covers the
    # consent page and its CSP-safe ``/oauth/authorize.css``; the
    # ``.well-known`` prefixes cover the bare and ``/mcp``-scoped discovery
    # documents.  Rate limited per IP like every other public path, never 401.
    (None, "/oauth"),
    (None, "/.well-known"),
    (None, "/mcp/.well-known"),
)

#: Declared authed — everything below needs a key (member class unless admin).
MEMBER_PREFIXES: tuple[str, ...] = (
    "/api/v1/agent",
    "/api/v1/assistant",
    "/api/v1/records",
    "/api/v1/audit",
    "/api/v1/review",
    "/api/v1/linguistics",
    "/api/v1/predictions",
    "/api/v1/foundation",
    "/api/v1/auth",
    # ``/desktop`` is a declared authed surface outside ``/api/v1``: 44 routes in
    # zolai/api/desktop_router.py + 7 in zolai/api/jsonl_router.py (51
    # ``(method, path)`` pairs). Every one of them is scope-gated at the route,
    # so ``member`` is the honest class — a key is required, and the finer
    # per-action split (dataset:read for reads, the stricter write action for the
    # full-corpus import/export) is enforced by ``require_scope`` on the handler
    # itself, not by this declaration table.
    #
    # This closes a real hole: the completeness guard in
    # tests/test_rbac_public_matrix.py only walks OpenAPI paths starting with
    # ``/api/v1``, so the entire ``/desktop`` prefix had never been declared and
    # classify_route() returned "unclassified" for all 51 of them.
    "/desktop",
    # ``/api/v1/bible/*`` is the **versioned alias** of ``/desktop/bible/*``
    # (``zolai/api/bible_alias_router.py`` re-registers the same handler
    # callables behind the same ``dataset:read`` dependency). Declaring it here
    # is a bookkeeping statement, not a scope widening: the middleware already
    # gated every ``/api/v1`` path, the alias router already required
    # ``dataset:read``, and these 8 routes did not exist before this line.
    # Without a declaration ``classify_route`` returns ``unclassified`` and the
    # completeness guard in tests/test_rbac_public_matrix.py fails — "unclassified"
    # is documented above as a bug, not a fourth class.
    "/api/v1/bible",
    # ``/api/v1/role-requests`` — the ask/decide queue
    # (``zolai/api/role_requests_router.py``).  Declared **member**, never admin,
    # and that distinction is load-bearing: ``is_admin_path`` prefix-matches, so
    # an entry in ``ADMIN_PREFIXES`` or ``ADMIN_METHOD_ROUTES`` would 401 a
    # member on ``GET /role-requests/mine`` — their own list.  The two queue
    # routes are admin *at the route* (``role:manage`` +
    # ``require_role("superadmin", strict=True)``); this table records only the
    # "a credential is required" half, exactly as it does for ``/desktop``.
    "/api/v1/role-requests",
    # ``/api/v1/contributions`` is public for the **submit** half (the exact
    # ``("POST", …)`` entry in PUBLIC_ROUTES above, which ``classify_route``
    # consults before this list). The prefix is here for ``GET /mine`` — reading
    # back your own submissions needs a resolved account, so "a key is required"
    # is the honest class for the sub-resource.
    "/api/v1/contributions",
)

#: Strict admin surface (401 anon in warn *and* enforce + scope-gated).
#:
#: ``/api/v1/learning/ws`` is here even though it carries no HTTP methods:
#: ``ApiKeyMiddleware`` returns early for every non-HTTP scope, so the socket
#: handshake never reaches this module — the router resolves the key itself and
#: requires the admin role (its handlers write to SQLite). Declaring the path
#: keeps ``classify_route`` honest for anything that ever adds an HTTP sibling.
ADMIN_PREFIXES: tuple[str, ...] = ("/api/v1/admin", "/api/v1/learning/ws")

#: Ops / API-surface paths gated admin at the **middleware**.  They sit outside
#: ``/api/v1``, so before core#15 ``is_protected_path`` had never covered them
#: and every one of these answered anonymous HTTP 200 on production when this
#: gate was added, 2026-10-21.  Default-deny now covers them too, but the
#: branch stays: this surface must 401 for anonymous in **warn** as well, which
#: the dual-accept below would not do:
#:
#: - ``/metrics``      — the Prometheus exposition dump (every metric
#:                       family, process info, per-route labels).
#: - ``/api/metrics``  — the JSON ops views (summary, health, eval runs,
#:                       alerts, build info, annotations CRUD).
#: - ``/review``       — the review-queue HTML UI (``zolai/ui/routes.py``):
#:                       pending knowledge rows before promotion.
#: - ``/docs`` / ``/redoc`` / ``/openapi.json`` — FastAPI's explorer. The
#:                       OpenAPI schema is the full route inventory with
#:                       request/response shapes: reconnaissance value, so
#:                       it is admin by default; :func:`dev_docs_open`
#:                       (``ZOLAI_DEV_DOCS``, **default off**) re-opens
#:                       *only* these three for local development.
#:
#: Enforcement lives in ``ApiKeyMiddleware`` (strict admin: 401 anon in
#: warn *and* enforce, 403 for a resolved non-admin, only
#: ``ZOLAI_API_AUTH=off`` bypasses) — the same semantics as
#: ``require_role(ROLE_ADMIN, strict=True)``, applied one layer earlier
#: because these routes carry no route-level dependency to hang it on.
#: ``/metrics``, ``/api/metrics`` and ``/review`` are **not** re-opened by
#: ``ZOLAI_DEV_DOCS``; Prometheus scrapes must present an admin key.
OPS_ADMIN_PREFIXES: tuple[str, ...] = (
    "/metrics",
    "/api/metrics",
    "/review",
    "/docs",
    "/redoc",
    "/openapi.json",
)

#: The explorer subset of :data:`OPS_ADMIN_PREFIXES` that the
#: ``ZOLAI_DEV_DOCS`` escape hatch may re-open. Prefix-matched, so
#: ``/docs/oauth2-redirect`` rides along with ``/docs``.
DEV_DOCS_PATHS: tuple[str, ...] = ("/docs", "/redoc", "/openapi.json")

#: Method-scoped strict-admin entries, matched before ADMIN_PREFIXES.
#: Needed where one prefix carries both public reads and admin mutations —
#: ``/api/v1/learning`` serves the learning surface publicly while letting only
#: an admin retune the agents behind it or write to the learning tools' tables.
ADMIN_METHOD_ROUTES: tuple[tuple[str | None, str], ...] = (
    ("POST", "/api/v1/learning/agents"),    # create + enable + disable
    ("PATCH", "/api/v1/learning/agents"),
    ("DELETE", "/api/v1/learning/agents"),
    ("POST", "/api/v1/learning/skills"),    # create skill
    ("POST", "/api/v1/learning/bindings"), # bind an agent to an assistant
    ("DELETE", "/api/v1/learning/bindings"),  # unbind
    # Learning tools — the write halves of the publicly-readable prefixes.
    ("POST", "/api/v1/learning/metrics/collect"),   # samples rows
    ("POST", "/api/v1/learning/metrics/export"),    # push to a destination
    ("POST", "/api/v1/learning/corpus/expand"),     # thousands of rows
    ("POST", "/api/v1/learning/corpus/validate"),   # runs + persists a report
    # Fine-tuning spends real compute, the registry decides what serves
    # traffic, and the stored datasets are raw feedback — admin for every verb,
    # reads included.
    ("GET", "/api/v1/learning/finetuning"),
    ("POST", "/api/v1/learning/finetuning"),
    ("PUT", "/api/v1/learning/finetuning"),
    ("PATCH", "/api/v1/learning/finetuning"),
    ("DELETE", "/api/v1/learning/finetuning"),
)

#: Paths under the public assistant that get the stricter anonymous chat bucket.
PUBLIC_CHAT_PATHS: tuple[str, ...] = (
    "/api/v1/assistant/chat",
    # MCP ``tools/call`` runs a real DB query per message, so anonymous callers
    # get the strict 10/min bucket, not 120/min — on both MCP paths.
    "/api/v1/mcp",
    "/mcp",
    # Learning runs are CPU/SQLite-heavy (pattern extraction, vocabulary
    # builds), so anonymous callers get the strict 10/min bucket, not 120/min.
    "/api/v1/learning/run",
)


def _prefix_match(path: str, prefix: str) -> bool:
    prefix = prefix.rstrip("/")
    return path == prefix or path.startswith(prefix + "/")


def is_public_path(method: str | None, path: str) -> bool:
    """True when ``(method, path)`` may be served without a key, in any mode.

    Both the middleware and ``require_scope`` consult this — it is the single
    definition of "anonymous is allowed here".
    """
    if not path:
        return False
    norm = _norm(method)
    for entry_method, entry in PUBLIC_ROUTES:
        if entry_method is None or entry_method == norm:
            if path == entry:
                return True
    for entry_method, entry in PUBLIC_PREFIXES:
        if entry_method is None or entry_method == norm:
            if _prefix_match(path, entry):
                return True
    return False


def is_public_chat_path(method: str | None, path: str) -> bool:
    """Whether this request gets the stricter anonymous chat bucket."""
    norm = _norm(method)
    for entry in PUBLIC_CHAT_PATHS:
        if norm in (None, "POST") and path == entry:
            return True
    return False


def dev_docs_open() -> bool:
    """Whether ``ZOLAI_DEV_DOCS`` re-opens the API explorer (default **off**).

    Values ``1``/``true``/``on``/``yes`` (case-insensitive) enable it; any
    other value, unset or blank keeps ``/docs``, ``/redoc`` and
    ``/openapi.json`` admin-gated. Read live at call time (same pattern as
    ``auth.api_auth_mode``) so a dev can export it per-shell. The hatch
    never applies to ``/metrics``, ``/api/metrics`` or ``/review``.
    """
    raw = os.environ.get("ZOLAI_DEV_DOCS", "")
    return raw.strip().lower() in {"1", "true", "on", "yes"}


def is_ops_admin_path(path: str) -> bool:
    """True when ``path`` sits on the middleware-gated ops surface.

    Consulted by :class:`zolai.api.auth_middleware.ApiKeyMiddleware`. The
    ``ZOLAI_DEV_DOCS`` escape hatch (default off) is applied **here**, so
    every caller of this predicate sees the effective decision — with the
    hatch on, the explorer trio reports ``False`` (served like an ordinary
    unlisted route) while ``/metrics``, ``/api/metrics`` and ``/review``
    stay ``True``.
    """
    if not path:
        return False
    if dev_docs_open() and any(_prefix_match(path, p) for p in DEV_DOCS_PATHS):
        return False
    return any(_prefix_match(path, p) for p in OPS_ADMIN_PREFIXES)


def is_admin_path(path: str, method: str | None = None) -> bool:
    """True for the strict admin surface.

    ``method``-scoped entries in ADMIN_METHOD_ROUTES are checked first, so a
    prefix can be public for reads and admin for writes.
    """
    norm = _norm(method)
    for entry_method, entry in ADMIN_METHOD_ROUTES:
        if entry_method is None or entry_method == norm:
            if _prefix_match(path, entry):
                return True
    return any(path == p or path.startswith(p + "/") for p in ADMIN_PREFIXES)


def classify_route(method: str | None, path: str) -> str:
    """Route class: ``admin`` | ``public`` | ``member`` | ``unclassified``."""
    if is_admin_path(path, method):
        return "admin"
    if is_public_path(method, path):
        return "public"
    if any(path == p or path.startswith(p + "/") for p in MEMBER_PREFIXES):
        return "member"
    return "unclassified"


# ── require_role ─────────────────────────────────────────────────────────────


def require_role(minimum: str, *, strict: bool = False) -> Callable[..., dict[str, Any]]:
    """Route dependency enforcing a minimum role.

    ``strict=True`` (admin/agent surfaces): an anonymous caller is **401** in
    both ``warn`` and ``enforce`` — only ``ZOLAI_API_AUTH=off`` bypasses.
    ``strict=False`` (member surfaces): 401 in ``enforce``, dual-accept in
    ``warn`` (zero regression on today's posture).
    """
    if minimum not in _RANK:
        raise ValueError(f"unknown role {minimum!r}; expected one of {ROLES}")

    def dependency(request: Request) -> dict[str, Any]:
        # Public routes are public for everyone — role checks never apply.
        if is_public_path(request.method, request.url.path):
            return {}
        record: dict[str, Any] | None = getattr(request.state, "api_key", None)
        role = role_for(record)
        if role == ROLE_ANONYMOUS:
            mode = auth.api_auth_mode()
            if mode == "enforce" or (strict and mode != "off"):
                reason = getattr(request.state, "api_key_error", None) or "missing_api_key"
                raise HTTPException(
                    status_code=401,
                    detail={"error": "unauthorized", "reason": reason},
                )
            return {}
        if not has_role(record, minimum):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "forbidden",
                    "required_role": minimum,
                    "role": role,
                    "reason": "insufficient_role",
                },
            )
        return record

    return dependency


# ── GET /api/v1/auth/me ──────────────────────────────────────────────────────

auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@auth_router.get("/me")
def auth_me(request: Request) -> dict[str, Any]:
    """Who am I?  Public, never 401 — powers Studio role gating.

    The original four keys (``role``, ``key_prefix``, ``scopes``, ``mode``) are
    unchanged — additive only, and ``role`` gains a fourth value
    (``superadmin``) rather than replacing any existing one.  A Studio build
    that predates it reads an unknown role as its least-privileged answer, so
    clients widen with the enum, they do not guess.  When the caller
    authenticated with a username session (``Authorization: Bearer zolai_ss_*``)
    the response also carries ``auth_source="session"`` with ``username`` /
    ``display_name`` / ``user_id`` / ``expires_at``; ``key_prefix`` stays
    ``None`` because a session has no key prefix (D7).  With
    ``ZOLAI_AUTH_SESSIONS=off`` the response is the original four keys plus
    ``auth_source`` set to ``"api_key"``/``"anonymous"``.

    ``auth_source`` reports **the credential that was actually resolved**, so it
    takes four values: ``anonymous``, ``api_key``, ``session`` and ``oauth`` (an
    MCP access token, ``zolai_at_*``).  Saying ``api_key`` for the fourth was a
    small lie that Studio then had to design around — "which of my identity keys
    should I expect?" cannot be answered from a label that merges a person's
    OAuth grant with a machine's key.  An ``api_keys`` record carries no marker
    of its own (only sessions and grants do), so an *absent* marker means a key,
    not an unknown source.
    """
    record: dict[str, Any] | None = getattr(request.state, "api_key", None)
    is_session = bool(record) and record.get("auth_source") == "session"
    declared = str((record or {}).get("auth_source") or "")
    body: dict[str, Any] = {
        "role": role_for(record),
        "key_prefix": (record or {}).get("key_prefix"),
        "scopes": list((record or {}).get("scopes") or []),
        "mode": auth.api_auth_mode(),
        "auth_source": declared or ("api_key" if record else "anonymous"),
    }
    if is_session:
        body["username"] = record.get("username")
        body["display_name"] = record.get("display_name")
        body["user_id"] = record.get("user_id")
        body["expires_at"] = record.get("expires_at")
    return body
