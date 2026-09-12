"""Text summarization for Zolai using Gemini ensemble + extractive fallback."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ZolaiSummarizer:
    """Text summarization for Zolai using Gemini ensemble."""

    def __init__(self, voter: Any = None):
        """Initialize summarizer.

        Args:
            voter: EnsembleVoter instance for Gemini integration (optional).
        """
        self.voter = voter

    async def summarize(
        self,
        text: str,
        max_sentences: int = 3,
        language: str = "en",
    ) -> dict:
        """Summarize Zolai text using Gemini ensemble.

        Args:
            text: Zolai text to summarize.
            max_sentences: Maximum sentences in summary.
            language: Output language ("en" for English, "zo" for Zolai).

        Returns:
            {"summary": "...", "key_points": [...], "sentence_count": 3}
        """
        if self.voter is None:
            return self._extractive_summary(text, max_sentences)

        lang_instruction = (
            "Output the summary in English."
            if language == "en"
            else "Output the summary in Zolai (Tedim)."
        )
        prompt = (
            f"Summarize this Zolai text in {max_sentences} sentences. "
            f"{lang_instruction}\n"
            "Also extract 3-5 key points.\n\n"
            f"Text:\n{text}\n\n"
            'Output JSON: {"summary": "...", "key_points": ["...", "..."]}'
        )
        try:
            raw = await self.voter.vote(prompt)
            result = json.loads(raw) if isinstance(raw, str) else raw
            data = result.get("result", result)
            summary = data.get("summary", "")
            key_points = data.get("key_points", [])
            return {
                "summary": summary,
                "key_points": key_points,
                "sentence_count": len(summary.split(". ")),
            }
        except Exception as e:
            logger.warning("Gemini summarization failed, using extractive: %s", e)
            return self._extractive_summary(text, max_sentences)

    def _extractive_summary(self, text: str, max_sentences: int = 3) -> dict:
        """Extractive summarization fallback.

        Args:
            text: Zolai text to summarize.
            max_sentences: Maximum sentences in summary.

        Returns:
            {"summary": "...", "key_points": [...], "sentence_count": 3}
        """
        # Split into sentences
        sentences = []
        for part in text.replace("!", ".").replace("?", ".").split("."):
            s = part.strip()
            if s and len(s) > 5:
                sentences.append(s + ".")

        if not sentences:
            return {
                "summary": text[:200] if text else "",
                "key_points": [],
                "sentence_count": 0,
            }

        # Simple extractive: take first N sentences
        selected = sentences[:max_sentences]
        summary = " ".join(selected)

        # Extract key points (sentences with important words)
        important_words = {
            "pasian", "topa", "hiam", "hoih", "koh",
            "nuntakna", "suahtakna", "dam", "damna",
        }
        key_points = []
        for s in sentences:
            words = set(s.lower().split())
            if words & important_words:
                key_points.append(s)
                if len(key_points) >= 5:
                    break

        return {
            "summary": summary,
            "key_points": key_points[:5],
            "sentence_count": len(selected),
        }

    async def summarize_batch(
        self,
        texts: list[str],
        max_sentences: int = 3,
    ) -> list[dict]:
        """Summarize multiple Zolai texts.

        Args:
            texts: List of Zolai texts.
            max_sentences: Maximum sentences per summary.

        Returns:
            List of summary results.
        """
        results = []
        for text in texts:
            result = await self.summarize(text, max_sentences)
            results.append(result)
        return results


# Module-level singleton
_summarizer_instance: ZolaiSummarizer | None = None


def get_summarizer(voter: Any = None) -> ZolaiSummarizer:
    """Get or create the singleton summarizer instance."""
    global _summarizer_instance
    if _summarizer_instance is None:
        _summarizer_instance = ZolaiSummarizer(voter=voter)
    return _summarizer_instance
