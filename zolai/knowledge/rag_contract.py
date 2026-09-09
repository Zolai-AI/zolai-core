"""Structured RAG contract for Zolai knowledge retrieval.

Provides layered retrieval across vocabulary, grammar, phrases, Bible,
and ZVS sources with evidence ranking and confidence scoring.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import config


@dataclass
class Evidence:
    """A single piece of evidence from a knowledge source."""

    id: str
    text: str
    source: str  # "dictionary", "grammar", "phrase", "bible", "zvs", "context"
    type: str  # "vocabulary", "grammar_pattern", "phrase", "verse", "forbidden_form", "word_usage"
    confidence: float  # 0.0 to 1.0
    metadata: dict = field(default_factory=dict)


@dataclass
class EvidencePack:
    """Structured retrieval result with categorized evidence."""

    query: str
    vocabulary: list[Evidence] = field(default_factory=list)
    grammar: list[Evidence] = field(default_factory=list)
    phrases: list[Evidence] = field(default_factory=list)
    bible: list[Evidence] = field(default_factory=list)
    zvs: list[Evidence] = field(default_factory=list)
    context: list[Evidence] = field(default_factory=list)

    def total(self) -> int:
        return (
            len(self.vocabulary)
            + len(self.grammar)
            + len(self.phrases)
            + len(self.bible)
            + len(self.zvs)
            + len(self.context)
        )

    def to_prompt(self) -> str:
        """Format evidence pack as RAG context for AI prompt injection."""
        blocks = []

        if self.vocabulary:
            blocks.append("## Vocabulary Evidence")
            for e in self.vocabulary[:5]:
                blocks.append(f"- {e.text} (confidence: {e.confidence:.2f})")

        if self.grammar:
            blocks.append("\n## Grammar Evidence")
            for e in self.grammar[:3]:
                blocks.append(f"- {e.text}")

        if self.phrases:
            blocks.append("\n## Phrase Evidence")
            for e in self.phrases[:3]:
                blocks.append(f"- {e.text}")

        if self.bible:
            blocks.append("\n## Bible Examples")
            for e in self.bible[:3]:
                blocks.append(f"- {e.text}")

        if self.zvs:
            blocks.append("\n## ZVS Compliance")
            for e in self.zvs:
                blocks.append(f"- {e.text}")

        return "\n".join(blocks)


class ZolaiRAG:
    """Layered RAG retriever for Zolai knowledge.

    Usage:
        rag = ZolaiRAG()
        pack = rag.retrieve("How do you say 'I don't go' in Zolai?")
        print(pack.to_prompt())
    """

    def __init__(self, data_dir: Path | None = None):
        if data_dir is None:
            data_dir = config.paths.data
        self.data_dir = data_dir
        self._dict_zo_en: dict[str, dict] | None = None
        self._dict_en_zo: dict[str, dict] | None = None
        self._grammar: list[dict] | None = None
        self._phrases: list[dict] | None = None
        self._bible: list[dict] | None = None
        self._vocab_index: dict[str, dict] | None = None
        self._zvs_forms: dict[str, str] = {
            "pathian": "pasian",
            "ram": "gam",
            "fapa": "tapa",
            "bawipa": "topa",
            "siangpahrang": "kumpipa",
            "cu": "tua",
            "cun": "tua",
        }

    def _ensure_loaded(self) -> None:
        """Lazy-load all data sources on first query."""
        dict_dir = self.data_dir / "dictionary" / "processed"
        bible_dir = self.data_dir / "bible"

        # Load ZO→EN dictionary
        if self._dict_zo_en is None:
            path = dict_dir / "dict_zo_en_master_v1.jsonl"
            self._dict_zo_en = self._load_jsonl_index(path, "zolai", "word")

        # Load EN→ZO dictionary
        if self._dict_en_zo is None:
            path = dict_dir / "dict_canonical_clean.jsonl"
            self._dict_en_zo = self._load_jsonl_index(path, "english", "word")

        # Load grammar patterns
        if self._grammar is None:
            path = bible_dir / "grammar_patterns_v2.jsonl"
            self._grammar = self._load_jsonl_list(path)

        # Load phrases
        if self._phrases is None:
            path = bible_dir / "phrases_v1.jsonl"
            self._phrases = self._load_jsonl_list(path)

        # Load Bible verses
        if self._bible is None:
            path = bible_dir / "parallel_corpus_v1.jsonl"
            self._bible = self._load_jsonl_list(path)

        # Load vocab index
        if self._vocab_index is None:
            path = bible_dir / "vocab_index_full.jsonl"
            self._vocab_index = self._load_jsonl_index(path, "word", None)

    @staticmethod
    def _load_jsonl_index(path: Path, key: str, fallback: str | None) -> dict[str, dict]:
        """Load JSONL into a dict keyed by `key` field."""
        index: dict[str, dict] = {}
        if not path.exists():
            return index
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                word = entry.get(key, "")
                if not word and fallback:
                    word = entry.get(fallback, "")
                # Handle list fields (e.g. translations: ["say"])
                if isinstance(word, list):
                    word = word[0] if word else ""
                if isinstance(word, str) and word:
                    index[word.lower()] = entry
        return index

    @staticmethod
    def _load_jsonl_list(path: Path) -> list[dict]:
        """Load JSONL into a list."""
        items: list[dict] = []
        if not path.exists():
            return items
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return items

    def retrieve(self, query: str, top_k: int = 10) -> EvidencePack:
        """Retrieve structured evidence for a query.

        Args:
            query: User question or sentence to analyze
            top_k: Maximum results per category

        Returns:
            EvidencePack with categorized evidence
        """
        self._ensure_loaded()
        pack = EvidencePack(query=query)
        query_lower = query.lower()
        words = query_lower.split()

        # 1. Vocabulary lookup
        for word in words:
            # ZO→EN
            if self._dict_zo_en and word in self._dict_zo_en:
                entry = self._dict_zo_en[word]
                pack.vocabulary.append(
                    Evidence(
                        id=f"dict_zo_en:{word}",
                        text=f"{word} = {entry.get('english', entry.get('meaning', ''))}",
                        source="dictionary",
                        type="vocabulary",
                        confidence=0.95,
                        metadata={"direction": "zo_en", "entry": entry},
                    )
                )
            # EN→ZO
            if self._dict_en_zo and word in self._dict_en_zo:
                entry = self._dict_en_zo[word]
                pack.vocabulary.append(
                    Evidence(
                        id=f"dict_en_zo:{word}",
                        text=f"{word} = {entry.get('zolai', entry.get('translation', ''))}",
                        source="dictionary",
                        type="vocabulary",
                        confidence=0.95,
                        metadata={"direction": "en_zo", "entry": entry},
                    )
                )
            # Vocab index (frequency, examples)
            if self._vocab_index and word in self._vocab_index:
                entry = self._vocab_index[word]
                pack.vocabulary.append(
                    Evidence(
                        id=f"vocab:{word}",
                        text=f"{word}: freq={entry.get('frequency', 0)}, "
                        f"books={entry.get('books', '')}",
                        source="vocab_index",
                        type="vocabulary",
                        confidence=0.85,
                        metadata={"entry": entry},
                    )
                )

        # 2. Grammar pattern matching
        if self._grammar:
            for pattern in self._grammar[:200]:
                pattern_text = pattern.get("pattern", "").lower()
                if any(w in pattern_text or pattern_text in w for w in words):
                    pack.grammar.append(
                        Evidence(
                            id=pattern.get("id", ""),
                            text=f"Pattern: {pattern.get('pattern', '')} — "
                            f"{pattern.get('description', pattern.get('function', ''))}",
                            source="grammar",
                            type="grammar_pattern",
                            confidence=0.80,
                            metadata={"pattern": pattern},
                        )
                    )
                    if len(pack.grammar) >= 3:
                        break

        # 3. Phrase matching
        if self._phrases:
            for phrase_entry in self._phrases[:100]:
                phrase_text = phrase_entry.get(
                    "phrase", phrase_entry.get("zolai", "")
                ).lower()
                if any(w in phrase_text for w in words):
                    pack.phrases.append(
                        Evidence(
                            id=f"phrase:{phrase_text[:20]}",
                            text=f"{phrase_text} = "
                            f"{phrase_entry.get('english', phrase_entry.get('meaning', ''))}",
                            source="phrases",
                            type="phrase",
                            confidence=0.80,
                            metadata={"phrase": phrase_entry},
                        )
                    )
                    if len(pack.phrases) >= 3:
                        break

        # 4. Bible verse search
        if self._bible:
            for verse in self._bible[:500]:
                zo = (verse.get("zo", "") or "").lower()
                en = (verse.get("en", "") or "").lower()
                if any(w in zo or w in en for w in words):
                    pack.bible.append(
                        Evidence(
                            id=f"bible:{verse.get('ref', '')}",
                            text=f"{verse.get('ref', '')}: "
                            f"{verse.get('zo', '')} / {verse.get('en', '')}",
                            source="bible",
                            type="verse",
                            confidence=0.75,
                            metadata={"verse": verse},
                        )
                    )
                    if len(pack.bible) >= 3:
                        break

        # 5. ZVS compliance check
        for word in words:
            if word in self._zvs_forms:
                pack.zvs.append(
                    Evidence(
                        id=f"zvs:{word}",
                        text=f"FORBIDDEN: '{word}' → use "
                        f"'{self._zvs_forms[word]}' instead",
                        source="zvs",
                        type="forbidden_form",
                        confidence=1.0,
                        metadata={
                            "forbidden": word,
                            "correct": self._zvs_forms[word],
                        },
                    )
                )

        return pack

    def validate_sentence(self, sentence: str) -> dict[str, Any]:
        """Validate a Zolai sentence against grammar and ZVS rules.

        Returns:
            dict with "valid", "issues", "suggestions"
        """
        self._ensure_loaded()
        issues: list[str] = []
        suggestions: list[str] = []
        words = sentence.lower().split()

        # Check ZVS forbidden forms
        for word in words:
            if word in self._zvs_forms:
                issues.append(
                    f"FORBIDDEN: '{word}' → use '{self._zvs_forms[word]}'"
                )
                suggestions.append(
                    f"Replace '{word}' with '{self._zvs_forms[word]}'"
                )

        # Check if ends with expected particle (basic SOV check)
        valid_endings = {"hi", "hiam", "diam", "leh", "a", "lo", "ding", "sak"}
        if words and words[-1] not in valid_endings:
            issues.append(
                f"Sentence doesn't end with expected particle "
                f"(got '{words[-1]}')"
            )

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "suggestions": suggestions,
        }


# Convenience function
def retrieve(query: str, top_k: int = 10) -> EvidencePack:
    """Quick RAG retrieval for a query."""
    rag = ZolaiRAG()
    return rag.retrieve(query, top_k=top_k)
