"""
Bible Corpus Expander - Expands and enriches the Bible corpus for learning.

Every derived artifact (parallel pairs, sentence pairs, word alignments and
chapter expansions) is written to SQLite. Nothing is written to JSON/JSONL or
any other file.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.shared.clock import stamp

from ...data.repositories import get_engine

logger = logging.getLogger(__name__)

#: Canonical Protestant book order, used for deterministic expansion runs.
BOOK_ORDER = [
    "GEN", "EXO", "LEV", "NUM", "DEU", "JOS", "JDG", "RUT", "1SA", "2SA",
    "1KI", "2KI", "1CH", "2CH", "EZR", "NEH", "EST", "JOB", "PSA", "PRO",
    "ECC", "SNG", "ISA", "JER", "LAM", "EZK", "DAN", "HOS", "JOL", "AMO",
    "OBA", "JON", "MIC", "NAM", "HAB", "ZEP", "HAG", "ZEC", "MAL", "MAT",
    "MRK", "LUK", "JHN", "ACT", "ROM", "1CO", "2CO", "GAL", "EPH", "PHP",
    "COL", "1TH", "2TH", "1TI", "2TI", "TIT", "PHM", "HEB", "JAS", "1PE",
    "2PE", "1JN", "2JN", "3JN", "JUD", "REV",
]

_STOP_WORDS = frozenset({
    "the", "and", "a", "an", "in", "on", "at", "to", "for", "of", "with",
    "by", "from", "as", "is", "was", "are", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "can", "this", "that", "these",
    "those", "i", "you", "he", "she", "it", "we", "they", "my", "your",
    "his", "her", "its", "our", "their", "what", "which", "who", "whom",
    "where", "when", "why", "how", "not", "no", "yes", "so", "if", "then",
})


@dataclass
class VerseExpansion:
    """Expanded verse with additional context."""

    ref: str
    book: str
    chapter: int
    verse: int
    zo_tdb77: str
    zo_tedim2010: str | None
    en_kJV: str
    context_before: list = field(default_factory=list)
    context_after: list = field(default_factory=list)
    keywords: list = field(default_factory=list)
    grammar_features: dict = field(default_factory=dict)


class VerseExpander:
    """Expands individual verses with additional context."""

    def __init__(self, engine: Engine = None):
        self.engine = engine or get_engine()

    def expand_verse(self, ref: str, context_window: int = 2) -> VerseExpansion | None:
        """Expand a single verse with surrounding context."""
        with self.engine.connect() as conn:
            # Get the verse
            result = conn.execute(
                text("""
                    SELECT ref, book, chapter, verse, zo_tdb77, zo_tedim2010, en_kJV
                    FROM bible_verses
                    WHERE ref = :ref
                """),
                {"ref": ref},
            ).fetchone()

            if not result:
                return None

            verse = VerseExpansion(
                ref=result.ref,
                book=result.book,
                chapter=result.chapter,
                verse=result.verse,
                zo_tdb77=result.zo_tdb77 or "",
                zo_tedim2010=result.zo_tedim2010,
                en_kJV=result.en_kJV or "",
            )

            # Get context
            context = self._get_context(conn, result.book, result.chapter, result.verse, context_window)
            verse.context_before = context["before"]
            verse.context_after = context["after"]

            # Extract keywords
            verse.keywords = self._extract_keywords(result.zo_tdb77 or "")

            # Extract grammar features
            verse.grammar_features = self._extract_grammar_features(result.zo_tdb77 or "")

            return verse

    def _get_context(self, conn, book: str, chapter: int, verse: int, window: int) -> dict:
        """Get surrounding verses."""
        before = conn.execute(
            text("""
                SELECT ref, zo_tdb77, en_kJV
                FROM bible_verses
                WHERE book = :book AND chapter = :chapter AND verse BETWEEN :start AND :end
                ORDER BY verse
            """),
            {
                "book": book,
                "chapter": chapter,
                "start": max(1, verse - window),
                "end": verse - 1,
            },
        ).fetchall()

        after = conn.execute(
            text("""
                SELECT ref, zo_tdb77, en_kJV
                FROM bible_verses
                WHERE book = :book AND chapter = :chapter AND verse BETWEEN :start AND :end
                ORDER BY verse
            """),
            {
                "book": book,
                "chapter": chapter,
                "start": verse + 1,
                "end": verse + window,
            },
        ).fetchall()

        return {
            "before": [{"ref": r.ref, "zo": r.zo_tdb77, "en": r.en_kJV} for r in before],
            "after": [{"ref": r.ref, "zo": r.zo_tdb77, "en": r.en_kJV} for r in after],
        }

    def _extract_keywords(self, text_value: str) -> list:
        """Extract key terms from Zolai text."""
        words = re.findall(r"\b[a-zA-Z]{3,}\b", text_value.lower())
        keywords = [w for w in words if w not in _STOP_WORDS and len(w) > 3]
        freq = Counter(keywords)
        return [word for word, _ in freq.most_common(10)]

    def _extract_grammar_features(self, text_value: str) -> dict:
        """Extract grammar features from text."""
        padded = f" {text_value} "
        lower = text_value.lower()

        return {
            # Ergative marker
            "has_ergative": " in " in padded,
            # Negation
            "has_negation_kei": " kei " in padded,
            # Interrogative marker
            "is_question": " hiam " in padded or lower.strip().endswith("hiam"),
            # Quotative
            "has_quotative": " ci hi " in padded,
            "word_count": len(text_value.split()),
            # Plural markers
            "has_plural": any(m in lower for m in ("uh", "hei", "te")),
        }

    def expand_passage(self, book: str, chapter: int, start_verse: int, end_verse: int) -> list:
        """Expand a range of verses."""
        with self.engine.connect() as conn:
            refs = conn.execute(
                text("""
                    SELECT ref FROM bible_verses
                    WHERE book = :book AND chapter = :chapter
                    AND verse BETWEEN :start AND :end
                    ORDER BY verse
                """),
                {"book": book, "chapter": chapter, "start": start_verse, "end": end_verse},
            ).fetchall()

        expansions = []
        for row in refs:
            exp = self.expand_verse(row.ref)
            if exp:
                expansions.append(exp)
        return expansions

    def get_chapter_expansions(self, book: str, chapter: int) -> list:
        """Get all expansions for a chapter."""
        with self.engine.connect() as conn:
            verses = conn.execute(
                text("""
                    SELECT ref FROM bible_verses
                    WHERE book = :book AND chapter = :chapter
                    ORDER BY verse
                """),
                {"book": book, "chapter": chapter},
            ).fetchall()

        expansions = []
        for verse in verses:
            exp = self.expand_verse(verse.ref)
            if exp:
                expansions.append(exp)
        return expansions


class ParallelCorpusBuilder:
    """Builds and stores parallel corpora from Bible translations."""

    def __init__(self, engine: Engine = None):
        self.engine = engine or get_engine()
        self.ensure_schema()

    def ensure_schema(self) -> None:
        """Create the corpus tables if missing (additive, idempotent)."""
        with self.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS corpus_parallel_pairs (
                    pair_id TEXT PRIMARY KEY,
                    ref TEXT NOT NULL,
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    source_lang TEXT NOT NULL,
                    target_lang TEXT NOT NULL,
                    pair_type TEXT NOT NULL,
                    seq INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """))
            conn.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS ux_corpus_parallel_pair
                ON corpus_parallel_pairs (pair_type, ref, seq)
            """))

    def store_pairs(self, pairs: list[dict], pair_type: str = "verse") -> int:
        """Persist parallel pairs to SQLite.

        Idempotent: a pair already present under the same
        ``(pair_type, ref, seq)`` key is left untouched.
        """
        if not pairs:
            return 0

        now = stamp()
        rows = []
        for seq, pair in enumerate(pairs):
            ref = pair.get("ref") or ""
            rows.append({
                "pair_id": f"{pair_type}:{ref}:{seq}",
                "ref": ref,
                "source": pair.get("source", ""),
                "target": pair.get("target", ""),
                "source_lang": pair.get("source_lang", "zo"),
                "target_lang": pair.get("target_lang", "en"),
                "pair_type": pair_type,
                "seq": seq,
                "created_at": now,
            })

        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT OR IGNORE INTO corpus_parallel_pairs
                        (pair_id, ref, source, target, source_lang, target_lang,
                         pair_type, seq, created_at)
                    VALUES (:pair_id, :ref, :source, :target, :source_lang,
                            :target_lang, :pair_type, :seq, :created_at)
                """),
                rows,
            )
        return len(rows)

    def count_pairs(self, pair_type: str = None) -> int:
        """Count stored pairs (optionally for one pair type)."""
        sql = "SELECT COUNT(*) FROM corpus_parallel_pairs"
        params: dict = {}
        if pair_type:
            sql += " WHERE pair_type = :pair_type"
            params["pair_type"] = pair_type
        with self.engine.connect() as conn:
            return conn.execute(text(sql), params).scalar()

    def get_stored_pair(self, pair_id: str) -> dict | None:
        """Read one stored pair back."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT pair_id, ref, source, target, source_lang, target_lang, pair_type
                    FROM corpus_parallel_pairs WHERE pair_id = :pair_id
                """),
                {"pair_id": pair_id},
            ).fetchone()
            return dict(row._mapping) if row else None

    def build_parallel_pairs(
        self,
        source_lang: str = "zo",
        target_lang: str = "en",
        min_length: int = 5,
        max_length: int = 200,
    ) -> list:
        """Build parallel sentence pairs from Bible."""
        pairs = []

        with self.engine.connect() as conn:
            results = conn.execute(
                text("""
                    SELECT zo_tdb77, en_kJV, ref
                    FROM bible_verses
                    WHERE zo_tdb77 IS NOT NULL AND en_kJV IS NOT NULL
                    AND LENGTH(zo_tdb77) BETWEEN :min_len AND :max_len
                    AND LENGTH(en_kJV) BETWEEN :min_len AND :max_len
                    ORDER BY ref
                """),
                {"min_len": min_length, "max_len": max_length},
            ).fetchall()

        for row in results:
            pairs.append({
                "source": row.zo_tdb77.strip(),
                "target": row.en_kJV.strip(),
                "ref": row.ref,
                "source_lang": source_lang,
                "target_lang": target_lang,
            })

        return pairs

    def build_verse_level_pairs(self) -> list:
        """Build verse-level parallel pairs."""
        return self.build_parallel_pairs()

    def build_sentence_pairs(self) -> list:
        """Build sentence-level parallel pairs by splitting verses."""
        pairs = []

        with self.engine.connect() as conn:
            results = conn.execute(
                text("""
                    SELECT zo_tdb77, en_kJV, ref
                    FROM bible_verses
                    WHERE zo_tdb77 IS NOT NULL AND en_kJV IS NOT NULL
                    ORDER BY ref
                """)
            ).fetchall()

        for row in results:
            zo = row.zo_tdb77
            en = row.en_kJV

            # Split into sentences (rough)
            zo_sentences = re.split(r"[.!?।]", zo)
            en_sentences = re.split(r"[.!?]", en)

            # Simple alignment - pair by position
            for zo_s, en_s in zip(zo_sentences, en_sentences):
                zo_s = zo_s.strip()
                en_s = en_s.strip()
                if len(zo_s) > 5 and len(en_s) > 5:
                    pairs.append({
                        "source": zo_s,
                        "target": en_s,
                        "ref": row.ref,
                        "source_lang": "zo",
                        "target_lang": "en",
                        "type": "sentence",
                    })

        return pairs

    def build_word_aligned_pairs(self, limit: int = 0) -> list:
        """Build word-aligned pairs from the stored alignment table."""
        sql = """
            SELECT ref, zolai_word, english_word, position
            FROM word_alignments
            WHERE zolai_word IS NOT NULL AND english_word IS NOT NULL
            AND zolai_word != '' AND english_word != ''
            ORDER BY ref, position
        """
        params: dict = {}
        if limit:
            sql += " LIMIT :limit"
            params["limit"] = limit

        pairs = []
        with self.engine.connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()

        for row in rows:
            pairs.append({
                "source": row.zolai_word,
                "target": row.english_word,
                "ref": row.ref or "",
                "position": row.position,
                "source_lang": "zo",
                "target_lang": "en",
            })
        return pairs

    def get_stats(self) -> dict:
        """Get corpus statistics."""
        with self.engine.connect() as conn:
            total = conn.execute(text("""
                SELECT COUNT(*) FROM bible_verses
                WHERE zo_tdb77 IS NOT NULL AND en_kJV IS NOT NULL
            """)).scalar()
            with_zo = conn.execute(text("""
                SELECT COUNT(*) FROM bible_verses WHERE zo_tdb77 IS NOT NULL
            """)).scalar()
            with_en = conn.execute(text("""
                SELECT COUNT(*) FROM bible_verses WHERE en_kJV IS NOT NULL
            """)).scalar()
            stored = conn.execute(text("SELECT COUNT(*) FROM corpus_parallel_pairs")).scalar()

        return {
            "total_verses": total,
            "with_zo": with_zo,
            "with_en": with_en,
            "parallel": min(with_zo, with_en),
            "stored_pairs": stored,
        }


class BibleCorpusExpander:
    """Main entry point for Bible corpus expansion (SQLite-backed)."""

    def __init__(self, engine: Engine = None):
        self.engine = engine or get_engine()
        self.verse_expander = VerseExpander(self.engine)
        self.corpus_builder = ParallelCorpusBuilder(self.engine)
        self.ensure_schema()

    def ensure_schema(self) -> None:
        """Create the expansion tables if missing (additive, idempotent)."""
        with self.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS corpus_chapter_expansions (
                    ref TEXT PRIMARY KEY,
                    book TEXT NOT NULL,
                    chapter INTEGER NOT NULL,
                    verse INTEGER NOT NULL,
                    zo_tdb77 TEXT,
                    zo_tedim2010 TEXT,
                    en_kJV TEXT,
                    keywords TEXT,
                    grammar_features TEXT,
                    created_at TEXT NOT NULL
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS corpus_expansion_runs (
                    run_key TEXT PRIMARY KEY,
                    stats TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """))

    def _list_chapters(self) -> list:
        """All (book, chapter) pairs in canonical order."""
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT DISTINCT book, chapter FROM bible_verses")).fetchall()

        chapters = [(row.book, row.chapter) for row in rows]
        order = {book: i for i, book in enumerate(BOOK_ORDER)}
        chapters.sort(key=lambda bc: (order.get(bc[0], len(BOOK_ORDER)), bc[0], bc[1]))
        return chapters

    def store_expansions(self, expansions: list) -> int:
        """Persist verse expansions to SQLite (idempotent on ``ref``)."""
        if not expansions:
            return 0

        now = stamp()
        rows = [
            {
                "ref": e.ref,
                "book": e.book,
                "chapter": e.chapter,
                "verse": e.verse,
                "zo_tdb77": e.zo_tdb77,
                "zo_tedim2010": e.zo_tedim2010,
                "en_kJV": e.en_kJV,
                "keywords": json.dumps(e.keywords, ensure_ascii=False),
                "grammar_features": json.dumps(e.grammar_features, ensure_ascii=False),
                "created_at": now,
            }
            for e in expansions
        ]

        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT OR IGNORE INTO corpus_chapter_expansions
                        (ref, book, chapter, verse, zo_tdb77, zo_tedim2010, en_kJV,
                         keywords, grammar_features, created_at)
                    VALUES (:ref, :book, :chapter, :verse, :zo_tdb77, :zo_tedim2010,
                            :en_kJV, :keywords, :grammar_features, :created_at)
                """),
                rows,
            )
        return len(rows)

    def store_run_stats(self, run_key: str, stats: dict) -> None:
        """Record run statistics (idempotent on ``run_key``)."""
        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT OR IGNORE INTO corpus_expansion_runs (run_key, stats, created_at)
                    VALUES (:run_key, :stats, :created_at)
                """),
                {
                    "run_key": run_key,
                    "stats": json.dumps(stats, ensure_ascii=False, sort_keys=True),
                    "created_at": stamp(),
                },
            )

    def expand_all(self, max_books: int = None, include_sentences: bool = True) -> dict:
        """Expand the whole Bible corpus into SQLite.

        Idempotent: re-running leaves the stored row counts unchanged.
        """
        logger.info("Building parallel corpus...")
        pairs = self.corpus_builder.build_parallel_pairs()
        self.corpus_builder.store_pairs(pairs, pair_type="verse")

        stats = {"parallel_pairs": len(pairs)}

        if include_sentences:
            logger.info("Building sentence pairs...")
            sent_pairs = self.corpus_builder.build_sentence_pairs()
            self.corpus_builder.store_pairs(sent_pairs, pair_type="sentence")
            stats["sentence_pairs"] = len(sent_pairs)

        logger.info("Expanding chapters...")
        chapters = self._list_chapters()
        if max_books is not None:
            allowed = {b for b, _ in chapters[:max_books]}
            chapters = [bc for bc in chapters if bc[0] in allowed]

        chapter_expansions = 0
        for book, chapter in chapters:
            expansions = self.verse_expander.expand_passage(book, chapter, 1, 200)
            chapter_expansions += len(expansions)
            self.store_expansions(expansions)

        stats["chapter_expansions"] = chapter_expansions
        stats["chapters"] = len(chapters)
        stats["stored_pairs"] = self.corpus_builder.count_pairs()
        stats["stored_expansions"] = self.count_expansions()
        stats["stats"] = self.corpus_builder.get_stats()

        self.store_run_stats("expand_all", stats)

        logger.info(f"Corpus expansion complete: {stats}")
        return stats

    def count_expansions(self) -> int:
        """Number of stored verse expansions."""
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT COUNT(*) FROM corpus_chapter_expansions")).scalar()
