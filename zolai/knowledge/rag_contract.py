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
from ..data.database import get_manager
from ..learning.feedback import FeedbackStore


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
        self._db = None
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
        self._book_knowledge: dict[str, dict] | None = None
        self._feedback: FeedbackStore | None = None

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

        # Load vocab index (keyed on "headword")
        if self._vocab_index is None:
            path = bible_dir / "vocab_index_full.jsonl"
            self._vocab_index = self._load_jsonl_index(path, "headword", None)

        # Load per-book knowledge
        if self._book_knowledge is None:
            self._book_knowledge = {}
            book_dir = self.data_dir / "bible" / "book_knowledge"
            if book_dir.exists():
                for f in book_dir.glob("*.json"):
                    if f.name == "_master_summary.json":
                        continue
                    try:
                        data = json.loads(f.read_text())
                        code = data.get("book", f.stem)
                        self._book_knowledge[code.upper()] = data
                    except Exception:
                        continue

        # Detect database (preferred over JSONL when available)
        if self._db is None:
            db_path = self.data_dir.parent / "zolai.db"
            if db_path.exists():
                try:
                    self._db = get_manager(f"sqlite:///{db_path}")
                except Exception:
                    self._db = None

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
        if self._db:
            # Database path (fast, indexed)
            for word in words:
                # ZO→EN via database
                results = self._db.lookup_word(word)
                for r in results[:3]:
                    en_val = r.get("english", r.get("english_clean", ""))
                    if isinstance(en_val, list):
                        en_val = ", ".join(str(v) for v in en_val)
                    pack.vocabulary.append(
                        Evidence(
                            id=f"dict_zo_en:{word}",
                            text=f"{word} = {en_val}",
                            source="dictionary",
                            type="vocabulary",
                            confidence=0.95,
                            metadata={"direction": "zo_en", "db": True},
                        )
                    )
                # Vocab index via database
                vocab_results = self._db.get_vocab(word)
                for v in vocab_results[:2]:
                    pack.vocabulary.append(
                        Evidence(
                            id=f"vocab:{word}",
                            text=f"{word}: freq={v.get('frequency', 0)}, "
                            f"books={v.get('books', '')}",
                            source="vocab_index",
                            type="vocabulary",
                            confidence=0.85,
                            metadata={"db": True},
                        )
                    )
        else:
            # JSONL fallback (legacy path)
            for word in words:
                # ZO→EN
                if self._dict_zo_en and word in self._dict_zo_en:
                    entry = self._dict_zo_en[word]
                    en_val = entry.get(
                        "english_clean", entry.get("english", "")
                    )
                    if isinstance(en_val, list):
                        en_val = ", ".join(str(v) for v in en_val)
                    pack.vocabulary.append(
                        Evidence(
                            id=f"dict_zo_en:{word}",
                            text=f"{word} = {en_val}",
                            source="dictionary",
                            type="vocabulary",
                            confidence=0.95,
                            metadata={"direction": "zo_en", "entry": entry},
                        )
                    )
                # EN→ZO
                if self._dict_en_zo and word in self._dict_en_zo:
                    entry = self._dict_en_zo[word]
                    zo_val = entry.get(
                        "zolai", entry.get("headword", "")
                    )
                    if isinstance(zo_val, list):
                        zo_val = ", ".join(str(v) for v in zo_val)
                    pack.vocabulary.append(
                        Evidence(
                            id=f"dict_en_zo:{word}",
                            text=f"{word} = {zo_val}",
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

        # 1b. Feedback overrides — user corrections override dict results
        if self._feedback is None:
            self._feedback = FeedbackStore()
        for word in words:
            override = self._feedback.get_override(word)
            if override:
                pack.vocabulary.append(
                    Evidence(
                        id=f"feedback:{word}",
                        text=f"{word} = {override} (user correction)",
                        source="feedback",
                        type="vocabulary",
                        confidence=0.99,
                        metadata={
                            "direction": "feedback_override",
                            "corrected": override,
                        },
                    )
                )

        # 2. Grammar pattern matching
        if self._db:
            for word in words:
                results = self._db.get_grammar(word)
                for r in results[:3]:
                    pack.grammar.append(
                        Evidence(
                            id=r.get("id", ""),
                            text=f"Pattern: {r.get('pattern', '')} — "
                            f"{r.get('description', r.get('function', ''))}",
                            source="grammar",
                            type="grammar_pattern",
                            confidence=0.80,
                            metadata={"db": True},
                        )
                    )
        else:
            if self._grammar:
                for pattern in self._grammar[:200]:
                    pattern_text = pattern.get("pattern", "").lower()
                    if any(w in pattern_text or pattern_text in w
                           for w in words):
                        pack.grammar.append(
                            Evidence(
                                id=pattern.get("id", ""),
                                text=f"Pattern: "
                                f"{pattern.get('pattern', '')} — "
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
        if self._db:
            for word in words:
                results = self._db.match_phrase(word)
                for r in results[:3]:
                    pack.phrases.append(
                        Evidence(
                            id=f"phrase:{r.get('zolai', '')[:20]}",
                            text=f"{r.get('zolai', '')} = "
                            f"{r.get('english', r.get('meaning', ''))}",
                            source="phrases",
                            type="phrase",
                            confidence=0.80,
                            metadata={"db": True},
                        )
                    )
        else:
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

        # 4. Bible verse search (data uses zo_tdb77/zo_tedim2010/en_kJV)
        if self._db:
            for word in words:
                results = self._db.search_bible(word)
                for r in results[:3]:
                    pack.bible.append(
                        Evidence(
                            id=f"bible:{r.get('ref', '')}",
                            text=f"{r.get('ref', '')}: "
                            f"{r.get('zo_tdb77', '')} / "
                            f"{r.get('en_kJV', '')}",
                            source="bible",
                            type="verse",
                            confidence=0.75,
                            metadata={"db": True},
                        )
                    )
        else:
            if self._bible:
                for verse in self._bible[:500]:
                    zo = (verse.get("zo_tdb77") or verse.get("zo_tedim2010") or "") .lower()
                    en = (verse.get("en_kJV") or "") .lower()
                    if any(w in zo or w in en for w in words):
                        pack.bible.append(
                            Evidence(
                                id=f"bible:{verse.get('ref', '')}",
                                text=f"{verse.get('ref', '')}: "
                                f"{verse.get('zo_tdb77', '')} / "
                                f"{verse.get('en_kJV', '')}",
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

        # 6. Per-book knowledge (if a Bible book is referenced)
        BOOK_CODES: dict[str, str] = {
            "gen": "GENESIS", "exo": "EXODUS", "lev": "LEVITICUS",
            "num": "NUMBERS", "deu": "DEUTERONOMY", "jos": "JOSHUA",
            "jdg": "JUDGES", "rut": "RUTH", "1sa": "1SAMUEL",
            "2sa": "2SAMUEL", "1ki": "1KINGS", "2ki": "2KINGS",
            "1ch": "1CHRONICLES", "2ch": "2CHRONICLES",
            "ezr": "EZRA", "neh": "NEHEMIAH", "est": "ESTHER",
            "job": "JOB", "psa": "PSALMS", "pro": "PROVERBS",
            "ecc": "ECCLESIASTES", "sng": "SONGOFSOLOMON",
            "isa": "ISAIAH", "jer": "JEREMIAH",
            "lam": "LAMENTATIONS", "ezk": "EZEKIEL",
            "dan": "DANIEL", "hos": "HOSEA", "jol": "JOEL",
            "amo": "AMOS", "oba": "OBADIAH", "jon": "JONAH",
            "mic": "MICAH", "nah": "NAHUM", "hab": "HABAKKUK",
            "zep": "ZEPHANIAH", "hag": "HAGGAI",
            "zec": "ZECHARIAH", "mal": "MALACHI",
            "mat": "MATTHEW", "mrk": "MARK", "luk": "LUKE",
            "jhn": "JOHN", "act": "ACTS", "rom": "ROMANS",
            "1co": "1CORINTHIANS", "2co": "2CORINTHIANS",
            "gal": "GALATIANS", "eph": "EPHESIANS",
            "php": "PHILIPPIANS", "col": "COLOSSIANS",
            "1th": "1THESSALONIANS", "2th": "2THESSALONIANS",
            "1ti": "1TIMOTHY", "2ti": "2TIMOTHY", "tit": "TITUS",
            "phm": "PHILEMON", "heb": "HEBREWS", "jas": "JAMES",
            "1pe": "1PETER", "2pe": "2PETER", "1jn": "1JOHN",
            "2jn": "2JOHN", "3jn": "3JOHN", "jud": "JUDE",
            "rev": "REVELATION",
        }
        BOOK_NAMES_TO_CODES = {
            v.lower(): k for k, v in BOOK_CODES.items()
        }

        if self._book_knowledge:
            # Detect book reference in query
            detected_book: str | None = None
            for word in words:
                if word.upper() in self._book_knowledge:
                    detected_book = word.upper()
                    break
                if word in BOOK_NAMES_TO_CODES:
                    detected_book = BOOK_NAMES_TO_CODES[
                        word
                    ].upper()
                    break
            # Also check for patterns like "genesis" or "psalms"
            for name, code in BOOK_NAMES_TO_CODES.items():
                if name in query_lower:
                    detected_book = code.upper()
                    break

            if (
                detected_book
                and detected_book in self._book_knowledge
            ):
                book = self._book_knowledge[detected_book]
                # Add book overview
                pack.context.append(
                    Evidence(
                        id=f"book:{detected_book}",
                        text=f"{book.get('name', detected_book)}: "
                        f"{book.get('verses', 0)} verses, "
                        f"{book.get('unique_words', 0)} unique words",
                        source="book_knowledge",
                        type="book_overview",
                        confidence=0.95,
                        metadata={"book": book},
                    )
                )
                # Add top words for this book
                top_words = book.get(
                    "top_50_words", []
                )[:10]
                if top_words:
                    word_list = ", ".join(
                        f"{w}({c})"
                        for w, c in top_words
                    )
                    pack.context.append(
                        Evidence(
                            id=f"book_words:{detected_book}",
                            text=(
                                f"Top words in "
                                f"{book.get('name', detected_book)}: "
                                f"{word_list}"
                            ),
                            source="book_knowledge",
                            type="book_vocabulary",
                            confidence=0.90,
                            metadata={
                                "top_words": top_words
                            },
                        )
                    )
                # Add grammar patterns for this book
                patterns = book.get("grammar_patterns", {})
                if patterns:
                    pattern_list = ", ".join(
                        f"{k}: {v}"
                        for k, v in list(patterns.items())[:5]
                    )
                    pack.context.append(
                        Evidence(
                            id=(
                                f"book_grammar:{detected_book}"
                            ),
                            text=(
                                f"Grammar patterns in "
                                f"{book.get('name', detected_book)}: "
                                f"{pattern_list}"
                            ),
                            source="book_knowledge",
                            type="book_grammar",
                            confidence=0.85,
                            metadata={"patterns": patterns},
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
