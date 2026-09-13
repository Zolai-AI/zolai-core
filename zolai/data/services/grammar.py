"""Grammar service for pattern validation and ZVS checking."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..repositories import (
    GrammarRepository,
    WordCollocationRepository,
)


class GrammarService:
    """Service for grammar pattern validation and ZVS 2018 checking."""

    FORBIDDEN_FORMS = {
        "pathian": "pasian",
        "ram": "gam",
        "fapa": "tapa",
        "bawipa": "topa",
        "siangpahrang": "kumpipa",
        "cu": "tua",
        "cun": "tua",
        "suah": "suahtakna",
        "zalenna": "suahtakna",
        "nunnak": "nuntakna",
    }

    def __init__(self, engine: Engine) -> None:
        self.repo = GrammarRepository(engine)
        self.engine = engine

    def validate_zvs_2018(self, text: str) -> list[dict[str, Any]]:
        """Check text for ZVS 2018 violations.

        Returns list of violations with position and correction.
        """
        violations = []
        text_lower = text.lower()
        words = text_lower.split()

        for i, word in enumerate(words):
            for forbidden, correct in self.FORBIDDEN_FORMS.items():
                if forbidden in word:
                    violations.append({
                        "forbidden": forbidden,
                        "correct": correct,
                        "position": i,
                        "context": " ".join(words[max(0, i-2):i+3]),
                    })

        return violations

    def validate_sentence(self, zolai_text: str) -> dict[str, Any]:
        """Validate a Zolai sentence against grammar patterns.

        Returns validation result with matched patterns and ZVS check.
        """
        # ZVS 2018 check
        zvs_violations = self.validate_zvs_2018(zolai_text)

        # Pattern matching
        matched_patterns = self.repo.validate_pattern(zolai_text)

        return {
            "text": zolai_text,
            "zvs_compliant": len(zvs_violations) == 0,
            "zvs_violations": zvs_violations,
            "matched_patterns": matched_patterns,
            "pattern_count": len(matched_patterns),
        }

    def get_pattern(self, pattern_id: str) -> dict[str, Any] | None:
        """Get a grammar pattern by ID."""
        return self.repo.get_by_pattern_id(pattern_id)

    def search_patterns(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search grammar patterns."""
        return self.repo.get_by_pattern_name(query)

    def get_patterns_by_function(self, function: str) -> list[dict[str, Any]]:
        """Get patterns by grammatical function."""
        return self.repo.get_by_function(function)

    def get_patterns_by_book(self, book: str) -> list[dict[str, Any]]:
        """Get patterns applicable to a book."""
        return self.repo.get_by_book(book)

    def get_top_patterns(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get most frequent patterns."""
        return self.repo.get_top_patterns(limit=limit)

    def get_functions(self) -> list[str]:
        """Get all grammatical functions."""
        return self.repo.get_functions()

    def get_books(self) -> list[str]:
        """Get all books with patterns."""
        return self.repo.get_books()

    def parse_examples(self, examples_json: str) -> list[str]:
        """Parse examples JSON to list of refs."""
        return self.repo.parse_examples(examples_json)

    def get_stats(self) -> dict[str, Any]:
        """Get grammar statistics."""
        with self.engine.connect() as conn:
            total = conn.execute(
                text("SELECT COUNT(*) FROM grammar_patterns")
            ).scalar()
            with_examples = conn.execute(
                text("SELECT COUNT(*) FROM grammar_patterns WHERE examples != '[]'")
            ).scalar()
            functions = len(self.get_functions())
            books = len(self.get_books())

        return {
            "total_patterns": total or 0,
            "with_examples": with_examples or 0,
            "unique_functions": functions,
            "books_covered": books,
        }


class CollocationService:
    """Service for word collocations."""

    def __init__(self, engine: Engine) -> None:
        self.repo = WordCollocationRepository(engine)

    def get_by_word(self, word: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get collocations for a word."""
        return self.repo.get_by_word(word, limit=limit)

    def get_by_word1(self, word1: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get collocations where word is first."""
        return self.repo.get_by_word1(word1, limit=limit)

    def get_by_word2(self, word2: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get collocations where word is second."""
        return self.repo.get_by_word2(word2, limit=limit)

    def get_top(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get top collocations by frequency."""
        return self.repo.get_top_collocations(limit=limit)

    def get_high_pmi(self, min_pmi: float = 3.0, limit: int = 100) -> list[dict[str, Any]]:
        """Get high PMI collocations."""
        return self.repo.get_high_pmi(min_pmi, limit=limit)

    def get_stats(self) -> dict[str, Any]:
        """Get collocation statistics."""
        with self.engine.connect() as conn:
            total = conn.execute(
                text("SELECT COUNT(*) FROM word_collocations")
            ).scalar()
            high_pmi = conn.execute(
                text("SELECT COUNT(*) FROM word_collocations WHERE pmiproxy >= 3.0")
            ).scalar()

        return {
            "total_collocations": total or 0,
            "high_pmi_count": high_pmi or 0,
        }
