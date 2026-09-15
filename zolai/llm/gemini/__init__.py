"""Gemini LLM client for Zolai verification tasks.

Public API::

    from zolai.llm.gemini import (
        GeminiClient,
        ModelConfig,
        FLASH, PRO, PRO_PLUS,
        ModelRouter,
        VERIFY_WORD_PROMPT, VERIFY_SENTENCE_PROMPT, VERIFY_GRAMMAR_PROMPT,
        WordVerificationResult,
        SentenceVerificationResult,
        GrammarVerificationResult,
        batch_verify,
        build_context,
        inject_evidence,
    )
"""

from __future__ import annotations

from .batch import batch_verify
from .client import CallRecord, GeminiClient
from .grounding import build_context, inject_evidence
from .models import FLASH, PRO, PRO_PLUS, ModelConfig, ModelRouter
from .prompts import VERIFY_GRAMMAR_PROMPT, VERIFY_SENTENCE_PROMPT, VERIFY_WORD_PROMPT
from .schemas import (
    GrammarVerificationResult,
    SentenceVerificationResult,
    WordVerificationResult,
)

__all__ = [
    "FLASH",
    "PRO",
    "PRO_PLUS",
    "CallRecord",
    "GrammarVerificationResult",
    "GeminiClient",
    "ModelConfig",
    "ModelRouter",
    "SentenceVerificationResult",
    "VERIFY_GRAMMAR_PROMPT",
    "VERIFY_SENTENCE_PROMPT",
    "VERIFY_WORD_PROMPT",
    "WordVerificationResult",
    "batch_verify",
    "build_context",
    "inject_evidence",
]
