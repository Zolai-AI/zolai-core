# Engine Findings — P2 Contract Harness

**Status:** FINDINGS (no behavior fixes in P2 — plan constraint) · **Date:** 2026-10-01
**Source:** `tests/test_engine_contract.py` (registry contract + mode matrix) and
`tests/test_api_smoke.py` (public GET smoke). Defects that cannot be fixed in P2 are encoded as
`xfail(reason=...)` in the test suite so they stay visible without turning CI red.

## Harness results (evidence)

| Check | Result |
|-------|--------|
| Registry entries | **16** (`zolai/engines.py`), all lazy targets resolve (`test_lazy_import_resolves`) |
| Registry ↔ probe drift guard | pass (`test_probe_table_matches_registry`) |
| Offline engines (network=False) run twice, identical output | **15/15** pass (ZVS determinism) |
| Offline engines under socket guard (zero outbound network) | **15/15** pass — 0 attempts recorded |
| Network-flagged engines excluded from offline runs | `online_search` construct-only (`network=True`, `deterministic=False`) |
| Mode default | `rule` (`ZOLAI_ENGINE_MODE` unset → rule; unknown value → rule, never `ai`) |
| Rule mode vs LLM chain | gate fires **before** provider registry is consulted, even with `GEMINI_API_KEY` set |
| `ai`/`hybrid` without key | degrade to rule path — chain returns `provider="rule_based"`, zero sockets; HTTP endpoints still **200** with identical bodies (modulo additive `mode`) |
| `ai` with key | gate opens — fake in-process provider called exactly once |
| `/api/v1/predictions/health` | reports `mode` (additive key only; other keys unchanged) |
| Full suite after P2 | **1552 passed, 7 skipped, 1 xfailed, 0 failed** (`ruff check zolai tests` clean) |

---

## F1 — Legacy `/chat/*` endpoints bypass `ZOLAI_ENGINE_MODE` (Medium)

- **Where:** `zolai/api/server.py` — `POST /chat/chat`, `POST /chat/chat/stream`, `GET /chat`,
  `GET /chat/models`, `POST /chat/zolai`, `POST /chat/gemini`.
- **Defect:** these open sockets to Ollama/Gemini regardless of engine mode. In `rule` mode
  (the default) an outbound LLM call is attempted — a D2 violation — and `GET /chat` /
  `POST /chat/chat` / `GET /chat/models` return **500** when the provider is unreachable
  (no graceful degradation; `/chat/zolai` and `/chat/gemini` *do* swallow errors → 200).
- **Evidence:** `tests/test_engine_contract.py::TestEngineMode::test_rule_mode_legacy_chat_endpoint_stays_offline`
  — marked `xfail` (observed: HTTP 500 + network attempt recorded by the socket guard).
- **Proposed fix (later phase):** route legacy chat through `FallbackChain` (which now honours
  `llm_allowed()`) or gate each handler with `llm_allowed()` + rule-path answer; P5 proxy posture
  already DENYs legacy mutations and keeps this surface internal. **No fix in P2.**

## F2 — `OnlineSearch` has no mode gate (Low)

- **Where:** `zolai/learning/online_search.py` (registry entry `online_search`, `network=True`).
- **Defect:** nothing inside the class consults `llm_allowed()`/`resolve_engine_path()`; any caller in
  `rule` mode can still trigger web search. The registry *documents* the capability, but enforcement
  is caller-side only.
- **Evidence:** capability flags + `test_network_engine_probe_runs_unguarded` (probe is construct-only
  by design — a live search would hit external backends and flake CI).
- **Proposed fix:** gate `search_*` entry points on engine mode (or assert mode in the router layer)
  when C2 lands the DB-backed usage-pattern queries that replace most web-search dependence.

## F3 — Tokenizer contract is construct-only until P3 (Info)

- **Where:** `zolai/tokenizer/zolai_tokenizer.py` (registry entry `tokenizer`).
- **Status:** no trained SentencePiece artifact ships yet (`data/tokenizer/*.model` is gitignored,
  P3 deliverable), so `encode`/`decode`/`vocab_size` cannot be exercised — the probe only proves the
  target imports and constructs (`_probe_tokenizer`).
- **Proposed:** re-run the contract harness after P3 with a real encode→decode round-trip probe
  against the artifact + manifest.

## F4 — AI gate is key-based only; keyless local LLM counts as "no credential" (Design note)

- **Where:** `zolai/engines.py::AI_KEY_ENV_VARS` (`GEMINI_API_KEY`, `OPENAI_API_KEY`,
  `OPENROUTER_API_KEY`).
- **Behaviour:** `hybrid`/`ai` without one of these keys always resolve to the rule path, so a running
  local **Ollama** (keyless by nature, `OllamaProvider.is_available` pings localhost) is never consulted
  — even though it is not an external LLM call. This is the conservative reading of D2
  ("absence of key must never break endpoints"), documented here so the choice is not silently
  re-litigated later.
- **Proposed:** if/when local-model enrichment is wanted, extend the gate with an explicit
  `ZOLAI_LOCAL_LLM=1` opt-in rather than weakening the default.

## F5 — ZVS forbidden-forms duplicated in 15 places (Medium, drift risk)

- **Where:** see [`ENGINE_HARDCODE_INVENTORY.md`](ENGINE_HARDCODE_INVENTORY.md) §3 — canonical is
  `zolai/zvs/rules_data.py`; 14 copies live across `api/`, `foundation/`, `data/services/`,
  `knowledge/`, `offline/` and the `/chat/zolai` system prompt.
- **Defect:** copies can (and will) drift; D2 requires versioned single-source config. Allowed as
  *versioned config*, but must be **one** versioned config.
- **Proposed (C2):** all consumers import `rules_data.py`, which loads a rebuildable
  `data/artifacts/zvs_rules_v1.json`.

## F6 — Attestation cold start ~21 s (Low, performance)

- **Where:** `zolai/learning/word_attestation.py::_ensure_loaded` (first call materialises Bible /
  dictionary / corpus word sets in memory; subsequent calls are ~0 s).
- **Impact:** the contract probe pays it once per test process (~42 s before instance reuse — the probe
  now reuses one instance). In serving, first request latency after cold start is affected.
- **Proposed (C2):** push attestation to indexed DB lookups (`EXISTS` per source) or a persisted
  bloom/set artifact so cold start is a file read.

---

## Explicitly not defects (verified in P2)

- All 16 registry lazy references resolve; no dead registry entries.
- Rule-path engines are deterministic and socket-free — the D2 offline contract holds on the real DB.
- `GET /api/v1/predictions/health` gained only the additive `mode` key; pre-existing consumers
  (`tests/test_prediction_api.py`, `tests/test_word_engine_api.py`) unaffected (they assert keys, not
  exact key sets).
- `FallbackChain` gate ordering: provider registry is never touched when the mode says no — proven by
  the exploding-registry sentinel in `test_rule_mode_blocks_chain_even_with_key`.

**All findings above are deferred by design — P2 is inventory + harness only.**
