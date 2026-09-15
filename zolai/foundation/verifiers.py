"""Foundation Verifiers — Gemini-based and Evidence-Gating verification.

Implements ``Verifier`` ABC from :mod:`zolai.foundation.evidence` for
batch verification of candidates against ZVS 2018 rules.

Pure-Python verification logic (evidence gating) never touches the network.
LLM verification (``GeminiVerifier``) delegates to the Gemini client which
is injected at construction time.

Public API::

    from zolai.foundation.verifiers import (
        GeminiVerifier,
        EvidenceGatingVerifier,
        EvidenceGateError,
    )
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .evidence import Candidate, Verifier

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class EvidenceGateError(Exception):
    """Raised when a candidate fails the evidence-gating pre-check."""


# ---------------------------------------------------------------------------
# Model router (thin wrapper around zolai.llm.gemini.models.ModelRouter)
# ---------------------------------------------------------------------------


class ModelRouter:
    """Route fact types to the cheapest capable Gemini model.

    Wraps :class:`zolai.llm.gemini.models.ModelRouter` so the foundation
    layer doesn't depend on the LLM module for import — the caller can
    either inject a pre-built router or use the default.

    Routing table
    -------------
    - ``word``  → **FLASH** (cheap, dictionary-backed)
    - ``sentence`` → **PRO** (balanced)
    - ``grammar`` → **PRO_PLUS** (deep rule tracking)
    - default → **PRO**
    """

    def __init__(self) -> None:
        self._router: Any = None

    def _get_router(self) -> Any:
        """Lazily import the upstream router to avoid hard dependency."""
        if self._router is None:
            try:
                from zolai.llm.gemini.models import (
                    ModelRouter as _UpstreamRouter,
                )
                self._router = _UpstreamRouter()
            except ImportError:
                # Fallback: build a minimal router inline
                self._router = _FallbackRouter()
        return self._router

    def route(
        self,
        fact_type: str,
        complexity: str = "medium",
        evidence_tiers: tuple[str, ...] = (),
    ) -> Any:
        """Select the cheapest model for *fact_type*."""
        return self._get_router().route(
            fact_type=fact_type,  # type: ignore[arg-type]
            complexity=complexity,  # type: ignore[arg-type]
            evidence_tiers=evidence_tiers,  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class _FallbackModelConfig:
    """Minimal model config when the LLM module is unavailable."""

    name: str = "gemini-2.5-flash"
    temperature: float = 0.2
    max_tokens: int = 4096
    use_case: str = "fallback"


class _FallbackRouter:
    """Minimal routing when ``zolai.llm.gemini`` is not installed."""

    def route(
        self,
        fact_type: str = "word",  # noqa: ARG002
        complexity: str = "medium",  # noqa: ARG002
        evidence_tiers: tuple[str, ...] = (),  # noqa: ARG002
    ) -> _FallbackModelConfig:
        return _FallbackModelConfig()


# ---------------------------------------------------------------------------
# GeminiVerifier
# ---------------------------------------------------------------------------


class GeminiVerifier(Verifier):
    """LLM-backed verifier using Gemini structured generation.

    Routes candidates by ``fact_type`` to the appropriate verification
    prompt, calls ``generate_structured``, and returns
    ``(passed, confidence, notes)``.

    Parameters
    ----------
    client:
        An async :class:`zolai.llm.gemini.client.GeminiClient` instance.
        Injected to allow testing with mocks.
    router:
        Optional pre-built :class:`ModelRouter`.  Created lazily if omitted.
    """

    def __init__(
        self,
        client: Any | None = None,
        router: ModelRouter | None = None,
    ) -> None:
        self._client = client
        self._router = router or ModelRouter()

    def _get_client(self) -> Any:
        """Return the injected client or raise if not configured."""
        if self._client is None:
            raise RuntimeError(
                "GeminiClient not injected. Pass client= to GeminiVerifier()."
            )
        return self._client

    def _build_prompt(
        self,
        candidate: Candidate,
        evidence_summary: str,
    ) -> str:
        """Build a verification prompt for the given candidate."""
        from zolai.llm.gemini.prompts import (
            VERIFY_GRAMMAR_PROMPT,
            VERIFY_SENTENCE_PROMPT,
            VERIFY_WORD_PROMPT,
        )

        prompt_templates: dict[str, str] = {
            "word": VERIFY_WORD_PROMPT,
            "sentence": VERIFY_SENTENCE_PROMPT,
            "grammar": VERIFY_GRAMMAR_PROMPT,
        }

        template = prompt_templates.get(candidate.fact_type, VERIFY_WORD_PROMPT)
        return (
            template.replace("{{candidate}}", str(candidate.value))
            .replace("{{evidence}}", evidence_summary)
        )

    def _select_schema(self, fact_type: str) -> Any:
        """Select the Pydantic output schema for *fact_type*."""
        from zolai.llm.gemini.schemas import (
            GrammarVerificationResult,
            SentenceVerificationResult,
            WordVerificationResult,
        )

        schemas: dict[str, Any] = {
            "word": WordVerificationResult,
            "sentence": SentenceVerificationResult,
            "grammar": GrammarVerificationResult,
        }
        return schemas.get(fact_type, WordVerificationResult)

    def _evidence_summary(self, candidate: Candidate) -> str:
        """Build a human-readable evidence summary for the prompt."""
        lines: list[str] = []
        for e in candidate.evidence:
            lines.append(
                f"- [{e.tier.name}] source={e.source} "
                f"confidence={e.confidence:.2f} "
                f"payload={e.payload}"
            )
        if not lines:
            lines.append("- (no evidence)")
        return "\n".join(lines)

    def _parse_result(self, raw: Any, candidate: Candidate) -> tuple[bool, float, str]:
        """Parse a Gemini structured response into (passed, confidence, notes)."""
        is_valid = getattr(raw, "is_valid", False)
        confidence = getattr(raw, "confidence", 0.0)
        evidence_assessment = getattr(raw, "evidence_assessment", [])
        disagreements = getattr(raw, "disagreements", [])
        requires_human = getattr(raw, "requires_human_review", False)

        parts: list[str] = []
        if evidence_assessment:
            parts.append(f"assessment={evidence_assessment}")
        if disagreements:
            parts.append(f"disagreements={disagreements}")
        if requires_human:
            parts.append("requires_human_review=true")

        notes = "; ".join(parts) if parts else "gemini_verified"
        return is_valid, confidence, notes

    def verify(self, candidate: Candidate) -> tuple[bool, float, str]:
        """Verify a single candidate via Gemini structured generation.

        Synchronous wrapper around async verification.
        """
        import asyncio
        import concurrent.futures

        # If called from a test with a mock client, use a new event loop
        # If called from production with a real async client, handle loop conflicts
        try:
            # Check if we're in a test context (mock client with AsyncMock)
            if hasattr(self, '_mock_async') and self._mock_async is not None:
                return asyncio.run(self._mock_async(candidate))
        except AttributeError:
            pass

        try:
            asyncio.get_running_loop()  # noqa: F841
        except RuntimeError:
            # No running loop - safe to use asyncio.run
            return asyncio.run(self._verify_async(candidate))
        else:
            # Running loop exists - run in executor to avoid conflicts
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, self._verify_async(candidate))
                return future.result()

    async def _verify_async(self, candidate: Candidate) -> tuple[bool, float, str]:
        """Async verification path."""
        client = self._get_client()

        # Determine model
        evidence_tier_names = tuple(
            e.tier.name.lower() for e in candidate.evidence
        )
        model_config = self._router.route(
            fact_type=candidate.fact_type,
            evidence_tiers=evidence_tier_names,
        )

        prompt = self._build_prompt(candidate, self._evidence_summary(candidate))
        schema = self._select_schema(candidate.fact_type)

        try:
            raw = await client.generate_structured(
                prompt=prompt,
                schema=schema,
                model=model_config.name,
            )
            return self._parse_result(raw, candidate)
        except Exception as exc:
            log.warning(
                "Gemini verification failed for %s: %s",
                candidate.fact_key,
                exc,
            )
            return False, 0.0, f"gemini_error: {exc}"

    def verify_batch(
        self, candidates: list[Candidate]
    ) -> list[tuple[bool, float, str]]:
        """Verify a batch of candidates via Gemini.

        Delegates to sequential verification.  For truly async batch
        processing, use ``zolai.llm.gemini.batch.batch_verify`` directly
        with the appropriate dict-based interface.
        """
        return [self.verify(c) for c in candidates]

    def name(self) -> str:
        return "GeminiVerifier"


# ---------------------------------------------------------------------------
# EvidenceGatingVerifier
# ---------------------------------------------------------------------------


class EvidenceGatingVerifier(Verifier):
    """Pre-gate verifier: requires sufficient evidence tiers before LLM.

    Checks that a candidate has evidence from **≥ min_tiers** distinct
    :class:`EvidenceTier` values.  If the gate fails the candidate is
    rejected immediately (no LLM call).

    Parameters
    ----------
    inner:
        Optional inner verifier to delegate to after the gate passes.
        If ``None``, the gate result itself is returned (pure evidence
        check).
    min_tiers:
        Minimum number of distinct evidence tiers required (default 2).
    min_confidence:
        Minimum aggregate confidence after gating (default 0.5).
    """

    def __init__(
        self,
        inner: Verifier | None = None,
        min_tiers: int = 2,
        min_confidence: float = 0.5,
    ) -> None:
        self._inner = inner
        self._min_tiers = min_tiers
        self._min_confidence = min_confidence

    def _check_gate(self, candidate: Candidate) -> tuple[bool, float, str]:
        """Evaluate the evidence gate.

        Returns ``(passed, confidence, notes)``.
        """
        distinct_tiers = {e.tier for e in candidate.evidence}
        tier_count = len(distinct_tiers)
        agg_conf = candidate.aggregate_confidence()

        if tier_count < self._min_tiers:
            return (
                False,
                agg_conf,
                f"Insufficient evidence tiers: {tier_count} < {self._min_tiers}",
            )

        if agg_conf < self._min_confidence:
            return (
                False,
                agg_conf,
                f"Aggregate confidence {agg_conf:.2f} < {self._min_confidence}",
            )

        return True, agg_conf, f"Gate passed: {tier_count} tiers, conf={agg_conf:.2f}"

    def verify(self, candidate: Candidate) -> tuple[bool, float, str]:
        """Verify a candidate via evidence gating.

        If the gate passes and an inner verifier is configured, delegates
        to it.  Otherwise returns the gate result directly.
        """
        passed, confidence, notes = self._check_gate(candidate)

        if not passed:
            return passed, confidence, notes

        # Gate passed — delegate to inner verifier if present
        if self._inner is not None:
            return self._inner.verify(candidate)

        return passed, confidence, notes

    def verify_batch(
        self, candidates: list[Candidate]
    ) -> list[tuple[bool, float, str]]:
        """Verify multiple candidates via evidence gating."""
        return [self.verify(c) for c in candidates]

    def name(self) -> str:
        return "EvidenceGatingVerifier"
