---
title: API Envelope Error-Messages + Desktop Surface (Plan)
status: complete
last_updated: 2026-10-18
---

# API Envelope: Error Messages + Desktop Surface

> Scope note: the pass covers **every** `zolai/api/*_router.py` that can return a 200 body
> carrying exception text, not only the desktop router. The three named extra modules
> (`zolai/api/desktop_app.py`, `zolai/api/self_learning_engine.py`,
> `zolai/api/zomidaily_learning_engine.py`) are all under `zolai/api/`.

**Status: IMPLEMENT_DONE** (commit `365a693` on `main`).

## Re-measured leak sites

Raw-exception-in-200-body sites re-measured at **28 leak lines across 9 modules** (task
estimate said ~18 in one file). **27 fixed** · **1 allowlisted** as a documented KNOWN
DEFERRED LEAK:

- `zolai/api/server.py` legacy `/chat/gemini` (marker `zolai_response=f"Gemini error: {str(e)}"`
  ~L1298) — `server.py` is shared surface, explicitly out of this pass's touch list; the
  allowlist row must be deleted when it is fixed.

| Module | Sites | Disposition |
|---|---|---|
| `zolai/api/desktop_router.py` | 19 (18× `{"error": str(e)}` + `r.stderr[:500]`) | fixed |
| `zolai/api/pipeline.py` | 2 (`str(e)` + stderr — reaches the **job-status HTTP endpoint**) | fixed |
| `zolai/api/assistant_router.py` | 1 (`provider_error=str(exc)` on public 200) | fixed (`ProviderError.code` only) |
| `zolai/api/desktop_app.py` | 1 | fixed |
| `zolai/api/dynamic_data_pipeline.py` | 1 | fixed |
| `zolai/api/gemini_ensemble.py` | 1 | fixed |
| `zolai/api/self_learning_engine.py` | 1 | fixed |
| `zolai/api/zomidaily_learning_engine.py` | 1 | fixed |
| `zolai/api/server.py` | 1 | **allowlisted** (deferred, see above) |

## Wire-up

New messages route through `internal_error_envelope()` / `internal_http_error()` / `api_error()`
(traceback + raw `str(exc)` logged server-side; response keeps the same HTTP status + same
response-model shape, message is now stable/human-readable). Pre-existing `logger.error(...)`
calls at converted sites were **upgraded into** `internal_error_envelope` (same logger, gains
traceback) rather than kept as duplicates.

## Widened AST guard

| Done when | |
|---|---|
| [`test_api_error_envelope.py`](../../zolai-core/tests/test_api_error_envelope.py) AST scan widened from **module-level constants only** to: `return` statements, response constructors, response-field container writes, and list/dict mutations carrying an `except`-clause binding name | ☑ |
| `except HTTPException` sites excluded; **stderr-subprocess pattern** (`p.stderr`, `r.stderr`) caught even without an except binding | ☑ (0 desktop hits) |
| **Exemptions = explicit allowlist entries** (exact line + reason) — no silent skip patterns | ☑ (1 entry, `LEGACY_GEMINI_LEAK`) |
| Synthetic unit test proving the new rules catch the pre-change pattern (**non-vacuous**) | ☑ (`test_body_scan_catches_the_pre_change_pattern`; pre-change tree scan → 28 rows, test FAILS with 27 unexempted) |
| `test_allowlist_entries_are_still_present` still fails on stale/duplicate entries | ☑ (already enforced, keep; presence now counts unique linenos across both scans) |
| Envelope tests 12+ / desktop-surface 12 / rbac 30 all pass · collect-only 0 errors · ruff clean on changed files (no NEW errors vs repo baseline ~349) | ☑ (22 / 12 / 30 · 2925 collected 0 errors · 8 files clean; `assistant_router.py` keeps exactly its 8 pre-existing errors, shifted +6) |

## Verified by (evidence)

- Envelope suite **22 passed** · `test_desktop_surface_auth.py` **12 passed** ·
  `test_rbac_public_matrix.py` **30 passed** · `test_assistant_api.py` **20 passed** ·
  `test_dbfirst_compliance.py` **11 passed** · `--collect-only` **2925 collected / 0 errors**.
- Non-vacuity: pre-change tree scanned with the new guard → 28 leak rows; simulated
  `test_no_raw_exception_text_in_200_body` **FAILS** with 27 unexempted rows.
- Byte-compatibility: success payloads of 3 desktop endpoints byte-identical pre/post;
  error paths stay 200 with the same single-key shape.
- Ruff: 8 files clean; `assistant_router.py` exactly its 8 pre-existing errors (lines +6).
