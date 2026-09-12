"""Named Entity Recognition for Zolai using Gemini ensemble + rule-based fallback."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

# Zolai entity types
ENTITY_TYPES = ["PER", "LOC", "ORG", "DATE", "NUM", "BOOK"]

# Known person names from Bible (attested)
KNOWN_PERSONS = {
    "pasian", "topa", "israel", "adam", "eva", "noe", "abraham",
    "isak", "jakob", "jusef", "moses", "david", "solomon", "paul",
    "jesus", "christ", "kumpipa", "topa",
}

# Known location words
KNOWN_LOCATIONS = {
    "vantung", "leitung", "gam", "khua", "jerusalem", "bethlehem",
    "nazareth", "galilee", "judea", "sinai", "canaan", "egypt",
    "babylon", "rome", "antioch", "corinth", "ephesus",
}

# Known organization words
KNOWN_ORGANIZATIONS = {
    "biakinn", "ekklesia", "lawmna", "suhna", "kammal",
}

# Date patterns
DATE_PATTERNS = [
    "kumin", "kum", "ni", "nisaang", "chingkhat",
    "ciangin", "panin", "kuma", "tepen", "napi", "abeisa",
]

# Number words
NUMBER_WORDS = {
    "khat", "hnih", "ktil", "pali", "anga", "tauka", "sagat",
    "hari", "pakhat", "kua", "chang", "sang",
}

# Bible book abbreviations
BIBLE_BOOKS = {
    "GEN", "EXO", "LEV", "NUM", "DEU", "JOS", "JDG", "RUT",
    "1SA", "2SA", "1KI", "2KI", "1CH", "2CH", "EZR", "NEH",
    "EST", "JOB", "PSA", "PRO", "ECC", "SNG", "ISA", "JER",
    "LAM", "EZK", "DAN", "HOS", "JOE", "AMO", "OBA", "JON",
    "MIC", "NAM", "HAB", "ZEP", "HAG", "ZEC", "MAL", "MAT",
    "MRK", "LUK", "JHN", "ACT", "ROM", "1CO", "2CO", "GAL",
    "EPH", "PHP", "COL", "1TH", "2TH", "1TI", "2TI", "TIT",
    "PHM", "HEB", "JAS", "1PE", "2PE", "1JN", "2JN", "3JN",
    "JUD", "REV",
}


class ZolaiNER:
    """Named Entity Recognition for Zolai using Gemini ensemble."""

    def __init__(self, voter: Any = None, db_path: str | None = None):
        """Initialize NER.

        Args:
            voter: EnsembleVoter instance for Gemini integration (optional).
            db_path: Path to zolai.db for dictionary lookup.
        """
        self.voter = voter
        self._db_path = db_path
        self._dict_cache: dict[str, dict] | None = None
        self._bible_persons: set[str] | None = None

    def _ensure_loaded(self) -> None:
        """Lazy-load dictionary and Bible data."""
        if self._dict_cache is not None:
            return
        self._dict_cache = {}
        self._bible_persons = set()

        db_path = self._db_path or os.environ.get(
            "ZOLAI_DB_PATH", str(
                __import__("pathlib").Path(__file__).resolve().parent.parent.parent.parent
                / "data" / "zolai.db"
            )
        )
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            # Load dictionary headwords
            for row in conn.execute("SELECT zolai, pos FROM dictionary LIMIT 50000"):
                self._dict_cache[row["zolai"].lower()] = {"pos": row["pos"]}
            # Load Bible person names (words tagged as proper nouns in verses)
            for row in conn.execute(
                "SELECT zo_tdb77 FROM bible_verses LIMIT 10000"
            ):
                text = row["zo_tdb77"] or ""
                for word in text.split():
                    if word and word[0:1].isupper():
                        self._bible_persons.add(word.lower())
            conn.close()
            logger.info(
                "Loaded %d dict entries, %d Bible persons",
                len(self._dict_cache), len(self._bible_persons),
            )
        except Exception as e:
            logger.warning("Failed to load NER data from DB: %s", e)
            self._dict_cache = {}
            self._bible_persons = set()

    async def recognize(self, sentence: str) -> list[dict]:
        """Recognize named entities in Zolai sentence using Gemini ensemble.

        Args:
            sentence: Zolai text to analyze.

        Returns:
            List of entity dicts: [{"text": "...", "type": "PER", "start": 0, "end": 5}]
        """
        if self.voter is None:
            return self.recognize_rulebased(sentence)

        prompt = (
            "Identify named entities in this Zolai sentence. "
            "Entity types: PER (person), LOC (location), ORG (organization), "
            "DATE (date/time), NUM (number), BOOK (Bible book reference).\n"
            f"Sentence: {sentence}\n"
            'Output JSON: {"entities": [{"text": "...", "type": "...", '
            '"start": 0, "end": 5}]}'
        )
        try:
            raw = await self.voter.vote(prompt)
            result = json.loads(raw) if isinstance(raw, str) else raw
            entities = result.get("result", result).get("entities", [])
            # Validate entity types
            return [
                e for e in entities
                if e.get("type") in ENTITY_TYPES
            ]
        except Exception as e:
            logger.warning("Gemini NER failed, falling back to rules: %s", e)
            return self.recognize_rulebased(sentence)

    def recognize_rulebased(self, sentence: str) -> list[dict]:
        """Rule-based NER using dictionary + Bible attestation.

        Args:
            sentence: Zolai text to analyze.

        Returns:
            List of entity dicts.
        """
        self._ensure_loaded()
        entities = []
        words = sentence.split()
        offset = 0

        for word in words:
            word_clean = word.strip(".,;:!?\"'()[]{}")
            word_lower = word_clean.lower()
            start = sentence.find(word, offset)
            end = start + len(word_clean)
            entity_type = None

            # Check Bible book abbreviations
            if word_clean.upper() in BIBLE_BOOKS:
                entity_type = "BOOK"
            # Check date patterns
            elif word_lower in DATE_PATTERNS or word_lower.endswith(("kum", "ni")):
                entity_type = "DATE"
            # Check number words
            elif word_lower in NUMBER_WORDS:
                entity_type = "NUM"
            # Check known person names
            elif word_lower in KNOWN_PERSONS:
                entity_type = "PER"
            # Check Bible-attested persons (capitalized in original)
            elif word_clean and word_clean[0].isupper() and word_lower in (self._bible_persons or set()):
                entity_type = "PER"
            # Check known locations
            elif word_lower in KNOWN_LOCATIONS:
                entity_type = "LOC"
            # Check known organizations
            elif word_lower in KNOWN_ORGANIZATIONS:
                entity_type = "ORG"
            # Check for proper nouns (capitalized, not start of sentence)
            elif (
                word_clean
                and word_clean[0].isupper()
                and offset > 0
                and word_lower not in {"ka", "na", "a", "i", "ki", "te", "leh", "tua"}
            ):
                # Could be a proper noun
                if word_lower in self._dict_cache:
                    pos = self._dict_cache[word_lower].get("pos", "")
                    if pos in ("n", "proper", "name"):
                        entity_type = "PER"

            if entity_type:
                entities.append({
                    "text": word_clean,
                    "type": entity_type,
                    "start": start,
                    "end": end,
                })

            offset = start + len(word) if start >= 0 else offset + len(word) + 1

        return entities


# Module-level singleton
_ner_instance: ZolaiNER | None = None


def get_ner(voter: Any = None, db_path: str | None = None) -> ZolaiNER:
    """Get or create the singleton NER instance."""
    global _ner_instance
    if _ner_instance is None:
        _ner_instance = ZolaiNER(voter=voter, db_path=db_path)
    return _ner_instance
