"""
Translation Agent — translates between Zolai and English.
"""
from __future__ import annotations

from ..api.rag_context import get_rag_context
from .base import AgentResult, ZolaiAgent


class TranslationAgent(ZolaiAgent):
    """Translates Zolai ↔ English using dictionary + Bible context."""

    def __init__(self) -> None:
        super().__init__("translation_agent")
        self.description = "Translates between Zolai and English with context"

    def process(self, input_data: dict) -> AgentResult:
        text = input_data.get("text", "")
        direction = input_data.get("direction", "auto")  # zo-en, en-zo, auto

        if not text:
            return AgentResult(
                success=False, errors=["No text provided"], agent_name=self.name
            )

        # Auto-detect direction
        if direction == "auto":
            direction = self._detect_direction(text)

        # Get translation context
        rag = get_rag_context()

        if direction == "zo-en":
            results = []
            words = rag.extract_zolai_words(text)
            for word in words[:5]:
                dict_results = rag.lookup_dictionary(word, limit=3)
                results.extend(dict_results)

            return AgentResult(
                success=True,
                data={
                    "text": text,
                    "direction": direction,
                    "translations": results,
                    "word_count": len(words),
                },
                agent_name=self.name,
            )
        else:  # en-zo
            # Search dictionary for English words
            words = text.lower().split()
            results = []
            for word in words:
                dict_results = rag.lookup_dictionary(word, limit=3)
                results.extend(dict_results)

            return AgentResult(
                success=True,
                data={
                    "text": text,
                    "direction": direction,
                    "translations": results,
                    "word_count": len(words),
                },
                agent_name=self.name,
            )

    def _detect_direction(self, text: str) -> str:
        """Auto-detect if text is Zolai or English."""
        words = text.lower().split()
        english_indicators = {
            "the", "is", "are", "was", "what", "how",
            "do", "you", "i", "he", "she",
        }
        english_count = sum(1 for w in words if w in english_indicators)
        if english_count > len(words) * 0.3:
            return "en-zo"
        return "zo-en"
