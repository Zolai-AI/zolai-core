"""Async batch processing with concurrency control for Gemini verification."""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from .client import GeminiClient
from .models import ModelRouter

logger = logging.getLogger(__name__)


async def batch_verify(
    candidates: list[dict],
    client: GeminiClient,
    model_router: ModelRouter,
    *,
    max_concurrent: int = 3,
    prompt_template: str = "",
    schema: type | None = None,
) -> AsyncIterator[tuple[dict, dict | None]]:
    """Process a list of candidates concurrently with Gemini verification.

    Yields ``(candidate, result)`` tuples as each candidate is verified.
    Failures yield ``(candidate, None)`` after logging the error.

    Args:
        candidates: List of candidate dicts. Each must have at least a
            ``"text"`` key and optionally ``"fact_type"``, ``"complexity"``,
            ``"evidence"`` keys.
        client: An initialised :class:`GeminiClient`.
        model_router: Router to select the appropriate model.
        max_concurrent: Maximum parallel verifications (semaphore-bounded).
        prompt_template: A prompt template with ``{{candidate}}`` and
            ``{{evidence}}`` placeholders.
        schema: Optional Pydantic schema for structured output.

    Yields:
        ``(candidate_dict, result_dict_or_None)`` pairs.
    """
    if not candidates:
        return

    semaphore = asyncio.Semaphore(max_concurrent)

    async def _verify_one(cand: dict) -> dict | None:
        """Verify a single candidate under the semaphore."""
        async with semaphore:
            from .grounding import inject_evidence

            fact_type = cand.get("fact_type", "word")
            complexity = cand.get("complexity", "medium")
            evidence_tiers = tuple(cand.get("evidence_tiers", []))

            model_config = model_router.route(
                fact_type=fact_type,
                complexity=complexity,
                evidence_tiers=evidence_tiers,
            )

            # Build prompt from template
            candidate_json = {
                "text": cand.get("text", ""),
                "fact_type": fact_type,
                "metadata": cand.get("metadata", {}),
            }
            evidence = cand.get("evidence", "")

            filled_prompt = inject_evidence(
                prompt_template=prompt_template,
                evidence_context=evidence,
                candidate_json=candidate_json,
            )

            try:
                if schema is not None:
                    raw = await client.generate_structured(
                        filled_prompt, schema, model=model_config.name
                    )
                    if hasattr(raw, "model_dump"):
                        return raw.model_dump()
                    return raw if isinstance(raw, dict) else {"raw": str(raw)}
                else:
                    text = await client.generate_text(
                        filled_prompt, model=model_config.name
                    )
                    return {"text": text}
            except Exception as exc:
                logger.error(
                    "Failed to verify candidate %r: %s",
                    cand.get("text", "")[:60],
                    exc,
                )
                return None

    # Run all candidates concurrently (bounded by semaphore)
    tasks = [asyncio.create_task(_verify_one(cand)) for cand in candidates]

    for cand, task in zip(candidates, tasks):
        result = await task
        yield cand, result
