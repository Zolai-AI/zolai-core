"""Single-source-of-truth API contract enforcement.

The canonical contract lives in ``docs/api-contract.md``. This test asserts
that every endpoint the desktop UI relies on actually exists on the FastAPI
app -- so the backend can't drift from the contract. See ``docs/api-contract.md``
for the authoritative mapping (UI function -> method -> path -> params -> keys).
"""

from zolai.api.server import app, create_app

# (method, path) for every endpoint the desktop UI calls. THE source of truth.
# Keep in sync with docs/api-contract.md.
DESKTOP_CONTRACT = [
    # Desktop Router - Database Tools
    ("GET", "/desktop/stats"),
    ("GET", "/desktop/tables"),
    ("GET", "/desktop/query"),
    
    # Desktop Router - Dictionary Tools
    ("GET", "/desktop/dict/browse"),
    ("GET", "/desktop/dict/non-zolai"),
    ("GET", "/dictionary/search/all"),
    ("GET", "/dictionary/search/my"),
    ("POST", "/dictionary/add"),
    ("PUT", "/dictionary/update"),
    ("DELETE", "/dictionary/delete"),
    ("GET", "/desktop/dict/stats"),
    
    # Desktop Router - Bible Tools
    ("GET", "/desktop/bible/study"),
    ("GET", "/desktop/bible/learn"),
    ("GET", "/desktop/bible/context/book"),
    ("GET", "/desktop/bible/context/word"),
    ("GET", "/desktop/bible/context/topics"),
    ("GET", "/bible/search"),
    
    # Desktop Router - Gemini Tools
    ("GET", "/desktop/gemini/fill-en"),
    ("GET", "/desktop/gemini/fill-my"),
    ("GET", "/desktop/gemini/coverage"),
    ("GET", "/desktop/gemini/fill"),
    
    # Desktop Router - Training Tools
    ("GET", "/desktop/training/generate"),
    ("GET", "/desktop/training/generate-sentences"),
    ("GET", "/desktop/training/validate"),
    ("GET", "/desktop/training/deep-validate"),
    ("GET", "/desktop/training/build"),
    ("GET", "/desktop/training/build-qwen"),
    ("GET", "/desktop/training/export"),
    ("GET", "/desktop/training/build-corpus"),
    ("GET", "/desktop/training/corpus-stats"),
    
    # Desktop Router - Test & Quiz Tools
    ("GET", "/desktop/test/quiz"),
    ("GET", "/desktop/test/stats"),
    
    # Desktop Router - Grammar Tools
    ("GET", "/desktop/grammar/check"),
    ("GET", "/desktop/grammar/negation-rules"),
    
    # Desktop Router - Paragraph Tools
    ("GET", "/desktop/paragraph/analyze"),
    ("GET", "/desktop/paragraph/style"),
    ("GET", "/desktop/paragraph/paraphrase"),
    
    # Desktop Router - ZVS Tools
    ("GET", "/desktop/zvs/validate"),
    ("GET", "/desktop/zvs/forbidden"),
    
    # Desktop Router - Pattern Tools
    ("GET", "/desktop/pattern/stats"),
    ("GET", "/desktop/pattern/learn"),
    
    # Desktop Router - Export Tools
    ("GET", "/desktop/export/{data_type}"),
    
    # Desktop Router - Audit Tools
    ("GET", "/desktop/audit/recent"),
    
    # JSONL Pipeline Router
    ("POST", "/desktop/jsonl/import/all"),
    ("POST", "/desktop/jsonl/import/file"),
    ("GET", "/desktop/jsonl/import/status/{batch_id}"),
    ("GET", "/desktop/jsonl/import/log"),
    ("POST", "/desktop/jsonl/export/table"),
    ("POST", "/desktop/jsonl/export/all"),
    ("GET", "/desktop/jsonl/tables"),
    
    # Application Routes
    ("GET", "/health"),
    ("GET", "/monitor/health"),
    ("GET", "/monitor/coverage"),
    ("GET", "/monitor/audit"),
    ("GET", "/dictionary/search/all"),
    ("GET", "/dictionary/search/my"),
    ("POST", "/dictionary/add"),
    ("PUT", "/dictionary/update"),
    ("DELETE", "/dictionary/delete"),
    ("GET", "/bible/search"),
    ("GET", "/chat/models"),
    ("POST", "/chat/zolai"),
    ("POST", "/chat/chat"),
    ("POST", "/chat/chat/stream"),
    ("GET", "/chat"),
]


def _registered_endpoints():
    """Return the set of (method, path) actually mounted on the app."""
    spec = app.openapi()  # materializes included routers into real paths
    endpoints = set()
    for path, ops in spec["paths"].items():
        for method in ops:
            endpoints.add((method.upper(), path))
    return endpoints


def _contract_app_endpoints():
    """Endpoints registered on a freshly created app (must match `app`)."""
    fresh = create_app().openapi()
    endpoints = set()
    for path, ops in fresh["paths"].items():
        for method in ops:
            endpoints.add((method.upper(), path))
    return endpoints


def test_all_contract_paths_registered():
    registered = _registered_endpoints()
    missing = [entry for entry in DESKTOP_CONTRACT if entry not in registered]
    assert missing == [], f"Contract endpoints missing from app: {missing}"


def test_gemini_fill_my_present():
    registered = _registered_endpoints()
    assert ("GET", "/desktop/gemini/fill-my") in registered


def test_jsonl_endpoints_present():
    registered = _registered_endpoints()
    jsonl_endpoints = [
        ("POST", "/desktop/jsonl/import/all"),
        ("POST", "/desktop/jsonl/import/file"),
        ("GET", "/desktop/jsonl/import/status/{batch_id}"),
        ("GET", "/desktop/jsonl/import/log"),
        ("POST", "/desktop/jsonl/export/table"),
        ("POST", "/desktop/jsonl/export/all"),
        ("GET", "/desktop/jsonl/tables"),
    ]
    for ep in jsonl_endpoints:
        assert ep in registered, f"JSONL endpoint missing: {ep}"


def test_fresh_app_matches_module_app():
    assert _contract_app_endpoints() == _registered_endpoints()
