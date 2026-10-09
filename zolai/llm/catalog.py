"""Built-in AI provider catalog — the single source of truth for every
provider this platform ships with (P1, plan §A).

The admin never adds a provider: :func:`seed_catalog` creates one row per
entry on boot, and the admin only pastes a key (or points a row at an
``env:NAME`` reference).  Adding an entry here is all it takes for a provider
to appear in ``GET /api/v1/admin/ai-providers``.

Adapter semantics — which request path :mod:`zolai.llm.adapter` takes:

- ``brain``     — P-Core Brain (Bearer key, Zen proxy endpoint; **never**
  receives a native ``tools`` key in its request body).
- ``openai``    — generic OpenAI-compatible ``POST …/chat/completions``.
- ``openrouter``— OpenRouter (same wire shape as ``openai``).
- ``custom``    — admin-defined endpoint (rows created directly in the DB).

Everything non-brain speaks the OpenAI wire format, so Anthropic / Gemini /
Groq / … are configured with their *OpenAI-compatible* endpoints.

Identity rules (ported from ``pcore-assistant`` ``catalog/ai-providers.ts``):

- ``catalog_id`` is the **stable identity** — persisted on the row, never
  renamed; every join/lookup compares ids, never display names.
- ``name`` is the display name — admin-renameable.
- ``base_url = ''`` on the row means "catalog default resolved at request
  time", so ops can change a default in config without a migration.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

#: Stable identity of the first-class P-Core Brain row.
BRAIN_CATALOG_ID = "pcore-brain"

#: Public Brain endpoint base, overridable via ``AI_BRAIN_URL`` /
#: ``PCORE_BRAIN_URL`` (blank value in the environment falls back to this).
DEFAULT_BRAIN_BASE_URL = "https://pcore-brain.peterlianpi.site/v1"

#: Suffix appended to the Brain base URL to form the completions endpoint.
BRAIN_COMPLETIONS_SUFFIX = "/chat/completions"


@dataclass(frozen=True)
class CatalogProvider:
    """One built-in catalog entry (immutable; the DB row is the mutable copy)."""

    id: str
    """Stable identity — persisted as ``ai_providers.catalog_id``."""

    name: str
    """Display name; the admin may rename the row."""

    adapter: str
    """``brain`` | ``openai`` | ``openrouter`` | ``custom``."""

    base_url: str
    """Default OpenAI-compatible chat-completions endpoint (blank = dynamic)."""

    models: tuple[str, ...]
    """Default model ids offered on first boot (admin may edit the list)."""

    docs: str
    """Provider documentation link for the settings UI."""

    env_keys: tuple[str, ...]
    """Env vars checked (in order) when resolving the API key."""

    requires_key: bool
    """``False`` = local endpoint that needs no key (Ollama)."""


#: The catalog — 7 seeded rows (plan §A: brain first-class + 6 OpenAI-wire).
AI_PROVIDER_CATALOG: tuple[CatalogProvider, ...] = (
    CatalogProvider(
        id=BRAIN_CATALOG_ID,
        name="P-Core Brain",
        adapter="brain",
        # Resolved from config at request time — never a static string, so a
        # boot picks up a changed AI_BRAIN_URL.
        base_url="",
        # Only models that actually return a completion are offered (matches
        # the pcore-assistant probe results — no dead ids in the model picker).
        models=(
            "opencode/nemotron-3-ultra-free",
            "opencode/mimo-v2.6-flash-free",
            "opencode/muse-spark-1.3-contributor-free",
            "opencode/big-pickle",
        ),
        docs="https://pcore-system.site",
        env_keys=("AI_BRAIN_API_KEY", "PCORE_BRIDGE_API_KEY", "AI_API_KEY"),
        requires_key=True,
    ),
    CatalogProvider(
        id="openai",
        name="OpenAI",
        adapter="openai",
        base_url="https://api.openai.com/v1/chat/completions",
        models=("gpt-4o-mini", "gpt-4o"),
        docs="https://platform.openai.com/docs/api-reference",
        env_keys=("OPENAI_API_KEY",),
        requires_key=True,
    ),
    CatalogProvider(
        id="anthropic",
        name="Anthropic",
        adapter="openai",  # via Anthropic's OpenAI-compatible endpoint
        base_url="https://api.anthropic.com/v1/chat/completions",
        models=("claude-sonnet-4-5", "claude-haiku-4-5"),
        docs="https://docs.anthropic.com/en/api/client-sdks",
        env_keys=("ANTHROPIC_API_KEY",),
        requires_key=True,
    ),
    CatalogProvider(
        id="google-gemini",
        name="Google Gemini",
        adapter="openai",  # via Gemini's OpenAI-compatibility layer
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        models=("gemini-2.5-flash", "gemini-2.0-flash"),
        docs="https://ai.google.dev/gemini-api/docs/openai",
        env_keys=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        requires_key=True,
    ),
    CatalogProvider(
        id="groq",
        name="Groq",
        adapter="openai",
        base_url="https://api.groq.com/openai/v1/chat/completions",
        models=("llama-3.3-70b-versatile", "llama-3.1-8b-instant"),
        docs="https://console.groq.com/docs/api-reference",
        env_keys=("GROQ_API_KEY",),
        requires_key=True,
    ),
    CatalogProvider(
        id="openrouter",
        name="OpenRouter",
        adapter="openrouter",
        base_url="https://openrouter.ai/api/v1/chat/completions",
        # ``:free`` is OpenRouter's own free-tier marker — listed first so
        # enabling this provider defaults to a free model.  A request may also
        # address any id with the ``openrouter/`` platform prefix; the adapter's
        # ``to_wire_model()`` strips it before the wire call.
        models=(
            "nvidia/nemotron-3.5-lightning:free",
            "qwen/qwen3.8-27b:free",
            "google/gemma-4-31b-it:free",
            "openai/gpt-4o-mini",
            "anthropic/claude-haiku-4.5",
            "deepseek/deepseek-chat",
        ),
        docs="https://openrouter.ai/docs/api-reference",
        env_keys=("OPENROUTER_API_KEY",),
        requires_key=True,
    ),
    CatalogProvider(
        id="ollama",
        name="Ollama",
        adapter="openai",  # local OpenAI-compatible server, no key required
        base_url="http://127.0.0.1:11434/v1/chat/completions",
        models=("llama3.2", "mistral", "qwen2.5"),
        docs="https://docs.ollama.com/api/chat",
        env_keys=(),
        requires_key=False,
    ),
)

#: Fast path id → entry.
CATALOG_BY_ID: dict[str, CatalogProvider] = {e.id: e for e in AI_PROVIDER_CATALOG}


def find_catalog(catalog_id: str | None) -> CatalogProvider | None:
    """Look up a catalog entry by its stable id (never by display name)."""
    if not catalog_id:
        return None
    return CATALOG_BY_ID.get(str(catalog_id).strip())


def brain_base_url() -> str:
    """Config-resolved Brain base URL (``AI_BRAIN_URL`` / ``PCORE_BRAIN_URL``)."""
    for var in ("AI_BRAIN_URL", "PCORE_BRAIN_URL"):
        raw = os.environ.get(var)
        if raw and raw.strip():
            return raw.strip().rstrip("/")
    return DEFAULT_BRAIN_BASE_URL


def brain_completions_url() -> str:
    """Full Brain chat-completions endpoint (idempotent suffix append)."""
    base = brain_base_url()
    if base.endswith(BRAIN_COMPLETIONS_SUFFIX):
        return base
    return base + BRAIN_COMPLETIONS_SUFFIX


def catalog_default_base_url(entry: CatalogProvider) -> str:
    """Endpoint a row uses when it has no admin-supplied ``base_url``."""
    if entry.id == BRAIN_CATALOG_ID:
        return brain_completions_url()
    return entry.base_url


def read_catalog_env_key(entry: CatalogProvider) -> str:
    """First env var of ``entry.env_keys`` that holds a key (``""`` when none)."""
    for name in entry.env_keys:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def seed_catalog(manager: Any = None) -> dict[str, Any]:
    """Idempotently insert every missing catalog row (app-boot seed).

    Existing rows are **never** overwritten — display name, models, keys and
    endpoint overrides are admin state; only *missing* ``catalog_id`` rows are
    created.  ``catalog_id`` is the join key everywhere (compare ids, never
    names).

    Returns:
        Dict with ``created`` / ``skipped`` / ``errors`` lists.
    """
    from ..data.migrations import create_ai_provider_tables

    if manager is None:
        from ..data.database import get_manager

        manager = get_manager()

    created: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    # Defensive: make sure the tables exist even if seed runs standalone
    # (the app lifespan already ran run_all_migrations before this).
    table_result = create_ai_provider_tables(manager)
    errors.extend(table_result.get("errors", []))

    from sqlalchemy import text

    try:
        with manager.engine.begin() as conn:
            existing = {
                row[0]
                for row in conn.execute(text("SELECT catalog_id FROM ai_providers")).fetchall()
            }
            for entry in AI_PROVIDER_CATALOG:
                if entry.id in existing:
                    skipped.append(entry.id)
                    continue
                conn.execute(
                    text(
                        "INSERT INTO ai_providers "
                        "(catalog_id, name, adapter, base_url, models, selected_model, "
                        " docs, requires_key, api_key_ref, enabled, is_active, tier, "
                        " timeout_s, metadata, created_at, updated_at) "
                        "VALUES (:catalog_id, :name, :adapter, :base_url, :models, '', "
                        " :docs, :requires_key, NULL, 1, 0, 'standard', 60, '{}', "
                        " :now, :now)"
                    ),
                    {
                        "catalog_id": entry.id,
                        "name": entry.name,
                        "adapter": entry.adapter,
                        # '' = catalog default resolved at request time.
                        "base_url": entry.base_url if entry.id != BRAIN_CATALOG_ID else "",
                        "models": json.dumps(list(entry.models)),
                        "docs": entry.docs,
                        "requires_key": 1 if entry.requires_key else 0,
                        "now": _utc_now(),
                    },
                )
                created.append(entry.id)

            # Phase B: a store where no row is active has no default at all —
            # activate the first-class brain row so a fresh install works.
            # Only when NOTHING is active: an admin's explicit choice is never
            # clobbered (one active row at a time, same rule as activate()).
            active = conn.execute(
                text("SELECT catalog_id FROM ai_providers WHERE is_active = 1")
            ).fetchone()
            if active is None:
                brain = conn.execute(
                    text("SELECT catalog_id FROM ai_providers WHERE catalog_id = :cid"),
                    {"cid": BRAIN_CATALOG_ID},
                ).fetchone()
                if brain is not None:
                    conn.execute(
                        text(
                            "UPDATE ai_providers SET is_active = 1, updated_at = :now "
                            "WHERE catalog_id = :cid"
                        ),
                        {"cid": BRAIN_CATALOG_ID, "now": _utc_now()},
                    )
                    logger.info("ai_provider_seed: activated default %s", BRAIN_CATALOG_ID)
    except Exception as exc:  # pragma: no cover - defensive
        errors.append(f"seed_catalog: {exc}")
        logger.exception("ai provider catalog seed failed")

    logger.info("ai_provider_seed: %s", {"created": created, "skipped": len(skipped)})
    return {"created": created, "skipped": skipped, "errors": errors}
