"""Translation service for translation pairs and word alignments."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..repositories import (
    TranslationRepository,
    WordAlignmentRepository,
)


class TranslationService:
    """Service for translation pair management."""

    def __init__(self, engine: Engine) -> None:
        self.repo = TranslationRepository(engine)
        self.engine = engine

    def add_pair(
        self,
        source: str,
        target: str,
        direction: str,
        reference: str = "",
        confidence: float = 1.0,
        user: str = "system",
    ) -> dict[str, Any]:
        """Add a new translation pair."""
        errors = []

        if not source.strip():
            errors.append("Source text cannot be empty")
        if not target.strip():
            errors.append("Target text cannot be empty")
        if not direction:
            errors.append("Direction is required")
        if not 0 <= confidence <= 1:
            errors.append("Confidence must be between 0 and 1")

        if errors:
            return {"success": False, "errors": errors}

        data = {
            "source": source.strip(),
            "target": target.strip(),
            "direction": direction,
            "reference": reference.strip() if reference else "",
            "confidence": confidence,
        }

        entity_id = self.repo.create(data, user=user)
        return {"success": True, "id": entity_id, "data": data}

    def get_by_direction(self, direction: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get translation pairs by direction."""
        return self.repo.get_by_direction(direction, limit=limit)

    def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search translation pairs in both source and target."""
        return self.repo.search_both(query, limit=limit)

    def search_source(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search in source language."""
        return self.repo.search_source(query, limit=limit)

    def search_target(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search in target language."""
        return self.repo.search_target(query, limit=limit)

    def get_by_reference(self, reference: str) -> list[dict[str, Any]]:
        """Get translations for a Bible reference."""
        return self.repo.get_by_reference(reference)

    def get_high_confidence(
        self, min_confidence: float = 0.9, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get high-confidence translation pairs."""
        return self.repo.get_high_confidence(min_confidence, limit=limit)

    def get_directions(self) -> list[str]:
        """Get all translation directions."""
        return self.repo.get_directions()

    def get_stats(self) -> dict[str, Any]:
        """Get translation statistics."""
        with self.engine.connect() as conn:
            total = conn.execute(text("SELECT COUNT(*) FROM translations")).scalar()
            by_direction = self.repo.count_by_direction()

        return {
            "total_pairs": total or 0,
            "by_direction": by_direction,
        }


class AlignmentService:
    """Service for word-level alignments."""

    def __init__(self, engine: Engine) -> None:
        self.repo = WordAlignmentRepository(engine)

    def get_by_ref(self, ref: str) -> list[dict[str, Any]]:
        """Get all word alignments for a verse."""
        return self.repo.get_by_ref(ref)

    def get_by_zolai(self, zolai_word: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get alignments for a Zolai word."""
        return self.repo.get_by_zolai_word(zolai_word, limit=limit)

    def get_by_english(self, english_word: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get alignments for an English word."""
        return self.repo.get_by_english_word(english_word, limit=limit)

    def get_aligned_pairs(self, limit: int = 1000) -> list[dict[str, Any]]:
        """Get unique aligned word pairs with frequencies."""
        return self.repo.get_aligned_pairs(limit=limit)

    def get_refs_for_word(self, zolai_word: str, limit: int = 100) -> list[str]:
        """Get verse refs where a Zolai word appears."""
        return self.repo.get_refs_for_word(zolai_word, limit=limit)

    def get_stats(self) -> dict[str, Any]:
        """Get alignment statistics."""
        return self.repo.get_alignment_stats()
