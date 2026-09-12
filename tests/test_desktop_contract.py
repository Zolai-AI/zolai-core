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
    ("GET", "/desktop/stats"),
    ("GET", "/desktop/tables"),
    ("GET", "/desktop/query"),
    ("GET", "/desktop/dict/browse"),
    ("GET", "/desktop/dict/non-zolai"),
    ("GET", "/desktop/bible/study"),
    ("GET", "/desktop/bible/learn"),
    ("GET", "/desktop/bible/context/book"),
    ("GET", "/desktop/bible/context/word"),
    ("GET", "/desktop/bible/context/topics"),
    ("GET", "/desktop/gemini/fill-en"),
    ("GET", "/desktop/gemini/fill-my"),
    ("GET", "/desktop/gemini/coverage"),
    ("GET", "/desktop/training/generate"),
    ("GET", "/desktop/training/build"),
    ("GET", "/desktop/training/build-qwen"),
    ("GET", "/desktop/export/{data_type}"),
    ("GET", "/desktop/test/quiz"),
    ("GET", "/desktop/grammar/check"),
    ("GET", "/desktop/paragraph/analyze"),
    ("GET", "/desktop/zvs/validate"),
    ("GET", "/desktop/audit/recent"),
    ("GET", "/monitor/health"),
    ("GET", "/monitor/coverage"),
    ("GET", "/monitor/audit"),
    ("GET", "/dictionary/search/all"),
    ("GET", "/dictionary/search/my"),
    ("POST", "/dictionary/add"),
    ("PUT", "/dictionary/update"),
    ("DELETE", "/dictionary/delete"),
    ("GET", "/bible/search"),
    ("GET", "/health"),
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


def test_fresh_app_matches_module_app():
    assert _contract_app_endpoints() == _registered_endpoints()
