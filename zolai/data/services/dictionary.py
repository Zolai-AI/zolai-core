"""Dictionary service for CRUD, validation, and correction workflow."""

from __future__ import annotations

from typing import Any

from sqlalchemy.engine import Engine

from ..repositories import (
    DictionaryEnZoRepository,
    DictionaryRepository,
)


class DictionaryService:
    """Service for Zolai→English dictionary operations.

    Enforces business rules:
    - Zolai headword must be unique
    - ZVS 2018 orthography compliance
    - Source tracking for provenance
    - Myanmar/English clean field maintenance
    """

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
        self.repo = DictionaryRepository(engine)
        self.engine = engine

    def validate_zvs_2018(self, zolai: str) -> list[str]:
        """Check Zolai word against ZVS 2018 forbidden forms.

        Returns list of violations found.
        """
        violations = []
        zolai_lower = zolai.lower()
        for forbidden, correct in self.FORBIDDEN_FORMS.items():
            if forbidden in zolai_lower:
                violations.append(f"Forbidden form '{forbidden}' found, should be '{correct}'")
        return violations

    def add_word(
        self,
        zolai: str,
        english: str,
        myanmar: str = "",
        pos: str = "",
        source: str = "manual",
        user: str = "system",
    ) -> dict[str, Any]:
        """Add a new dictionary entry with validation.

        Args:
            zolai: Zolai headword
            english: English translation
            myanmar: Myanmar translation (optional)
            pos: Part of speech (optional)
            source: Source identifier
            user: User performing the action

        Returns:
            Dict with 'success', 'id', and any 'errors'.
        """
        errors = []

        # Validate ZVS 2018
        zvs_errors = self.validate_zvs_2018(zolai)
        if zvs_errors:
            errors.extend(zvs_errors)

        # Check for existing
        existing = self.repo.lookup_exact(zolai)
        if existing:
            errors.append(f"Word '{zolai}' already exists (ID: {existing[0]['id']})")

        if not zolai.strip():
            errors.append("Zolai headword cannot be empty")

        if not english.strip():
            errors.append("English translation cannot be empty")

        if errors:
            return {"success": False, "errors": errors}

        # Create entry
        data = {
            "zolai": zolai.strip(),
            "english": english.strip(),
            "english_clean": english.strip(),
            "myanmar": myanmar.strip() if myanmar else "",
            "pos": pos.strip() if pos else "",
            "source": source.strip(),
        }

        entity_id = self.repo.create(data, user=user)
        return {"success": True, "id": entity_id, "data": data}

    def update_word(
        self,
        zolai: str,
        english: str | None = None,
        english_clean: str | None = None,
        myanmar: str | None = None,
        pos: str | None = None,
        source: str | None = None,
        user: str = "system",
    ) -> dict[str, Any]:
        """Update an existing dictionary entry.

        Args:
            zolai: Zolai headword to update
            english: New English translation
            english_clean: New clean English
            myanmar: New Myanmar translation
            pos: New part of speech
            source: New source
            user: User performing the action

        Returns:
            Dict with 'success', 'id', and any 'errors'.
        """
        errors = []
        entry = self.repo.lookup_exact(zolai)
        if not entry:
            return {"success": False, "errors": [f"Word '{zolai}' not found"]}

        entity_id = entry[0]["id"]
        updates = {}

        if english is not None:
            if not english.strip():
                errors.append("English translation cannot be empty")
            else:
                updates["english"] = english.strip()
                updates["english_clean"] = english_clean.strip() if english_clean else english.strip()

        if english_clean is not None:
            updates["english_clean"] = english_clean.strip()

        if myanmar is not None:
            updates["myanmar"] = myanmar.strip()

        if pos is not None:
            updates["pos"] = pos.strip()

        if source is not None:
            updates["source"] = source.strip()

        # Validate ZVS 2018 for any text fields being updated
        for field, value in updates.items():
            if isinstance(value, str):
                zvs_errors = self.validate_zvs_2018(value)
                if zvs_errors:
                    errors.extend([f"{field}: {e}" for e in zvs_errors])

        if errors:
            return {"success": False, "errors": errors}

        if not updates:
            return {"success": False, "errors": ["No fields to update"]}

        success = self.repo.update(entity_id, updates, user=user)
        return {"success": success, "id": entity_id}

    def delete_word(self, zolai: str, user: str = "system") -> dict[str, Any]:
        """Soft delete a dictionary entry."""
        entry = self.repo.lookup_exact(zolai)
        if not entry:
            return {"success": False, "errors": [f"Word '{zolai}' not found"]}

        entity_id = entry[0]["id"]
        success = self.repo.soft_delete(entity_id, deleted_by=user, reason="manual_delete")
        return {"success": success, "id": entity_id}

    def search(
        self,
        query: str,
        search_myanmar: bool = True,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search dictionary across Zolai, English, and Myanmar."""
        if search_myanmar:
            return self.repo.search_all(query, limit=limit)
        return self.repo.search_all(query, limit=limit)

    def get_missing_myanmar(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get entries missing Myanmar translations."""
        return self.repo.get_missing_myanmar(limit=limit)

    def get_missing_english_clean(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get entries missing clean English translations."""
        return self.repo.get_missing_english_clean(limit=limit)

    def fill_myanmar_batch(
        self, translations: dict[str, str], user: str = "gemini"
    ) -> dict[str, Any]:
        """Batch update Myanmar translations.

        Args:
            translations: Dict mapping zolai -> myanmar translation
            user: User identifier for audit

        Returns:
            Dict with counts of updated, failed, and errors.
        """
        updated = 0
        failed = 0
        errors = []

        for zolai, myanmar in translations.items():
            result = self.update_word(zolai, myanmar=myanmar, user=user)
            if result["success"]:
                updated += 1
            else:
                failed += 1
                errors.extend(result.get("errors", []))

        return {
            "updated": updated,
            "failed": failed,
            "errors": errors[:10],  # Limit error list
        }

    def get_stats(self) -> dict[str, Any]:
        """Get dictionary statistics."""
        with self.engine.connect() as conn:
            total = conn.execute(
                self.engine.dialect.execute(self.engine, "SELECT COUNT(*) FROM dictionary")
            ).scalar()

            sources = self.repo.count_by_source()

        return {
            "total_entries": total or 0,
            "by_source": sources,
        }


class DictionaryEnZoService:
    """Service for English→Zolai dictionary operations."""

    def __init__(self, engine: Engine) -> None:
        self.repo = DictionaryEnZoRepository(engine)
        self.engine = engine

    def add_entry(
        self,
        headword: str,
        translations: list[str],
        pos: str = "",
        source: str = "manual",
        user: str = "system",
    ) -> dict[str, Any]:
        """Add a new English→Zolai entry."""
        errors = []

        if not headword.strip():
            errors.append("Headword cannot be empty")

        if not translations:
            errors.append("At least one translation required")

        existing = self.repo.lookup_exact(headword)
        if existing:
            errors.append(f"Headword '{headword}' already exists")

        if errors:
            return {"success": False, "errors": errors}

        import json
        data = {
            "headword": headword.strip(),
            "translations": json.dumps(translations, ensure_ascii=False),
            "translations_clean": translations[0] if translations else "",
            "pos": pos.strip() if pos else "",
            "source": source.strip(),
        }

        entity_id = self.repo.create(data, user=user)
        return {"success": True, "id": entity_id, "data": data}

    def update_entry(
        self,
        headword: str,
        translations: list[str] | None = None,
        translations_clean: str | None = None,
        pos: str | None = None,
        myanmar: str | None = None,
        user: str = "system",
    ) -> dict[str, Any]:
        """Update an English→Zolai entry."""
        import json

        entry = self.repo.lookup_exact(headword)
        if not entry:
            return {"success": False, "errors": [f"Headword '{headword}' not found"]}

        entity_id = entry[0]["id"]
        updates = {}

        if translations is not None:
            updates["translations"] = json.dumps(translations, ensure_ascii=False)
            updates["translations_clean"] = translations_clean or (translations[0] if translations else "")

        if translations_clean is not None:
            updates["translations_clean"] = translations_clean

        if pos is not None:
            updates["pos"] = pos.strip()

        if myanmar is not None:
            updates["myanmar"] = myanmar.strip()

        if not updates:
            return {"success": False, "errors": ["No fields to update"]}

        success = self.repo.update(entity_id, updates, user=user)
        return {"success": success, "id": entity_id}

    def delete_entry(self, headword: str, user: str = "system") -> dict[str, Any]:
        """Soft delete an English→Zolai entry."""
        entry = self.repo.lookup_exact(headword)
        if not entry:
            return {"success": False, "errors": [f"Headword '{headword}' not found"]}

        entity_id = entry[0]["id"]
        success = self.repo.soft_delete(entity_id, deleted_by=user, reason="manual_delete")
        return {"success": success, "id": entity_id}

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search English→Zolai dictionary."""
        return self.repo.search_all(query, limit=limit)

    def get_missing_myanmar(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get entries missing Myanmar translations."""
        return self.repo.get_missing_myanmar(limit=limit)

    def get_stats(self) -> dict[str, Any]:
        """Get English→Zolai dictionary statistics."""
        with self.engine.connect() as conn:
            total = conn.execute(
                text("SELECT COUNT(*) FROM dictionary_en_zo")
            ).scalar()

        return {
            "total_entries": total or 0,
        }
