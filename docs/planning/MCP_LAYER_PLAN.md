# PLAN: MCP Layer inside `zolai-core` + Core Contract Hardening — PLAN_READY

- **Status:** SURFACE SHIPPED & CUT OVER (2026-10-18) — the transport, the one-canonical-definition
  registry, the cross-client handshake and the typed failure taxonomy are implemented, deployed
  (core `88ce003` on pcore-server) and serving: `mcp.zolai.space` and `api.zolai.space/mcp` are both
  answered by zolai-core, and `scripts/verify-mcp-cutover.sh` reports **14 PASS · 0 FAIL · 0
  PENDING**. The wave list below is retained as the design record; a wave with no evidence in the
  repository or in that verifier's output is still open.
- **Repo:** `zolai-core` (single repo; Python 3.14, FastAPI, ruff, pytest)
- **Date:** 2026-10-18
- **Supersedes:** the earlier `zolai-mcp-server` (Cloudflare Worker) rewrite plan — **cancelled, out of scope**
- **Target URL:** `https://api.zolai.space/mcp`
- **Ground truth:** `.pcore/mcp-rewrite-context.md` (founder decisions + ~20 verified source facts). This plan does not re-derive or contradict them.
- **Governing docs:** `docs/planning/BACKEND_CORE_V1_PLAN.md`, `docs/planning/AI_AGENTS_RBAC_PLAN.md`, `docs/architecture/api-design.md` (ADR-014), `docs/admin/permissions.md`, `docs/operations/DEPLOY_RUNBOOK.md`

---

## 1. Goal / non-goals

### Goal

Add a **separate MCP (Model Context Protocol) layer inside `zolai-core`** — a second adapter over the
*same in-process services and the same SQLite DB* as the existing REST `/api/v1` surface, mounted at
`/mcp` beside it — plus the core contract work it depends on:

1. **Typed contract** — replace every `request: dict` request body with a real Pydantic model, so every
   capability publishes a generatable JSON Schema and an LLM can call it without guessing.
2. **Missing versioned routes** — add `/api/v1/bible/*` and mount the dead `prediction_api`
   (`/predictions/next`, `/completions`, `/corrections`, `/health`).
3. **MCP manifest** — `GET /api/v1/mcp-manifest`, the shared, machine-readable tool contract.
4. **MCP layer** — dispatch, auth, rate limiting, health, error mapping, `outputSchema` /
   `structuredContent`.
5. **Auth** — static token first, OAuth 2.1 (resource server + minimal authorization server) after.
6. **Gates** — tests, a manifest↔`tools/list` drift guard, docs, deploy runbook, deploy + verify matrix.

### Non-goals (explicit)

- ❌ **No changes to `zolai-mcp-server`.** It is a Cloudflare Worker and is **kept as-is for now**. That
  repo is read-only reference at most. Zero commits in this effort touch it.
- ❌ **No behaviour change to any existing REST route.** No status-code change, no response-body shape
  change, no re-ordering that can shadow a path, no change to `PUBLIC_ROUTES` semantics for existing
  entries. W1's Pydantic swap must be byte-for-byte behaviour-identical.
- ❌ **No DB migration in early waves.** W0–W3 add **zero** tables. The first tables arrive in W5/W6
  (OAuth + static-token storage) and are additive `CREATE TABLE IF NOT EXISTS` only — no `DROP`,
  no `RENAME`, no change to an existing column.
- ❌ No new infrastructure service. Net new containers stays **0** (the project rule). No Redis, no
  Durable Objects (zolai-core is FastAPI on one VPS, not a Worker — see §7).
- ❌ No behaviour change to the LLM provider path, the agent orchestrator, or the assistant's honest
  `retrieval_only` fallback. MCP *calls* them; it never rewrites them.
- ❌ No mass fine-tuning, no new corpus ingestion, no POS-gold recruitment (speaker-dependent).
- ❌ No rewrite of `zolai-explorer`. It is not a consumer in this effort.

---

## 2. Verified baseline (read this before implementing)

Everything below was read from source on 2026-10-17/18. **Treat as ground truth.**

### 2.1 Route topology in `zolai/api/server.py` (1598 lines)

`create_app()` builds one `FastAPI(title="Zolai Toolkit API", version="1.0.0", lifespan=lifespan)`.
Route registration order inside `create_app()`:

| Line | What |
|------|------|
| 416–417 | `desktop_router` + `jsonl_router` + `ai_providers_router` appended via `app.router.routes.append(route)` (**manual, not `include_router`** — keeps their own prefixes) |
| 422–468 | 20+ `app.include_router(...)` calls — all `/api/v1/*` except `providers_router`, `ui_router`, `metrics_router` |
| **474** | `@app.get("/")` → `serve_frontend` (desktop app HTML) |
| 486–497 | 4 `app.add_middleware(...)`: `CORSMiddleware`, `ApiKeyMiddleware`, `MetricsMiddleware`, `SecurityHeadersMiddleware` |
| 502+ | exception handlers |
| 1314+ | legacy `@app.get("/learning/search/*")` GET routes |
| **1573** | `@app.get("/{path:path}")` → `serve_static` — **the catch-all, registered LAST**, `include_in_schema=False`; serves static files and raises a genuine `HTTPException(404)` |
| 1598 | `app = create_app()` |

**Three consequences for this plan (all verified empirically — see §2.5):**

1. The `lifespan` is a **closure defined inside `create_app()`** at line 371. That is the only place
   `mcp.session_manager.run()` can be entered.
2. `Mount("/", …)` matches **every** path (regex `^/(?P<path>.*)$`), so if it is registered before
   `serve_static` the static-file handler becomes unreachable → **the desktop frontend breaks**.
3. `POST /mcp` must reach the MCP ASGI app *after* the catch-all has already been consulted. The
   catch-all is **GET-only**, so `POST` falls through — but `GET /mcp` does **not** (see §2.5).

### 2.2 Auth model — the MCP layer needs *none* of the existing auth plumbing

- `zolai/api/auth_middleware.py` gates **only** `path == "/api/v1"` or `startswith("/api/v1/")`.
  `EXEMPT_PATHS` = `/health`, `/metrics`, `/api/v1/health`.
- Therefore **`/mcp` and `/.well-known/*` are already outside the API-key gate.** MCP authenticates with
  its own `TokenVerifier` (MCP SDK) plus per-tool `require_scope`-equivalent checks. **Zero change to
  `ApiKeyMiddleware`, zero change to existing routes.** This is what makes the whole effort additive.
- `zolai/api/rbac.py`: `PUBLIC_ROUTES` (line 74), `PUBLIC_PREFIXES` (118), `PUBLIC_CHAT_PATHS` (199),
  `is_public_path` (212), `is_admin_path` (241), `classify_route` (255), `require_role` (269),
  `ADMIN_SCOPES` (45). Roles: anonymous < member < admin.
- Sessions `Bearer zolai_ss_*`; API keys `X-API-Key: zolai_sk_*`. Prod runs `ZOLAI_API_AUTH=enforce`.
- `zolai/api/rate_limit.py`: `SCOPE_LIMITS` (19), `DEFAULT_LIMIT=60`, `DEFAULT_WINDOW=60`,
  `InMemoryRateLimiter`, `RateLimitBucket`, `get_rate_limiter()` (222). `RateLimitMiddleware` exists
  but is **not** globally registered — MCP reuses `get_rate_limiter()` directly rather than adding a
  global middleware (that would alter existing route behaviour).

### 2.3 The three confirmed defects (W1's job)

- **DEFECT 1 — untyped bodies.** 11 endpoints take `request: dict` and hand-read keys:
  `/analyze/morphology`, `/analyze/phonology`, `/analyze/corpus`, `/translate/enhanced`,
  `/search/ranked`, `/search/cross-lingual`, `/search/topical`, `/grammar/validate-structure`,
  `/translation/disambiguate`, `/progress/adaptive-difficulty`, `/progress/error-categorize`.
- **DEFECT 2 — dead capability.** `comprehensive_analyzer.py`, `intelligent_analyzer.py`,
  `self_learning_engine.py` have 0 router references. `prediction_api.py` is **never imported** in
  `server.py`, so `/predictions/next`, `/completions`, `/corrections`, `/health` are dead
  (only `/api/v1/predictions/complete` from `word_engine_router` is live).
  `grammar_editor.py` *is* reachable (lazily imported inside `foundation_router.validate_structure`).
- **DEFECT 3 — no versioned Bible route.** No `/api/v1/bible/*` exists.

### 2.4 Verified request schemas (reuse these shapes, do not invent)

`rag_router.py`: `SearchRequest{query, limit=20, source_filter: list[str]|None}`;
`RAGRequest{question, limit=5, provider, model}`;
`AnalyzeSentenceRequest{text}`; `AnalyzeParagraphRequest{text}`; `AnalyzeWordRequest{word}`.

Records WHITELIST (`records_router.py`) — the only legal `table` + search fields:
`dictionary(zolai,english,myanmar,pos)` · `bible_verses(ref,book,zo_tdb77,en_kJV,myanmar)` ·
`grammar_patterns(pattern_id,pattern,description,function,examples)` · `phrases(zolai,english,examples)` ·
`vocabulary(headword,english,examples)` · `contributions(word,type,suggested_value,status,email)`.

Reusable in-process singletons already exist and are what routers call today:
`zolai/rag/retrieve.py` (`UnifiedRetriever`) · `zolai/learning/online_search.py` ·
`zolai/foundation/{analysis,morphology,phonology,corpus}.py` · `zolai/learning/{translation,progress,
grammar_editor,context_validator,sentence_validator,word_attestation}.py` ·
`zolai/knowledge/{ngram,retrieve,review,versioning}.py` · `zolai/pos_tagger/` · `zolai/syllable/` ·
`zolai/llm/adapter.py` · `zolai/agent/orchestrator.py`.

### 2.5 ⚠ Mount topology — empirically verified, do not re-derive

`mcp.streamable_http_app()` returns a Starlette app whose single route is `/mcp`. Measured against the
repo's real **starlette 1.7.0 / fastapi 0.142.2**, with a catch-all `Route("/{path:path}")` present:

| Topology | `POST /mcp` | `POST /mcp/` | `GET /mcp` | `GET /` | `GET /static/app.js` |
|---|---|---|---|---|---|
| **A** — `Mount("/", mcp_app)` registered **LAST** | **200** ✅ | 200 ✅ | **404 (shadowed by catch-all)** ❌ | 200 ✅ | 200 ✅ |
| **B** — `Mount("/mcp", mcp_app)` before catch-all | **405** ❌ | 200 ✅ (`root_path='/mcp'`) | 404 ❌ | 200 ✅ | 200 ✅ |

**DECISION: Topology A.** `app.mount("/", mcp_streamable_app)` as the **final** entry in
`app.router.routes`, preserving `POST /mcp` exactly (no trailing-slash redirect, no `root_path`
rewrite) with **zero** regression to any existing route. Trade-off accepted deliberately:
`GET`/`DELETE /mcp` are shadowed by the existing GET catch-all — `GET /mcp` is the SSE
server→client stream and is only needed by handshake-era (≤ `2025-11-25`) clients. Current MCP
protocol `2026-07-28` is stateless/sessionless by construction, so `POST` alone is sufficient.
A ~15-line forwarding shim for `GET`/`DELETE /mcp` lands in **W6** if a legacy client needs it.

### 2.6 MCP SDK facts that shape the code (verified against official docs/repo)

- `MCPServer` is the v2 name (`from mcp.server import MCPServer`). `@mcp.tool()` accepts
  `name=`, `description=`, `annotations=` (`read_only_hint`, `destructive_hint`, `idempotent_hint`,
  `open_world_hint`, `title`). `structured_output=True|False` is a documented kwarg.
- **Lifespan**: `streamable_http_app()` wires `session_manager.run()` into *its own* lifespan, and
  **a mounted sub-application's lifespan never runs**. The host app must enter
  `mcp.session_manager.run()`. Omitting it yields `RuntimeError: Task group is not initialized. Make
  sure to use run().` on the first request. → W0 must edit the `create_app()` lifespan closure.
- **DNS-rebinding protection**: with no `transport_security=`, the app accepts only
  `Host: localhost…` and answers **421 Misdirected Request** behind any real hostname — *before*
  anything MCP-shaped runs. Must pass
  `TransportSecuritySettings(allowed_hosts=[...], allowed_origins=[...])`.
- **Reverse proxy**: behind TLS-terminating nginx, uvicorn needs `--proxy-headers
  --forwarded-allow-ips='<proxy>'`, else the usual `/mcp` → `/mcp/` redirect is issued as `http://`
  and clients refuse to downgrade an HTTPS endpoint. → W7 runbook item.
- **Auth**: `token_verifier=` + `auth=AuthSettings(issuer_url, resource_server_url, required_scopes)`
  travel together (one without the other raises `ValueError`). The SDK then serves RFC 9728
  `/.well-known/oauth-protected-resource/...` and returns `401` with
  `WWW-Authenticate: Bearer … resource_metadata="…"`. `get_access_token()` gives the handler the
  `AccessToken` (with `client_id`, `scopes`, `subject`, `claims`) — **that is where per-tool scope
  enforcement reads the caller from.** The SDK is a *resource server only*: it will **not** issue
  tokens and does **not** publish `/.well-known/oauth-authorization-server`. W6 implements that.
- **Protocol eras**: modern `2026-07-28` (`server/discover`, sessionless) vs handshake-era
  `2025-11-25`-and-earlier (`initialize`, `Mcp-Session-Id`). Multi-worker sticky sessions are only a
  problem on the legacy leg. No Redis/DO required for v1 (prod runs `--workers 2`).
- **Structured output**: the return type annotation *is* the `outputSchema`; the SDK fills both
  `content` (text, for the model) and `structuredContent` (data, for the app) and **validates** the
  return value against the schema. A Pydantic `BaseModel` return yields the model itself;
  scalars/lists get a `{"result": …}` wrapper; `dict[str, …]` is already an object. A class with no
  body annotations silently yields **no** `outputSchema` → force `structured_output=True`.
- **MCP spec 2026-07-28**: `outputSchema` on the Tool and `structuredContent` on the result are both
  in the current spec (confirmed in `specification/2026-07-28/server/tools`). Servers SHOULD return
  `tools/list` in **deterministic order** so clients can cache it and so tool definitions stay stable
  in the LLM prompt cache → the registry iterates `sorted(TOOLS)`.

### 2.7 Registry-verified library versions (queried today; latest **stable**, no rc/beta)

| Package | Latest stable | `requires_python` | Notes |
|---|---|---|---|
| `mcp` (official SDK) | **2.3.0** | `>=3.10` | **CHOSEN.** Deps: `starlette>=0.48.0` (py≥3.14), `pydantic>=2.12.0`, `sse-starlette>=3.0.0`, `uvicorn>=0.31.1`, `pyjwt[crypto]>=2.10.1`, `python-multipart>=0.0.9`, **`httpx2>=2.10.0`**, `anyio>=4.9`. Declares Python 3.14. |
| `fastmcp` (PrefectHQ) | **4.1.0** | `>=3.10` | Rejected — depends on `fastmcp-slim==4.1.0`, **not** on `mcp`. A second, independent MCP implementation = two protocol stacks in one process. *(Alternative, one line.)* |
| `authlib` | 1.9.1 | `>=3.10` | Not used — see §7.2. |
| `idpyoidc` | 5.0.0 | — | Not used — see §7.2. |
| `pyoidc` | 0.1.0 | `>=3.8` | ⚠️ **PyPI summary: "Benign slopsquatting research package."** **Do not install. Do not recommend.** |
| `httpx` | 0.28.1 | `>=3.8` | Already in the tree for `zolai/llm/adapter.py`. |
| `pydantic` | 2.14.1 | `>=3.10` | Tree has 2.13.5 — satisfies `mcp`'s `>=2.12.0`. |

Repo today (measured in `.venv`): `starlette 1.7.0`, `fastapi 0.142.2`, `pydantic 2.13.5`,
`mcp` **not installed**.

> ⚠️ **Not yet verified — W0's first job:** `mcp` is **not installed** in `.venv`, so runtime
> compatibility with starlette 1.7.0 / Python 3.14 is **declared**, not **proven**. `mcp` also pulls
> **`httpx2`** (a different distribution from the `httpx` the LLM adapter uses) — W0 must confirm
> both resolve and that the full suite does not regress. If `httpx2` conflicts, that is a
> **founder gate**, not a silent workaround.

---

## 3. Architecture

### 3.1 The shape

```
                      ┌───────────────────────────────────────────────┐
                      │            FastAPI app (server.py)            │
 ChatGPT ─┐           │                                               │
 Claude  ─┤           │   /api/v1/*  ──► zolai/api/*_router.py        │
 Gemini  ─┼─ HTTP ───►│        (unchanged; FastAPI, Pydantic, HTTP)    │──┐
 Cursor  ─┤           │                                               │  │
 VS Code ─┘           │   /mcp      ──► zolai/mcp/  (MCPServer ASGI)   │  │
 Claude API          │        (NEW; Streamable HTTP, tools/call)      │  │
                      │                                               │  │
                      │   /api/v1/mcp-manifest   /oauth/* /.well-known│  │
                      └───────────────────────────────────────────────┘  │
                                                                           │
                     ┌─────────────────────────────────────────────────────┘
                     ▼
        ┌────────────────────────────────────────────────────────────────┐
        │  zolai/services/  — capability adapters (the ONE place a       │
        │  capability is shaped for a caller; result-shaping lives here)  │
        └───────────────┬────────────────────────────────┬───────────────┘
                        │                                │
        ┌───────────────▼──────────────┐   ┌─────────────▼──────────────┐
        │ in-process singletons       │   │ zolai/api/*_router.py       │
        │ rag.retrieve.UnifiedRetriever│   │ (calls the same services)  │
        │ foundation.{morphology,      │   └────────────────────────────┘
        │   phonology,corpus,analysis} │
        │ learning.{translation,       │
        │   progress,online_search,…}  │
        │ knowledge.ngram, llm.adapter │
        │ pos_tagger, syllable, agent  │
        └───────────────┬────────────────┘
                        ▼
              data/zolai.db  (SQLite, WAL, 116 tables)
```

**MCP is a second adapter, not a second service.** Same DB, same singletons, same code paths, no
HTTP hop, no duplicated query logic.

### 3.2 The central question: how a tool handler reaches core capability

Three options were considered. Decisive answer: **option 3 + a shared capability-adapter layer.**

#### Option A — Extract a shared service layer and re-point the REST routers at it

Pros: the textbook answer; one source of truth forever.
Cons: **it touches every router.** `create_app()` currently wires ~25 routers plus three
manual `app.router.routes.append()` groups, with three documented comments warning that routers must
be registered before the `@app.get("/{path:path}")` catch-all. Re-pointing them all in the same effort
that adds an MCP layer is exactly the blast radius that has already produced two production
CrashLoops (module-level static block; missing `email-validator`). It also directly violates the
non-goal *"no behaviour change to existing REST routes"*.

**Rejected** — correct architecture, wrong moment. Re-open as a separate wave once the MCP layer has
proved itself in production and the parity tests exist.

#### Option B — Call the existing router endpoint functions directly

```python
@mcp.tool()
async def word_detail(word: str): return await lexicon_router_word(word)   # ← rejected
```
Pros: literally zero new logic.
Cons: router functions have **HTTP-shaped signatures** — `Depends(...)` injections, `Request`
parameters, query-parameter defaults, `HTTPException` for control flow, `Cache-Control` headers, and
some are `async` while some are sync. Reusing them means every tool handler re-derives the HTTP model
it was trying to escape, cannot cleanly re-apply scope checks (the scope is enforced by a *router-level
dependency*, not by the function), and makes each tool untestable without constructing a `Request`.
This is "remove the HTTP hop but keep the HTTP abstraction" — the worst of both.

**Rejected.**

#### Option C — Call the in-process singletons the routers already call ✅

The decisive observation, from reading the routers: **the query logic is not in the routers.**
`create_app()`'s handlers are two-liners that resolve a body → call a `get_*()` singleton → return a
`dict`:

```python
search = get_online_search()
results = search.search_bible(query=query, limit=limit)
return {"results": results, "count": len(results)}
```

The actual querying lives in `zolai/rag/retrieve.py`, `zolai/foundation/*`, `zolai/learning/*`,
`zolai/knowledge/ngram.py`, `zolai/llm/adapter.py`. So a tool handler that calls
`get_unified_retriever()` directly **duplicates almost nothing** — there was never a second copy to
avoid. It also gets full DB access (founder decision #2: the Worker only ever saw a proxied subset),
no serialization round-trip, and no second auth hop.

The one thing genuinely worth sharing is **result shaping**: the records WHITELIST, cursor paging,
and the assistant's honest `retrieval_only` envelope. That is what `zolai/services/` is for.

#### Decision

> MCP tool handlers call the **same in-process service/engine singletons** the REST routers call.
> A new `zolai/services/` package holds **capability adapters** — thin functions that own the result
> shape **once** (e.g. `word_detail(word)`, `search_bible(query, limit)`, `run_rag(question)`,
> `browse_records(table, q, limit, cursor)`). Both REST and MCP *can* consume them; in this effort
> **only MCP does**, so no REST route changes. Making REST adopt them later is a **follow-up wave
> with its own parity tests** — explicitly not scheduled here.
>
> Cost of the deferral, stated honestly: `records_router`'s WHITELIST + paging is temporarily
> implemented twice (once in the router, once in `services/records.py`). W7 adds a **parity test**
> that asserts the two produce identical key sets, so the duplication can never silently diverge,
> and Option A can be closed later with confidence.

### 3.3 Module layout (real files)

```
zolai/mcp/
  __init__.py
  server.py         MCPServer singleton, name/instructions, build_server(), mount helper
  registry.py       ToolSpec dataclass + @tool() decorator; sorted, scope-tagged registry
  context.py        caller identity/scopes from mcp get_access_token(); anonymous fallback
  errors.py         ZolaiMcpError taxonomy -> MCP is_error payloads (stable codes)
  limits.py         per-scope/per-tool token buckets over zolai/api/rate_limit.py primitives
  manifest.py       builds the GET /api/v1/mcp-manifest payload from the registry
  models/
    __init__.py
    common.py       envelope + shared response models (Paged, SearchResult, WordDetail, ...)
  tools/
    __init__.py     imports every module so registration is side-effect-on-import
    language.py     W3  search/lexicon/word_detail/bilingual
    records.py      W3  records browse (+ WHITELIST reuse)
    morphology.py   W3  morphology/phonology/pos/syllable/grammar/zvs
    translation.py  W3  translate + streak + error breakdown
    rag.py          W4  rag.ask / analyze sentence|paragraph|word
    assistant.py    W4  assistant chat (honest retrieval_only)
    agent.py        W4  agent runs / providers / learning run
    docs.py         W5  doc registry (cached) + contributions
    admin.py        W5  review queue / audit / users / providers / api-keys
  auth/
    __init__.py
    verifier.py     TokenVerifier over SQLite (W2: static tokens; W6: OAuth access tokens)
    oauth.py        W6  minimal AS: register/authorize/token + RFC 8414 metadata
    storage.py      W6  additive tables + DAO (clients/codes/tokens/static tokens)

zolai/services/     (new, MCP-owned capability adapters — §3.2)
  __init__.py
  language.py  records.py  morphology.py  translation.py  rag.py
  assistant.py  agent.py  docs.py  admin.py

zolai/api/
  mcp_router.py       NEW: GET /api/v1/mcp-manifest + GET /api/v1/mcp/health (public)
  mcp_oauth_router.py NEW (W6): /.well-known/*, /oauth/register|authorize|token
```

### 3.4 Tool registry design (W0)

```python
# zolai/mcp/registry.py
@dataclass(frozen=True)
class ToolSpec:
    name: str                       # dotted, stable: "zolai.word.detail"
    scope: str                      # an action from zolai.api.auth.VALID_ACTIONS
    summary: str                    # one line; the model reads this
    read_only: bool = True
    destructive: bool = False
    idempotent: bool = True
    open_world: bool = False        # Zolai is a closed corpus -> False
    structured: bool = True         # pass structured_output=True
    output_model: type | None = None  # the Pydantic return type, for the manifest

TOOLS: dict[str, ToolSpec] = {}

def tool(spec: ToolSpec):
    """Register a ToolSpec and return the mcp.tool() decorator."""
    TOOLS[spec.name] = spec
    def deco(fn):
        return mcp.tool(
            name=spec.name,
            description=spec.summary,
            annotations=ToolAnnotations(
                title=spec.name.replace(".", " ").title(),
                read_only_hint=spec.read_only,
                destructive_hint=spec.destructive,
                idempotent_hint=spec.idempotent,
                open_world_hint=spec.open_world,
            ),
            structured_output=spec.structured,
        )(fn)
    return deco
```

Rules the implementer must hold to:

1. **Use only documented API.** `@mcp.tool(name=…, description=…, annotations=…, structured_output=…)`
   is documented. `MCPServer.add_tool()` is **not** in the docs this plan verified — do not call it.
   Registration is decorator-based against the `server.py` singleton.
   ⚠️ The `annotations=` **kwarg** is documented, but the **import path of its `ToolAnnotations`
   class was not** — the W0 implementer must confirm it from the installed package
   (`python -c "from mcp.types import ToolAnnotations"`) rather than trusting the sketch above.
2. **Deterministic order.** `tools/list` MUST be deterministic (spec 2026-07-28) so clients cache it
   and prompt caches stay warm → sort by `name` in the manifest and in the registration loop.
3. **Every tool declares a `scope`** drawn from the frozen `zolai.api.auth` action vocabulary (33
   actions, incl. `dataset:read`, `rag:read`, `catalog:read`, `agent:read`, `agent:run`,
  `dataset:edit`, `audit:read`, `apikey:manage`, `user:manage`, `settings:write`). **No new action
   is invented** — MCP scopes *are* zolai scopes. `tests/test_rbac_public_matrix.py` enforces this.
4. **Namespaced + stable**: `zolai.<area>.<verb>`. Renaming a tool breaks a cached client tool list,
   so names are frozen once shipped.
5. **Every tool declares its output model**, so the manifest and the drift guard have one source.

### 3.5 Error mapping

`zolai/mcp/errors.py` — one stable taxonomy, surfaced as `isError: true` with a machine code the model
can read and self-correct (the SDK turns a raised exception into `is_error=True` with
`Error executing tool <name>`; the detail lands in the server log, so **every expected failure must be
an explicit `ZolaiMcpError` carrying a `code` + a `hint`**, not a bare `raise`):

| Code | Raised when | Model-facing hint |
|---|---|---|
| `UNAUTHENTICATED` | no/invalid token | "Call with a Bearer token." |
| `INSUFFICIENT_SCOPE` | token lacks the tool's scope | names the missing action |
| `INVALID_ARGUMENT` | Pydantic rejected the arguments | echoes the constraint |
| `NOT_FOUND` | no such word / verse / table | "Try a shorter or differently spelled query." |
| `RATE_LIMITED` | bucket exhausted | includes `Retry-After` seconds |
| `AI_UNAVAILABLE` | no provider key / `retrieval_only` degradation | **must state it is corpus-only** |
| `TABLE_NOT_WHITELISTED` | `records` asked for a non-whitelisted table | lists the 6 legal tables |
| `INTERNAL` | anything unexpected | opaque; detail to the log only |

### 3.6 `GET /api/v1/mcp-manifest` — the shared tool contract

One public, secret-free, machine-readable document that is the **single source of truth** for: the
MCP tool registry, the manifest route, the drift guard, the docs, and the client setup guides.

```
GET /api/v1/mcp-manifest
  → 200 application/json, no auth required (adds zero secrets)
```

```jsonc
{
  "schema_version": "1.0",
  "generated_at": "2026-10-18T00:00:00Z",
  "server": {
    "name": "zolai-language",
    "title": "Zolai Language Toolkit",
    "protocol_versions": ["2026-07-28", "2025-11-25"],
    "mcp_endpoint": "https://api.zolai.space/mcp",
    "instructions": "…first 512 chars…",   // mirrors MCPServer(instructions=…)
    "language": "Tedim Zolai (ZVS 2018)"
  },
  "auth": {
    "schemes": ["static_bearer", "oauth2_authorization_code_pkce"],
    "public_tools": ["zolai.health"],
    "protected_resource_metadata": "https://api.zolai.space/.well-known/oauth-protected-resource/mcp",
    "authorization_server_metadata": "https://api.zolai.space/.well-known/oauth-authorization-server"
  },
  "scopes": { "dataset:read": "…", "rag:read": "…", "agent:run": "…" },
  "tools": [
    {
      "name": "zolai.word.detail",
      "title": "Zolai Word Detail",
      "summary": "…",
      "scope": "dataset:read",
      "annotations": { "read_only_hint": true, "open_world_hint": false },
      "input_schema": { "type": "object", "properties": { "word": {"type": "string"} },
                        "required": ["word"] },
      "output_schema": { "$ref": "#/$defs/WordDetail" },
      "rest_equivalent": { "method": "GET", "path": "/api/v1/lexicon/{word}" },
      "capability": "core" | "ai" | "write" | "admin"
    }
  ],
  "models": { "$defs": { "WordDetail": { "…": "…" } } }
}
```

Design rules:

- **Generated, never hand-written.** `manifest.py` walks `registry.TOOLS` and the live tool
  `FuncMetadata` schemas. It cannot drift from the code by construction.
- `rest_equivalent` on every tool — this is the drift guard's other half and the honesty link between
  the two adapters. A tool with no REST twin sets `null`.
- `capability` drives client UX: `ai` tools may honestly degrade (`retrieval_only`);
  `write`/`admin` tools carry `read_only_hint: false` so a client can prompt the user.
- **Public route.** Add `("GET", "/api/v1/mcp-manifest")` to `rbac.PUBLIC_ROUTES` and update
  `tests/test_rbac_public_matrix.py` in the same commit (that test fails if a public route has no
  class entry). `/api/v1/mcp/health` likewise.

### 3.7 Server metadata & `instructions`

- `MCPServer(name="zolai-language", instructions=INSTRUCTIONS)`. `INSTRUCTIONS` is a ≤512-char
  self-contained block (ChatGPT's own guidance: keep the first 512 characters self-contained) naming
  the corpus (Tedim Zolai, ZVS 2018), the SOV/ergative `in` rules, the honest-degradation contract
  (`ai` tools return `retrieval_only` when no provider key), and the recommended order
  (`search` → `word.detail` → `rag.ask`).
- `host=` / `port` are owned by uvicorn. **Do not pass `host=`** to `streamable_http_app()` — per the
  SDK docs it does not allowlist the hostname, it only disarms the localhost default.

---

## 4. Waves

Size: **S** ≈ 1 focused session · **M** ≈ 2–3 · **L** ≈ 4+. Every wave ends green and is
independently shippable. `tests/` never regresses below the verified baseline.

> **Baseline (from `.pcore/mcp-rewrite-context.md`):** `2240 passed / 14 failed (pre-existing) /
> 7 skipped / 1 xfailed / 6 collection errors`. The 14 failures + 6 collection errors are
> **pre-existing and must not increase**. Any wave that raises either count is a VERIFY_FAIL.

### W0 — Foundation: SDK, mount, lifespan, transport security, registry ⭑ S

**Goal:** a mountable, authenticated-in-waiting MCP server with one tool, proven to not disturb any
existing route.

Files:
- `pyproject.toml` — add `mcp==2.3.0` (pinned exactly; a float would break the tool contract).
  Add to `requirements.txt`. Confirm `httpx2` resolves (see §2.7 warning).
- `zolai/mcp/__init__.py`, `zolai/mcp/server.py`, `zolai/mcp/registry.py`, `zolai/mcp/context.py`,
  `zolai/mcp/errors.py`
- `zolai/api/server.py` — **two** edits only:
  1. inside the `create_app()` `lifespan` closure (line 371), wrap the existing body in
     `async with mcp.session_manager.run():` — the `session_manager` attribute only exists **after**
     `streamable_http_app()` has been called, so build the ASGI app at module import in
     `zolai/mcp/server.py` and import the *object* here, never the manager before the app exists.
  2. after the `@app.get("/{path:path}")` catch-all is defined (i.e. at the very end of
     `create_app()`, before `return app`), append `app.mount("/", _mcp_asgi_app)`.
- `zolai/services/__init__.py` — empty package marker.
- `tests/test_mcp_mount.py`

Build `transport_security` from env with a safe default:

```python
TransportSecuritySettings(
    allowed_hosts=["api.zolai.space", "api.zolai.space:*", "127.0.0.1", "127.0.0.1:*",
                   "localhost", "localhost:*"],
    allowed_origins=[o for o in get_cors_origins() if o.startswith("http")],
)
```
(If `ZOLAI_MCP_BEHIND_PROXY=1` — set in prod where nginx controls `Host` — pass
`enable_dns_rebinding_protection=False` instead, the honest configuration per the SDK docs.)

Tools: exactly one — `zolai.health` (public, read-only, `structured_output=True`).

**Done when (objectively checkable):**
- `.venv/bin/ruff check zolai tests` → 0.
- `pytest tests/test_mcp_mount.py` → all pass, and specifically assert: `POST /mcp` → 200;
  `GET /mcp` → 404 *(the accepted §2.5 trade-off, asserted so it can never change silently)*;
  `GET /` → 200; `GET /api/v1/health` → 200; `GET /api/v1/search?q=pasian` → 200;
  `GET /api/v1/lexicon/pasian` → 200; a previously-known 404 stays 404.
- `tools/list` over the in-memory `Client(mcp)` contains exactly `["zolai.health"]`.
- Full suite: failed ≤ 14 and collection errors ≤ 6 (i.e. **no new** failures).
- **Without** the lifespan edit, the test must fail with `Task group is not initialized` — i.e. the
  test is non-vacuous (assert this by temporarily commenting the lifespan block; if the suite still
  passes, the test is worthless).

**Risk:** the `mcp` package failing to install or import on Python 3.14 / starlette 1.7.0 (unproven
today); `httpx2` conflicting with the tree's `httpx`.
**Blocks:** every other wave.
**Rollback:** `git revert` the two commits; `pyproject.toml` pin removed; nothing else references the
package.

### W1 — Contract hardening: real request models + missing versioned routes ⭑ M

**Goal:** every capability MCP needs is typed and reachable on a versioned path. **Behaviour must be
identical.**

Files:
- `zolai/api/schemas.py` (**already exists** — extend it, do not create a parallel module) — add 11
  request models for the DEFECT-1 endpoints. Defaults must reproduce the current `request.get(...)`
  behaviour **exactly** (including the "key absent" behaviour), so a body that worked before still
  works and a body that errored before still errors.
- `zolai/api/foundation_router.py`, `zolai/api/learning_tools_router.py` (whichever own the other
  endpoints) — swap `request: dict` → `request: <Model>`.
- `zolai/api/bible_router.py` (**new**) — `GET /api/v1/bible/books`,
  `GET /api/v1/bible/chapters?book=`, `GET /api/v1/bible/verses?ref=&limit=&cursor=`.
  Read `bible_verses` directly through the existing repository/manager layer. Default scope
  `dataset:read` — matching `/desktop/bible/*`, so no new surface is opened. `/desktop/bible/*` stays
  as-is (no deprecation, no removal — non-goal).
- `zolai/api/predictions_router.py` (**new**) — mount `zolai/api/prediction_api.py`, restoring
  `/predictions/next`, `/predictions/completions`, `/predictions/corrections`, `/predictions/health`
  under `/api/v1`. Registered **before** the catch-all, with the same manual
  `app.router.routes.append(...)` treatment `prediction_api.py` needs if it carries its own prefix.
- `zolai/api/rag_router.py` — expose the verified `SearchRequest` / `RAGRequest` /
  `Analyze*Request` shapes for reuse.
- `tests/test_mcp_contracts.py` — for each of the 11 endpoints, an **A/B parity test**: the same
  request body returns an identical response body before and after the swap.

**Decision on the other dead analyzers** (`comprehensive_analyzer`, `intelligent_analyzer`,
`self_learning_engine`): **do not wire them up in this effort.** Exposing an unreviewed, unreferenced
analyzer as an MCP tool would publish an unverified contract to every LLM client. W1 instead adds
`docs/linguistics/ENGINE_FINDINGS.md` entries recording *why* each stays unreferenced (behaviour
change → needs its own wave). `grammar_editor` **is** exposed (it is already reachable via
`foundation_router.validate_structure`).

**Done when:**
- `grep -rn "request: dict" zolai/api/` → **0 hits**.
- `GET /api/v1/bible/books` → 200; `/api/v1/predictions/health` → 200; `/api/v1/predictions/next` → 200.
- Every changed endpoint has an A/B parity test: **11/11 pass**.
- `python -c "from zolai.api.server import app; print(len(app.openapi()['paths']))"` — record before
  and after; **no existing path is removed or altered** (the new paths are strictly additive).
- ruff 0; full suite: failed ≤ 14, collection errors ≤ 6.

**Risk:** a `request: dict` body that relied on extra/unknown keys silently stops validating →
caught by the A/B parity tests; a new router registered after the catch-all → caught by
`test_mcp_mount.py`'s named-route assertions.
**Rollback:** revert 3 commits. The bible/predictions routers are pure additions; the `dict`→model
swap is independently revertible.

### W2 — MCP core: dispatch, static-token auth, health, errors, limits ⭑ M

**Goal:** the layer is usable by a real client with a real key, before a single language tool exists.

Files:
- `zolai/mcp/auth/__init__.py`, `zolai/mcp/auth/verifier.py` — `TokenVerifier` implementation.
  **W2 scope: static tokens only.** Verify `sha256(token)` against the existing `api_keys` table via
  `zolai`'s own key service (the same source `ApiKeyMiddleware` uses — **no new table in W2**), so a
  `zolai_sk_*` API key presented as `Authorization: Bearer …` to `/mcp` authenticates and its
  `scope[]` becomes the `AccessToken.scopes`. Returns `AccessToken(subject=key_id, scopes=[...],
  resource=resource_server_url, claims={"kind": "api_key"})`, or `None`.
- `zolai/api/server.py` — pass `token_verifier=` **and** `auth=AuthSettings(...)` together
  (one without the other raises `ValueError`). `issuer_url` = `https://api.zolai.space`,
  `resource_server_url` = `https://api.zolai.space/mcp`, `required_scopes=[]` at first (per-tool
  enforcement does the real work; do not demand a scope every client lacks).
- `zolai/mcp/context.py` — `current_scopes()` / `require(tool_scope)`, reading `get_access_token()`.
  Falls back to the anonymous set when it returns `None`.
- `zolai/mcp/limits.py` — per-scope token buckets built on `zolai/api/rate_limit.py` primitives
  (`InMemoryRateLimiter`, `SCOPE_LIMITS`, `DEFAULT_WINDOW`). **Do not register
  `RateLimitMiddleware` globally** — that would alter existing routes (non-goal).
- `zolai/mcp/manifest.py` + `zolai/api/mcp_router.py` — `GET /api/v1/mcp-manifest`,
  `GET /api/v1/mcp/health`; both added to `rbac.PUBLIC_ROUTES` + `PUBLIC_PREFIXES` as required, with
  `tests/test_rbac_public_matrix.py` updated in the same commit.
- `tests/test_mcp_core.py`

**Done when:**
- `POST /mcp` with no `Authorization` → **401** + `WWW-Authenticate: Bearer … resource_metadata=…`.
- `GET /.well-known/oauth-protected-resource/mcp` → 200 with `resource`, `authorization_servers`,
  `scopes_supported`, `bearer_methods_supported: ["header"]`.
- `POST /mcp` with a valid `zolai_sk_*` bearer → 200 `tools/list` containing `zolai.health`.
- `POST /mcp` with an invalid token → 401. With a valid token lacking a tool's scope → the tool call
  returns `is_error: true` with code `INSUFFICIENT_SCOPE`.
- `GET /api/v1/mcp-manifest` → 200, lists every `registry.TOOLS` entry, and contains **no secret**
  (assert: no `zolai_sk_`, no `AI_BRAIN_`, no `X-API-Key` value).
- Rate limit: burst past the bucket → `RATE_LIMITED` with `Retry-After` in the error payload.
- Every W0 named-route assertion still holds. ruff 0; suite: failed ≤ 14.

**Risk:** `AuthSettings` misconfiguration locking out every client (mitigated: `required_scopes=[]`
first, and W2 is verifiable with plain `curl`); `PUBLIC_ROUTES` edit breaking the matrix test
(caught in-wave).
**Rollback:** remove the `token_verifier`/`auth` kwargs → the SDK serves `/mcp` unauthenticated;
revert the `PUBLIC_ROUTES` entries. `/mcp` remains inert.

### W3 — Language + learning tools ⭑ L (largest)

**Goal:** the read-only Zolai corpus is fully reachable over MCP. No AI, no writes.

Files: `zolai/services/{language,records,morphology,translation}.py`,
`zolai/mcp/models/common.py`, `zolai/mcp/tools/{language,records,morphology,translation}.py`.

Tools (name → scope → backing service):

| Tool | Scope | Backing |
|---|---|---|
| `zolai.search` | `rag:read` | `services/language.search` → `get_online_search()` + `UnifiedRetriever.search` |
| `zolai.bible.search` | `dataset:read` | `UnifiedRetriever.search(source_filter=["bible_verses"])` — the exact pattern `assistant_router` already uses (DEFECT 3 closed) |
| `zolai.lexicon.get` | `dataset:read` | dictionary + `dictionary_en_zo` |
| `zolai.word.detail` | `dataset:read` | **the flagship**: forms · contexts · collocations · patterns · evidence · related |
| `zolai.records.browse` | `dataset:read` | the **6-table WHITELIST** + cursor paging, verbatim |
| `zolai.morphology.analyze` | `dataset:read` | `foundation.morphology` (W1's typed body) |
| `zolai.phonology.analyze` | `dataset:read` | `foundation.phonology` — tone + all 19 sandhi rules |
| `zolai.pos.tag` | `dataset:read` | `pos_tagger` |
| `zolai.syllable.analyze` | `dataset:read` | `syllable` |
| `zolai.grammar.validate` | `dataset:read` | `foundation` grammar + ZVS (`zvs/`) |
| `zolai.zvs.validate` | `dataset:read` | `zvs_checker` — SOV, ergative `in`, forbidden forms |
| `zolai.translate` | `dataset:read` | `learning.translation` — ZO↔EN↔MY, 3-tier confidence |
| `zolai.progress.streak` | `dataset:read` | `learning.progress` |
| `zolai.progress.errors` | `dataset:read` | error breakdown |

Every tool: `async def` (I/O), a Pydantic **input model** as the single body parameter (the documented
"model as a parameter" form → a real nested `inputSchema`), a Pydantic **output model** as the return
annotation with `structured_output=True`, and a docstring written **for the model** — say what the
tool is for, when to prefer it over a sibling tool, and what an empty result means.

**Done when:**
- `tools/list` returns exactly these 14 names, **sorted**, each with a non-empty `inputSchema` and
  `outputSchema`, each carrying the correct `read_only_hint: true` and `open_world_hint: false`.
- Every tool has ≥2 contract tests: a happy path and a `NOT_FOUND`/empty path; each asserts
  `isError` semantics and the stable error code.
- `zolai.records.browse` rejects `table="users"` with `TABLE_NOT_WHITELISTED` naming the 6 legal
  tables, and accepts each of the 6.
- **ZVS gate:** a fixture containing `pathian` / `ram` / `fapa` is reported by
  `zolai.zvs.validate`; a ZVS-2018-correct sentence is not.
- `services/records.py` parity test vs `records_router` key sets: **passes**.
- ruff 0; suite: failed ≤ 14; `docs/architecture/api-design.md` gains an MCP section.

**Risk:** 14 tools in one wave is a lot of surface → mitigated by per-tool contract tests and by
splitting the wave into two commits (`language+records`, then `morphology+translation`).
**Rollback:** tools are registered by importing `zolai/mcp/tools/*`; removing a module import removes
its tools. No DB, no REST, no state.

### W4 — AI / RAG / assistant / agent tools ⭑ M

**Goal:** the intelligence surface, with **honest degradation**.

Files: `zolai/services/{rag,assistant,agent}.py`, `zolai/mcp/tools/{rag,assistant,agent}.py`.

| Tool | Scope | Notes |
|---|---|---|
| `zolai.rag.ask` | `rag:read` | `RAGRequest` shape; uses the server's own provider resolution |
| `zolai.analyze.sentence` | `rag:read` | `AnalyzeSentenceRequest` |
| `zolai.analyze.paragraph` | `rag:read` | `AnalyzeParagraphRequest` |
| `zolai.analyze.word` | `rag:read` | `AnalyzeWordRequest` |
| `zolai.assistant.chat` | public | reuses the **existing** assistant path, `retrieval_only` contract preserved verbatim |
| `zolai.agent.run` | `agent:run` | `agent_router`'s orchestrator; honours the 5/min bucket |
| `zolai.agent.runs` | `agent:read` | list recent `agent_runs` |
| `zolai.providers.list` | public | the zero-secret provider catalog |
| `zolai.learning.run` | `dataset:edit` | **write-class**: `read_only_hint: false` |

**Honesty contract (non-negotiable, mirrors `AI_AGENTS_RBAC_PLAN.md`):**
- When no provider key is configured, `rag.ask` / `assistant.chat` / `analyze.*` return
  `retrieval_only: true` **with citations**, and the tool description says so. Never invent an
  answer.
- `provider_error` codes (`PROVIDER_KEY_MISSING`, `PROVIDER_MODEL_UNKNOWN`, `NO_ACTIVE_PROVIDER`,
  `MODEL_NOT_CONFIGURED`) pass through unchanged — **no string matching, ever**.
- `zolai.providers.list` returns the same five fields as `GET /api/v1/providers` and **zero secrets**.

**Done when:**
- With `ZOLAI_ENGINE_MODE=rule` and no AI key, `zolai.rag.ask` → `isError: false`,
  `retrieval_only: true`, ≥1 citation.
- With a fake provider key + stub adapter, exactly one provider call is made per tool call.
- `zolai.assistant.chat` output is byte-identical to `POST /api/v1/assistant/chat` for the same input.
- `tools/list` adds exactly these 9 names; `zolai.learning.run` carries `read_only_hint: false`.
- An anonymous caller can call `zolai.providers.list` and `zolai.assistant.chat`, and **cannot**
  call `zolai.agent.run` (`INSUFFICIENT_SCOPE`).
- ruff 0; suite: failed ≤ 14.

**Risk:** provider cost. `agent.run` and `rag.ask` burn tokens → per-scope buckets (limits.py) +
`agent:run` gating + the existing circuit breaker. **Risk:** an MCP caller reaching the agent loop
bypassing `ZOLAI_ENGINE_MODE` → W4's tests must assert the mode gate is still enforced in-process.
**Rollback:** drop the three module imports from `zolai/mcp/tools/__init__.py`.

### W5 — Ecosystem docs + write + admin tools ⭑ M

**Goal:** documents (Zolai's most distinctive asset) plus the gated write surface.

Files: `zolai/services/{docs,admin}.py`, `zolai/mcp/tools/{docs,admin}.py`,
`zolai/api/contributions_router.py` (read-only touch).

| Tool | Scope | Class |
|---|---|---|
| `zolai.doc.search` | `dataset:read` | reads `articles` + `wiki_lessons` |
| `zolai.doc.get` | `dataset:read` | one document by id/slug |
| `zolai.contribution.submit` | **public** | `POST /api/v1/contributions` is already public — keep it public |
| `zolai.review.queue` | `dataset:edit` | write-class |
| `zolai.review.submit` | `dataset:edit` | write-class |
| `zolai.audit.query` | `audit:read` | `data_audit_log`, read-only |
| `zolai.admin.users` | `user:manage` | admin-class |
| `zolai.admin.providers` | `settings:write` | admin-class |
| `zolai.admin.api_keys` | `apikey:manage` | admin-class, **strict** (like `admin_api_keys_router`) |

**Doc registry caching (explicit requirement):** `zolai/services/docs.py` keeps a small
`functools.lru_cache`-style registry keyed by `(source, id)` with an explicit `updated_at` check,
bounded in size, and a `ZOLAI_MCP_DOC_CACHE_MAX` env cap. Cache is **process-local** — with
`--workers 2` each worker has its own, which is correct (a stale doc is bounded by the
`updated_at` re-check, never served stale). Documents are read-only, so a cold cache is always safe.

**Admin safety:** admin tools re-check the role from `get_access_token()` claims on **every** call;
`TokenVerifier` sets `claims={"role": …}` from the key record. `@mcp.tool()`'s
`destructive_hint`/`read_only_hint` are **hints, not security** — the plan states this in the docs
so no reviewer mistakes an annotation for a gate.

**Done when:**
- `tools/list` adds exactly these 9 names; the 5 admin/write tools carry `read_only_hint: false`.
- An **anonymous** token: `zolai.doc.get` and `zolai.contribution.submit` succeed;
  `zolai.review.queue`, `zolai.audit.query`, and all three `zolai.admin.*` return
  `INSUFFICIENT_SCOPE`.
- A **member** token: `zolai.audit.query` succeeds; `zolai.admin.*` still denied.
- An **admin** token: all three `zolai.admin.*` succeed; `api_keys` **never** returns a plaintext
  secret except on mint (shown once, never logged, never cached).
- Doc cache: two identical `zolai.doc.get` calls → the second is served from cache (assert via a
  call counter); a bumped `updated_at` invalidates it.
- `PRAGMA integrity_check` = `ok`; canonical table counts unchanged; ruff 0; suite: failed ≤ 14.

**Risk:** the first **DB writes** in this effort (`contributions`, `review_queue`). Both target
existing tables through existing services — **no schema change, no new table in W5**. Risk: an admin
tool leaks a secret → the mint-path test asserts no secret in the manifest, the logs, or a second call.
**Rollback:** remove two imports; `mcp_oauth_router` is not yet live so no persisted token state exists.

### W6 — OAuth 2.1 authorization server, token storage, structured output, client compat ⭑ L

**Goal:** the OAuth path so a ChatGPT connector can do per-user sign-in **and** Gemini Enterprise can
connect at all; plus the client-compatibility polish.

Files:
- `zolai/mcp/auth/storage.py` — **the first tables added by this effort.** Additive
  `CREATE TABLE IF NOT EXISTS` registered in `zolai/data/migrations.py` (idempotent; `CREATE TABLE
  IF NOT EXISTS`; **no `DROP`, no `RENAME`, no `ALTER` of an existing table**):
  `mcp_oauth_clients`, `mcp_oauth_codes`, `mcp_oauth_tokens`, `mcp_static_tokens`. All secrets
  stored as `sha256` hex only. Authorization codes: single-use (deleted on consume), 60 s TTL,
  bound to `client_id` + `redirect_uri` + `code_challenge`.
- `zolai/mcp/auth/verifier.py` — extend to accept **either** an OAuth access token **or** a static
  token, from the same table set.
- `zolai/mcp/auth/oauth.py` + `zolai/api/mcp_oauth_router.py` — the minimal authorization server:
  - `GET  /.well-known/oauth-authorization-server` (RFC 8414 metadata; `code_challenge_methods_supported: ["S256"]`)
  - `POST /oauth/register` (RFC 7591 DCR) → `201 {"client_id": …}`
  - `GET  /oauth/authorize` (authorization-code + PKCE; accepts the **existing** `zolai_ss_*`
    session so there is **no new password store**; `state` + PKCE verified; issues a one-time code)
  - `POST /oauth/token` (code exchange; `client_secret_post` **or** `none` — Claude registers with
    `token_endpoint_auth_method: "none"`, so treat DCR clients as **public**)
  - `GET  /.well-known/openid-configuration` (same doc, OIDC-shaped, for clients that look there)
  - Redirect allowlist **must** include `https://chatgpt.com/connector_platform_oauth_redirect`.
  - The `resource` parameter is echoed through the whole flow and bound into the token.
- `zolai/api/server.py` — register `mcp_oauth_router` **before** the catch-all. These paths are
  outside `/api/v1`, so `ApiKeyMiddleware` ignores them with **zero** middleware change.
- `zolai/mcp/server.py` — CORS: add `expose_headers=["Mcp-Session-Id"]` + the `Mcp-*` request headers,
  **guarded by `ZOLAI_MCP_CORS=enabled`** (default **off**) so existing browser behaviour is byte-identical
  until explicitly turned on.
- `GET`/`DELETE /mcp` forwarder shim (~15 lines) registered **before** the catch-all, so
  handshake-era clients work (§2.5).
- `docs/MCP_CLIENTS.md` — the per-client setup guide for ChatGPT / Claude / Gemini CLI / Gemini
  Enterprise / Cursor / VS Code / Claude Platform API, from the §5 matrix.
- `tests/test_mcp_auth.py`, `tests/test_mcp_oauth.py`

**Done when:**
- `GET /.well-known/oauth-authorization-server` → 200, `code_challenge_methods_supported` contains
  `S256`, `registration_endpoint` present.
- Full DCR + PKCE dance passes end-to-end in a test: register → authorize (with `resource`) →
  token → `POST /mcp` with the bearer → `tools/list` 200.
- An authorization code **cannot** be replayed; an expired one is refused; a wrong
  `code_challenge`/`redirect_uri` is refused; `resource` mismatch is refused.
- `/oauth/register` returns **no** client secret for a public client (`token_endpoint_auth_method: none`).
- A `zolai_sk_*` bearer still works on `/mcp` (back-compat with W2).
- **Zero** plaintext token/secret in the DB, the logs, or `/api/v1/mcp-manifest` (assert by grep over
  a populated DB).
- `PRAGMA integrity_check` = `ok`; **only** the 4 new tables added; every pre-existing table count
  unchanged; ruff 0; suite: failed ≤ 14.

**Risk (highest in the plan):** an auth bug locks every client out. Mitigated by: static-token auth
already shipped in W2 as the always-working path; OAuth is additive; the static path is never removed.
**Rollback:** drop `mcp_oauth_router` from `create_app()` and revert the verifier to W2's
API-key-only behaviour; the 4 tables are inert (no `DROP` — per the project's additive rule).

### W7 — Gates: drift guard, tests, docs, deploy runbook, deploy + verify ⭑ M

**Goal:** the layer cannot silently rot, and it is deployed and proven.

Files: `tests/test_mcp_drift.py`, `tests/test_mcp_mount.py` (extended), `docs/MCP.md`,
`docs/MCP_CLIENTS.md`, `docs/architecture/api-design.md`, `docs/planning/MCP_LAYER_PLAN.md`
(tick), `docs/operations/DEPLOY_RUNBOOK.md`, `docker-compose.prod.yml`, `Dockerfile.prod`,
`scripts/`, `.github/workflows/` if a CI job is added.

**The drift guard** (one test file, four assertions — this is the plan's most important test):

1. **Registry ↔ manifest**: every `registry.TOOLS` key appears in `GET /api/v1/mcp-manifest`, and
   every manifest tool exists in `registry.TOOLS`. (Bipartite equality, not "is a subset".)
2. **Manifest ↔ `tools/list`**: the manifest's tool names **equal** the live `tools/list` names,
   in the same sorted order.
3. **Scopes are real**: every `ToolSpec.scope` ∈ `zolai.api.auth.VALID_ACTIONS`; no tool carries a
   scope outside the frozen 33-action vocabulary.
4. **Schemas are present**: every tool has a non-empty `inputSchema` **and** `outputSchema`
   (`structured_output=True` forced), and every manifest `output_schema` resolves against the
   tool's declared Pydantic model.

Plus a **REST regression pin**: a frozen list of ~20 named routes asserted to keep their status
codes (`/health`, `/api/v1/health`, `GET /api/v1/search`, `POST /api/v1/search`, `POST /api/v1/rag`,
`GET /api/v1/lexicon/pasian`, `GET /api/v1/records?table=dictionary`, `GET /api/v1/audit`,
`GET /api/v1/providers`, `POST /api/v1/assistant/chat`, `GET /api/v1/auth/me`,
`GET /api/v1/foundation/stats`, `GET /`, and 401-on-anon for member-class routes). This is the
executable form of "no behaviour change to existing REST routes".

**Deploy + verify matrix** (extend `docs/operations/DEPLOY_RUNBOOK.md`, execute on pcore-server):

| # | Check | Expected |
|---|---|---|
| 1 | `POST https://api.zolai.space/mcp` (`initialize`/`tools/list`) with a consumer key | 200, tool list |
| 2 | Same, **no** token | 401 + `WWW-Authenticate` |
| 3 | `GET /.well-known/oauth-protected-resource/mcp` | 200 |
| 4 | `GET /.well-known/oauth-authorization-server` | 200, `S256` |
| 5 | `GET /api/v1/mcp-manifest` | 200, `N` tools, zero secrets |
| 6 | The **whole** W7 named-route regression pin, against the live origin | unchanged |
| 7 | `POST /api/v1/agent/runs` with a **member** key over MCP | `INSUFFICIENT_SCOPE` |
| 8 | nginx `--proxy-headers` in effect (no `http://` downgrade redirect) | confirmed |
| 9 | 20 sequential `tools/call`s from one token | 429 + `Retry-After` on #21 |
| 10 | `PRAGMA integrity_check` inside the container | `ok` |

**Rollback for deploy (this is the one that matters in production):**
`docker compose -f docker-compose.prod.yml up -d --build <previous-sha>` — the MCP layer is inert if
absent (`/mcp` → the existing catch-all 404) and the OAuth tables are inert if unpopulated. **No
rollback step touches the DB.**

**Done when:** drift guard 4/4 · REST pin green · full suite ≥ baseline with **no new** failures ·
ruff 0 · the 10-row matrix executed and recorded in `docs/operations/DEPLOY_RUNBOOK.md` ·
`context/progress-tracker.md` updated.

---

## 5. Client compatibility matrix

All verified from official client docs on 2026-10-18. **Wave** = the first wave at which that client
can actually connect.

| Client | Transport | Auth it supports | What it needs from us | Wave |
|---|---|---|---|---|
| **ChatGPT** (custom connector / plugin) | SSE **or** streamable HTTP | OAuth 2.1 (DCR), **static credentials**, or **no auth** | `POST https://api.zolai.space/mcp`; static credential works immediately; the OAuth path needs `resource` echoed, `S256` PKCE, DCR `registration_endpoint`, and `https://chatgpt.com/connector_platform_oauth_redirect` in the redirect allowlist | **W2** (static) · **W6** (OAuth) |
| **Claude.ai / Desktop / Code / mobile / Cowork** (custom connector) | streamable HTTP | `oauth_dcr` (default), `oauth_cimd`, `static_headers` (beta), no-auth | Exact endpoint URL; **Claude uses only the FIRST entry in `authorization_servers`**; DCR clients register with `token_endpoint_auth_method: "none"` → treat as public | **W2** (header) · **W6** (DCR) |
| **Claude Platform API** (Messages `mcp_connector`) | streamable HTTP or SSE | `authorization_token` bearer | Pass the token as `authorization_token`; beta `mcp-client-…` | **W2** |
| **Gemini CLI** (`~/.gemini/settings.json`) | streamable HTTP | header bearer | Field is **`httpUrl`**, not `url`; `mcpServers` wrapper key | **W2** |
| **Gemini Enterprise** (custom MCP server data store) | **streamable HTTP only** | **no auth, or OAuth 2.0 client-id + secret only** | ⚠️ **No custom-header option.** Gemini Enterprise cannot connect at W2/W5 at all — it needs the OAuth 2.1 AS | **W6** |
| **Cursor** (`.cursor/mcp.json`) | `type: "http"` | header bearer | `mcpServers` key, `url`, `headers` | **W2** |
| **VS Code / Copilot** (`.vscode/mcp.json`) | `type: "http"` | header bearer, **or automatic OAuth** | Wrapper key is **`servers`** (not `mcpServers`); with OAuth configured VS Code opens the browser itself | **W2** (header) · **W6** (auto OAuth) |

**Rollout order (why):** W2's static bearer unblocks **six of seven** targets the day it ships. Only
Gemini Enterprise is blocked, and only because it offers no header option — an honest, documented
limitation, not an oversight. W6 unblocks the seventh and upgrades ChatGPT/Claude/VS Code to
per-user sign-in.

---

## 6. Risks, per wave, with rollback

| Wave | Risk | Severity | Mitigation | Rollback |
|---|---|---|---|---|
| **W0** | `mcp` will not install/import on py3.14 + starlette 1.7.0 (**unproven today**) | 🔴 | First task of W0; `mcp==2.3.0` pinned; a clean-worktree install + full-suite run before any other W0 edit | revert 2 commits |
| **W0** | `httpx2` (new dist) conflicts with the tree's `httpx` | 🟠 | Same gate; **founder gate** if unresolvable — no silent workaround | revert |
| **W0** | `Mount("/", …)` breaks the static-file handler or the catch-all | 🔴 | Topology A measured in §2.5; `test_mcp_mount.py` pins `GET /`, `GET /static/…`, and a known 404 | revert |
| **W0** | Forgot `session_manager.run()` | 🟠 | Non-vacuous test: commenting the lifespan block **must** fail | revert |
| **W0** | 421 behind the real hostname (DNS-rebinding) | 🔴 | `TransportSecuritySettings` with `allowed_hosts`/`allowed_origins` from `get_cors_origins()`; asserted in test | revert |
| **W1** | `dict` → Pydantic rejects a body that used to work | 🟠 | 11 A/B parity tests reproducing the exact old defaults | revert 3 commits |
| **W1** | New router registered after the catch-all → invisible | 🟠 | Named-route assertions in `test_mcp_mount.py` | revert |
| **W1** | `/predictions/*` becomes public surface | 🟡 | `dataset:read` scope by default; document as a **surface change** in `api-design.md` | revert |
| **W2** | `AuthSettings` misconfig locks every client out | 🔴 | `required_scopes=[]` first; per-tool enforcement; plain-`curl` verification in-wave | drop the kwargs |
| **W2** | `PUBLIC_ROUTES` edit breaks `test_rbac_public_matrix.py` | 🟡 | Updated in the same commit | revert |
| **W3** | 14 tools is too much surface for one wave | 🟠 | Split into two commits; per-tool contract tests; tools removable by import | drop the import |
| **W3** | Records WHITELIST drifts from the router's | 🟠 | Parity test on key sets (W3 + W7) | revert |
| **W4** | Provider cost from anonymous MCP callers | 🟠 | Per-scope buckets; `rag:read`/`agent:run` gates; existing circuit breaker; `retrieval_only` honesty | drop the import |
| **W4** | MCP path bypasses `ZOLAI_ENGINE_MODE` | 🔴 | Assert the mode gate still holds in-process | revert |
| **W5** | First DB writes; a secret leaks from `api_keys` | 🔴 | No schema change; mint-path asserts no secret in manifest/logs/second call | drop the import |
| **W6** | Auth bug locks **everyone** out | 🔴 | Static-token path from W2 is never removed; OAuth strictly additive | unregister the router |
| **W6** | OAuth implementation is a security surface | 🔴 | Codes single-use + 60 s TTL + PKCE-verified; secrets `sha256`-only; `resource` bound; replay/expiry/mismatch tests | as above |
| **W6** | Browser clients blocked by CORS | 🟡 | `Mcp-Session-Id` exposure behind `ZOLAI_MCP_CORS=enabled` (default **off**) | flip the flag off |
| **W7** | The layer rots as REST changes | 🟠 | The 4-assertion drift guard + the REST regression pin | — |
| **W7** | HTTPS downgrade redirect behind nginx | 🟠 | `--proxy-headers --forwarded-allow-ips` in the runbook; matrix row 8 | revert the deploy |

---

## 7. Auth design

### 7.1 Dual scheme

| | **Static bearer token** | **OAuth 2.1 (authorization code + PKCE)** |
|---|---|---|
| Ships in | **W2** | **W6** |
| Mechanism | A `zolai_sk_*` API key presented as `Authorization: Bearer …`. Verified against the **existing** key table via the same service `ApiKeyMiddleware` uses. | MCP SDK resource server (`TokenVerifier` + `AuthSettings`, RFC 9728 metadata) + a **minimal in-repo authorization server** (RFC 8414 metadata, RFC 7591 DCR, PKCE `S256`). |
| Clients it serves | ChatGPT (static creds), Claude (static headers / Platform API), Gemini CLI, Cursor, VS Code | ChatGPT (DCR), Claude (DCR/CIMD), Gemini Enterprise, VS Code auto-OAuth |
| Storage | **none new** — the existing `api_keys` table | 4 new additive tables, secrets `sha256`-only |
| Admin control | Existing `zolai apikey create/revoke` CLI | `mcp_oauth_clients` rows + revocation |

**Both are always on.** A client uses whichever it supports; the SDK `TokenVerifier` accepts either,
so there is a single code path in `context.py`.

### 7.2 Why SQLite and not a Worker-style store

The founder's note that zolai-core is **FastAPI on one VPS, not a Worker** is the whole argument:

- **No Durable Objects exist.** They are a Cloudflare-Workers-only primitive. `zolai-mcp-server`
  could use one; `zolai-core` cannot.
- **In-memory is wrong**, and this is the decisive point: production runs **uvicorn `--workers 2`**
  (per `BACKEND_CORE_V1_PLAN.md` P5). Two worker processes, two Python heaps. An access token minted
  by worker A would be **unknown to worker B**, and a client whose request lands on B gets a
  spurious 401. A token store must be cross-process and must outlive a restart.
- **SQLite is already the answer.** `data/zolai.db` (WAL mode, `busy_timeout=30000`) is the project's
  canonical store, `data/` is the one persistent container volume, and the project rule is **net new
  containers 0–1**. Four additive `CREATE TABLE IF NOT EXISTS` tables fit that rule exactly, get WAL
  concurrency for free, get the existing backup script for free, and stay readable by `zolai db`.
- **Redis is rejected**: a new container, a new failure mode, a new backup target — for a handful of
  rows an admin creates a few times a year.
- **Rejected libraries.** No OAuth server library. `authlib` 1.9.1 and `idpyoidc` 5.0.0 are both
  capable, but a generic framework is a large, security-sensitive dependency for **four endpoints
  (`register`, `authorize`, `token`, metadata)** that MCP clients require to be exactly RFC-shaped.
  Hand-rolled here is ~200 lines of `secrets`/`hashlib`/SQLite that we can read end to end, and the
  test matrix in W6 covers replay, expiry, PKCE and `resource` binding — which is what actually makes
  it safe. ⚠️ And **`pyoidc` on PyPI is a slopsquatting research package — never install it.**

### 7.3 Per-tool authorization

`context.py` reads `get_access_token()` (the SDK stores it in a context var per request) and exposes
`current_scopes()`. Every tool declares a `ToolSpec.scope` from the frozen 33-action vocabulary, and
`registry.py`'s wrapper enforces it before the handler body runs. Anonymous (`get_access_token()` is
`None` outside an authenticated HTTP request, on `stdio`, and in the in-memory test client) falls back
to `rbac`'s anonymous tier.

Admin tools additionally check `claims["role"] == "admin"` on every call. **Annotations are hints,
never security** — stated in `docs/MCP.md` so no reviewer mistakes
`destructive_hint=False` for a gate.

---

## 8. Commit strategy

- **Conventional Commits**, on `main`, one concern per commit, in wave order.
- **Phase-gated:** wave *N+1* starts only after wave *N* is green (ruff 0, suite non-regressed,
  done-when all ticked) and committed. Never land two waves in one commit.
- One commit per logical change inside a wave; never `git add -A` (the working tree has been dirty
  with other sessions' WIP before — stage explicit paths).
- Commit messages must name the wave: `feat(mcp): …`, `feat(api): …`, `test(mcp): …`, `docs(mcp): …`.

| Wave | Commits |
|---|---|
| W0 | `chore(deps): pin mcp==2.3.0` · `feat(mcp): server, registry and mount` · `test(mcp): mount, lifespan and route regression pin` |
| W1 | `feat(api): typed request models for 11 untyped endpoints` · `test(api): request-body parity` · `feat(api): versioned /api/v1/bible routes` · `feat(api): mount prediction_api under /api/v1` · `docs(linguistics): record why three analyzers stay unreferenced` |
| W2 | `feat(mcp): static-token verifier and per-tool scope enforcement` · `feat(mcp): error taxonomy, limits and manifest` · `feat(api): /api/v1/mcp-manifest + /api/v1/mcp/health` · `test(mcp): core dispatch, auth and limits` |
| W3 | `feat(services): language, records and morphology adapters` · `feat(mcp): language + records tools` · `feat(mcp): morphology, grammar, ZVS + translation tools` · `test(mcp): W3 tool contracts` |
| W4 | `feat(services): rag, assistant and agent adapters` · `feat(mcp): rag, analyze, assistant, agent and provider tools` · `test(mcp): W4 honesty and mode-gate contracts` |
| W5 | `feat(services): cached doc registry and admin adapters` · `feat(mcp): doc, contribution, review, audit and admin tools` · `test(mcp): W5 scope matrix` |
| W6 | `feat(db): additive mcp_oauth_* tables` · `feat(mcp): OAuth 2.1 authorization server` · `feat(mcp): static tokens and GET/DELETE /mcp forwarder` · `feat(mcp): opt-in browser CORS` · `docs(mcp): client setup guides` · `test(mcp): DCR, PKCE, replay and expiry` |
| W7 | `test(mcp): manifest/tools-list drift guard` · `docs(mcp): MCP architecture` · `docs(api): MCP section in api-design` · `chore(deploy): MCP runbook + verify matrix` · `docs: tick this plan and update the tracker` |

---

## 9. Definition of done (whole plan)

- [ ] `GET https://api.zolai.space/mcp` (POST transport) serves `tools/list` with **every** manifest
      tool, deterministically sorted.
- [ ] `GET /api/v1/mcp-manifest` is generated from the registry, is public, and contains **zero**
      secrets.
- [ ] The 4-assertion drift guard is green.
- [ ] All 11 DEFECT-1 endpoints are typed; `grep -rn "request: dict" zolai/api/` → 0.
- [ ] `/api/v1/bible/*` and `/api/v1/predictions/{next,completions,corrections,health}` are live.
- [ ] `ruff check zolai tests` → 0.
- [ ] Full pytest: **failed ≤ 14** and **collection errors ≤ 6** (the verified pre-existing baseline)
      — i.e. no new failures anywhere.
- [ ] `PRAGMA integrity_check` = `ok`; only additive tables added.
- [ ] **Every** existing REST route behaves exactly as before — proven by the executable
      `test_mcp_mount.py` / W7 regression pin.
- [ ] ChatGPT, Claude, Gemini CLI, Cursor and VS Code connect with a static token; Gemini Enterprise
      connects via OAuth.
- [ ] `docs/MCP.md`, `docs/MCP_CLIENTS.md`, `docs/architecture/api-design.md` and the runbook's
      10-row verify matrix are written and executed.
- [ ] **Zero commits in `zolai-mcp-server`.**

---

## 10. Open questions for the founder (do not block W0–W2)

1. **`httpx2`** — `mcp` 2.3.0 depends on `httpx2>=2.10.0`, a different distribution from the `httpx`
   0.28.1 the LLM adapter uses. If the trees conflict in the container image, that is a
   **founder gate** (accept both / pin `mcp` lower / vendor). W0 must report before W1.
2. **`/api/v1/mcp-manifest` public vs gated** — this plan recommends **public** (it holds no secret and
   a client needs it before it has a token). Reversible in one line if the founder wants it gated.
3. **`--workers 2` + legacy clients** — the W6 `GET`/`DELETE /mcp` forwarder closes this. Confirm
   whether any target client is still on a handshake-era protocol (`≤ 2025-11-25`); if none is, the
   forwarder can be deferred past W7.
4. **ZVS 2018 as a hard MCP guard** — tools already validate against the forbidden forms. Confirm
   whether an MCP tool should *refuse* to return a non-ZVS Zolai string, or return it **flagged**
   (the current REST behaviour). This plan assumes **flag, never rewrite** — same as REST.

PLAN_READY
