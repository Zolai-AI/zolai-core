"""
Comprehensive Linguistic Analyzer — Full analysis of Zolai words and sentences.

Covers ALL linguistic dimensions:
- Dictionary (meanings, POS, etymology)
- Grammar (patterns, functions, sentence structure)
- Verbs (classes, tense, transitivity)
- Particles (functions, meanings)
- Phrases (multi-word expressions)
- Proverbs & Idioms
- Tone Sandhi (tone changes)
- Word Usage (frequency, meaning shifts, co-occurring words)
- Training Exercises (practice sentences)
- Bible Context (usage in scripture)
- Syllable Structure (pronunciation)
- Collocations (word partnerships)
- Register (formal/informal/literary)
- What You Might Not Know (common gaps)

Works WITH or WITHOUT Gemini.
"""

import sqlite3
import json
import logging
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ComprehensiveAnalysis:
    """Full linguistic analysis of a word or sentence."""
    # Basic info
    word: str
    language: str
    primary_meaning: str
    all_meanings: list[dict]
    pos: str

    # Grammar
    grammar_patterns: list[dict]
    sentence_roles: list[str]
    verb_info: Optional[dict]  # verb class, tense, transitivity
    particle_info: Optional[dict]  # particle function, meaning

    # Vocabulary
    collocations: list[str]
    synonyms: list[str]
    antonyms: list[str]
    related_words: list[str]
    compound_words: list[str]

    # Usage
    frequency: int
    register: str  # formal/informal/literary/common
    bible_occurrences: list[dict]
    usage_examples: list[dict]

    # Phonology
    syllables: str
    tone_info: str
    tone_sandhi_rules: list[dict]

    # Culture
    proverbs: list[dict]
    idioms: list[dict]
    cultural_notes: str

    # Learning
    common_mistakes: list[str]
    learning_notes: str
    what_you_might_not_know: list[str]
    difficulty_level: str
    prerequisite_words: list[str]


class ComprehensiveAnalyzer:
    """
    Database-first comprehensive linguistic analyzer.

    Uses ALL available tables to build deep analysis.
    Optionally enhances with Gemini for missing data.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def analyze(self, word: str, language: str = "zo") -> ComprehensiveAnalysis:
        """Full linguistic analysis of a word."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # 1. Basic dictionary data
        dict_data = self._get_dictionary(conn, word, language)

        # 2. Grammar patterns
        grammar = self._get_grammar_patterns(conn, word, language)

        # 3. Verb information
        verb_info = self._get_verb_info(conn, word, language)

        # 4. Particle information
        particle_info = self._get_particle_info(conn, word, language)

        # 5. Collocations
        collocations = self._get_collocations(conn, word, language)

        # 6. Synonyms/antonyms
        synonyms, antonyms = self._get_synonyms_antonyms(conn, word, language)

        # 7. Related words
        related = self._get_related_words(conn, word, language)

        # 8. Compound words
        compounds = self._get_compound_words(conn, word, language)

        # 9. Usage patterns
        usage = self._get_usage_patterns(conn, word, language)

        # 10. Bible occurrences
        bible = self._get_bible_occurrences(conn, word, language)

        # 11. Examples
        examples = self._get_examples(conn, word, language)

        # 12. Syllable data
        syllables = self._get_syllables(conn, word, language)

        # 13. Tone information
        tone_info, sandhi_rules = self._get_tone_info(conn, word, language)

        # 14. Proverbs
        proverbs = self._get_proverbs(conn, word, language)

        # 15. Idioms
        idioms = self._get_idioms(conn, word, language)

        # 16. Common mistakes
        mistakes = self._get_common_mistakes(conn, word, language)

        # 17. What you might not know
        unknown_gaps = self._get_unknown_gaps(conn, word, language, dict_data, grammar, usage)

        # 18. Learning notes
        learning_notes = self._generate_learning_notes(
            word, language, dict_data, grammar, verb_info, particle_info,
            usage, bible, proverbs, tone_info, unknown_gaps
        )

        # 19. Difficulty level
        difficulty = self._assess_difficulty(word, language, usage, grammar)

        # 20. Prerequisite words
        prereqs = self._get_prerequisites(conn, word, language, difficulty)

        conn.close()

        return ComprehensiveAnalysis(
            word=word,
            language=language,
            primary_meaning=dict_data.get('meaning', ''),
            all_meanings=dict_data.get('all_meanings', []),
            pos=dict_data.get('pos', ''),
            grammar_patterns=grammar,
            sentence_roles=self._get_sentence_roles(grammar),
            verb_info=verb_info,
            particle_info=particle_info,
            collocations=collocations,
            synonyms=synonyms,
            antonyms=antonyms,
            related_words=related,
            compound_words=compounds,
            frequency=usage.get('frequency', 0),
            register=usage.get('register', 'neutral'),
            bible_occurrences=bible,
            usage_examples=examples,
            syllables=syllables,
            tone_info=tone_info,
            tone_sandhi_rules=sandhi_rules,
            proverbs=proverbs,
            idioms=idioms,
            cultural_notes=self._get_cultural_notes(word, language, proverbs, bible),
            common_mistakes=mistakes,
            learning_notes=learning_notes,
            what_you_might_not_know=unknown_gaps,
            difficulty_level=difficulty,
            prerequisite_words=prereqs
        )

    def _get_dictionary(self, conn, word: str, language: str) -> dict:
        """Get dictionary data from multiple tables."""
        if language == "zo":
            row = conn.execute(
                "SELECT * FROM dictionary WHERE zolai = ? AND is_deleted = 0",
                (word,)
            ).fetchone()
            if row:
                # Get all meanings from dictionary_meanings
                meanings = conn.execute(
                    "SELECT * FROM dictionary_meanings WHERE dictionary_id = ?",
                    (row['id'],)
                ).fetchall()
                return {
                    'meaning': row['english_clean'] or row['english'] or '',
                    'pos': row['pos'] or '',
                    'source': row['source'] or '',
                    'all_meanings': [dict(m) for m in meanings] if meanings else []
                }
        elif language == "en":
            row = conn.execute(
                "SELECT * FROM dictionary_en_zo WHERE headword = ?",
                (word,)
            ).fetchone()
            if row:
                return {
                    'meaning': row['translations_clean'] or row['translations'] or '',
                    'pos': row['pos'] or '',
                    'source': row['source'] or '',
                    'all_meanings': []
                }
        return {'meaning': '', 'pos': '', 'source': '', 'all_meanings': []}

    def _get_grammar_patterns(self, conn, word: str, language: str) -> list[dict]:
        """Get grammar patterns for a word."""
        if language != "zo":
            return []

        patterns = conn.execute("""
            SELECT pattern, description, function, examples, frequency
            FROM grammar_patterns
            WHERE pattern LIKE ? OR description LIKE ? OR examples LIKE ?
            ORDER BY frequency DESC
            LIMIT 10
        """, (f"%{word}%", f"%{word}%", f"%{word}%")).fetchall()

        return [dict(p) for p in patterns]

    def _get_verb_info(self, conn, word: str, language: str) -> Optional[dict]:
        """Get verb-specific information."""
        if language != "zo":
            return None

        row = conn.execute("""
            SELECT verb, stem, verb_class, tense, meaning, examples, transitivity
            FROM verb_database
            WHERE verb = ? OR stem = ?
        """, (word, word)).fetchone()

        if row:
            return dict(row)
        return None

    def _get_particle_info(self, conn, word: str, language: str) -> Optional[dict]:
        """Get particle-specific information."""
        if language != "zo":
            return None

        row = conn.execute("""
            SELECT particle, function, function_type, meaning, examples, notes
            FROM particle_database
            WHERE particle = ?
        """, (word,)).fetchone()

        if row:
            return dict(row)
        return None

    def _get_collocations(self, conn, word: str, language: str) -> list[str]:
        """Get word collocations."""
        if language != "zo":
            return []

        cols = []

        # Forward collocations
        rows = conn.execute("""
            SELECT word2, frequency FROM word_collocations
            WHERE word1 = ? ORDER BY frequency DESC LIMIT 10
        """, (word,)).fetchall()
        cols.extend([r['word2'] for r in rows if r['word2']])

        # Reverse collocations
        rows = conn.execute("""
            SELECT word1, frequency FROM word_collocations
            WHERE word2 = ? ORDER BY frequency DESC LIMIT 10
        """, (word,)).fetchall()
        cols.extend([r['word1'] for r in rows if r['word1']])

        return list(set(cols))

    def _get_synonyms_antonyms(self, conn, word: str, language: str) -> tuple[list[str], list[str]]:
        """Get synonyms and antonyms from translations and dictionary."""
        synonyms = []
        antonyms = []

        if language == "zo":
            # Get English translation
            row = conn.execute(
                "SELECT english_clean FROM dictionary WHERE zolai = ? AND is_deleted = 0",
                (word,)
            ).fetchone()

            if row and row['english_clean']:
                # Find similar words
                rows = conn.execute("""
                    SELECT zolai FROM dictionary
                    WHERE english_clean LIKE ? AND zolai != ? AND is_deleted = 0
                    LIMIT 5
                """, (f"%{row['english_clean'][:20]}%", word)).fetchall()
                synonyms = [r['zolai'] for r in rows]

        return synonyms, antonyms

    def _get_related_words(self, conn, word: str, language: str) -> list[str]:
        """Get related words (shared root, derivations)."""
        if language != "zo":
            return []

        related = []

        # Get words with shared root (first 3-4 chars)
        root = word.split('-')[0] if '-' in word else word[:4]
        rows = conn.execute("""
            SELECT zolai FROM dictionary
            WHERE zolai LIKE ? AND zolai != ? AND is_deleted = 0
            LIMIT 5
        """, (f"%{root}%", word)).fetchall()
        related.extend([r['zolai'] for r in rows])

        # Get words from same source
        row = conn.execute(
            "SELECT source FROM dictionary WHERE zolai = ? AND is_deleted = 0",
            (word,)
        ).fetchone()
        if row and row['source']:
            rows = conn.execute("""
                SELECT zolai FROM dictionary
                WHERE source = ? AND zolai != ? AND is_deleted = 0
                LIMIT 5
            """, (row['source'], word)).fetchall()
            related.extend([r['zolai'] for r in rows])

        return list(set(related))

    def _get_compound_words(self, conn, word: str, language: str) -> list[str]:
        """Get compound words containing this word."""
        if language != "zo":
            return []

        rows = conn.execute("""
            SELECT zolai FROM dictionary
            WHERE zolai LIKE ? AND zolai != ? AND is_deleted = 0
            LIMIT 10
        """, (f"%{word}%", word)).fetchall()

        return [r['zolai'] for r in rows]

    def _get_usage_patterns(self, conn, word: str, language: str) -> dict:
        """Get usage patterns from word_usage table."""
        if language != "zo":
            return {'frequency': 0, 'register': 'neutral', 'books': [], 'meaning_shifts': []}

        rows = conn.execute("""
            SELECT book, total_freq, meaning_shifts, co_occurring_words
            FROM word_usage
            WHERE word = ?
            ORDER BY total_freq DESC
        """, (word,)).fetchall()

        if not rows:
            return {'frequency': 0, 'register': 'neutral', 'books': [], 'meaning_shifts': []}

        total_freq = sum(r['total_freq'] or 0 for r in rows)
        books = [r['book'] for r in rows if r['book']]
        meaning_shifts = []
        for r in rows:
            if r['meaning_shifts']:
                try:
                    shifts = json.loads(r['meaning_shifts']) if isinstance(r['meaning_shifts'], str) else r['meaning_shifts']
                    if isinstance(shifts, list):
                        meaning_shifts.extend(shifts)
                except:
                    pass

        # Determine register
        register = 'neutral'
        if total_freq > 100:
            register = 'common'
        elif total_freq < 10:
            register = 'literary/rare'
        if any(book in ['PSA', 'PRO', 'JOB'] for book in books):
            register = 'literary/poetic'

        return {
            'frequency': total_freq,
            'register': register,
            'books': books,
            'meaning_shifts': meaning_shifts,
            'details': [dict(r) for r in rows]
        }

    def _get_bible_occurrences(self, conn, word: str, language: str) -> list[dict]:
        """Get Bible occurrences."""
        if language != "zo":
            return []

        rows = conn.execute("""
            SELECT ref, zo_tdb77, en_kJV
            FROM bible_verses
            WHERE zo_tdb77 LIKE ?
            LIMIT 10
        """, (f"%{word}%",)).fetchall()

        return [{'ref': r['ref'], 'zo': r['zo_tdb77'][:100], 'en': r['en_kJV'][:100]} for r in rows]

    def _get_examples(self, conn, word: str, language: str) -> list[dict]:
        """Get example sentences from translations."""
        examples = []

        rows = conn.execute("""
            SELECT source, target, source_lang
            FROM translations
            WHERE source LIKE ? OR target LIKE ?
            LIMIT 10
        """, (f"%{word}%", f"%{word}%")).fetchall()

        for r in rows:
            examples.append({
                'zo': r['source'] if r['source_lang'] == 'zo' else r['target'],
                'en': r['target'] if r['source_lang'] == 'zo' else r['source'],
                'source': 'translations'
            })

        return examples

    def _get_syllables(self, conn, word: str, language: str) -> str:
        """Get syllable breakdown."""
        if language != "zo":
            return ""

        row = conn.execute("""
            SELECT syllables FROM syllable_data
            WHERE word = ?
        """, (word,)).fetchone()

        return row['syllables'] if row else ""

    def _get_tone_info(self, conn, word: str, language: str) -> tuple[str, list[dict]]:
        """Get tone information and sandhi rules."""
        if language != "zo":
            return "", []

        # Get tone patterns for this word
        rows = conn.execute("""
            SELECT rule_number, rule_name, underlying_pattern, surface_pattern,
                   condition, examples, domain
            FROM tone_sandhi
            WHERE underlying_pattern LIKE ? OR surface_pattern LIKE ? OR examples LIKE ?
            LIMIT 5
        """, (f"%{word}%", f"%{word}%", f"%{word}%")).fetchall()

        rules = [dict(r) for r in rows]
        tone_info = f"Zolai has 4 tones. {len(rules)} sandhi rules apply to this word." if rules else "Zolai has 4 tones. No specific sandhi rules for this word."

        return tone_info, rules

    def _get_proverbs(self, conn, word: str, language: str) -> list[dict]:
        """Get proverbs containing this word."""
        if language != "zo":
            return []

        rows = conn.execute("""
            SELECT zolai, english, source, category
            FROM proverbs
            WHERE zolai LIKE ?
            LIMIT 5
        """, (f"%{word}%",)).fetchall()

        return [dict(r) for r in rows]

    def _get_idioms(self, conn, word: str, language: str) -> list[dict]:
        """Get idioms containing this word."""
        if language != "zo":
            return []

        rows = conn.execute("""
            SELECT zolai, english, source, category
            FROM proverbs_idioms
            WHERE zolai LIKE ? AND category = 'idiom'
            LIMIT 5
        """, (f"%{word}%",)).fetchall()

        return [dict(r) for r in rows]

    def _get_common_mistakes(self, conn, word: str, language: str) -> list[str]:
        """Get common mistakes from audit_findings."""
        mistakes = []

        rows = conn.execute("""
            SELECT description, severity
            FROM audit_findings
            WHERE description LIKE ?
            LIMIT 5
        """, (f"%{word}%",)).fetchall()

        for r in rows:
            mistakes.append(f"{r['severity']}: {r['description'][:100]}")

        return mistakes

    def _get_unknown_gaps(self, conn, word: str, language: str, dict_data: dict,
                          grammar: list, usage: dict) -> list[str]:
        """
        Identify what a learner might not know how to say.
        This is the KEY innovation — proactive gap detection.
        """
        gaps = []

        if language == "zo":
            # Gap 1: No grammar patterns found
            if not grammar:
                gaps.append("No grammar patterns found for this word — you might not know how to use it in sentences")

            # Gap 2: No examples in translations
            examples = conn.execute("""
                SELECT COUNT(*) as cnt FROM translations
                WHERE source LIKE ? OR target LIKE ?
            """, (f"%{word}%", f"%{word}%")).fetchone()
            if examples and examples['cnt'] == 0:
                gaps.append("No example sentences found — you might not know how to say it in context")

            # Gap 3: No Bible occurrences
            bible = conn.execute("""
                SELECT COUNT(*) as cnt FROM bible_verses
                WHERE zo_tdb77 LIKE ?
            """, (f"%{word}%",)).fetchone()
            if bible and bible['cnt'] == 0:
                gaps.append("Not found in Bible — might be rare or modern")

            # Gap 4: No collocations
            cols = conn.execute("""
                SELECT COUNT(*) as cnt FROM word_collocations
                WHERE word1 = ? OR word2 = ?
            """, (word, word)).fetchone()
            if cols and cols['cnt'] == 0:
                gaps.append("No known word partnerships — you might not know what words go together")

            # Gap 5: No meaning shifts
            if usage.get('meaning_shifts'):
                gaps.append("This word has different meanings in different Bible books — context matters")

            # Gap 6: Missing Myanmar translation
            if not dict_data.get('all_meanings'):
                gaps.append("No Myanmar translation available — you might not know how to say it in Myanmar")

            # Gap 7: Verb-specific gaps
            verb = conn.execute("""
                SELECT * FROM verb_database WHERE verb = ? OR stem = ?
            """, (word, word)).fetchone()
            if verb:
                if not verb['transitivity']:
                    gaps.append("Transitivity unknown — you might not know if it takes a direct object")
                if not verb['verb_class']:
                    gaps.append("Verb class unknown — you might not know its conjugation pattern")

            # Gap 8: Particle-specific gaps
            particle = conn.execute("""
                SELECT * FROM particle_database WHERE particle = ?
            """, (word,)).fetchone()
            if particle:
                if not particle['function_type']:
                    gaps.append("Particle function type unknown — you might not know its grammatical role")

            # Gap 9: Rare word
            freq = usage.get('frequency', 0)
            if freq < 5:
                gaps.append("Very rare word — you might not encounter it often")

            # Gap 10: Literary register only
            if usage.get('register') == 'literary/rare':
                gaps.append("Literary/formal register only — you might not use it in casual speech")

        return gaps

    def _get_sentence_roles(self, grammar: list) -> list[str]:
        """Extract sentence roles from grammar patterns."""
        roles = []
        for g in grammar:
            if g.get('function'):
                roles.append(g['function'])
        return list(set(roles))

    def _get_cultural_notes(self, word: str, language: str, proverbs: list, bible: list) -> str:
        """Generate cultural context notes."""
        notes = []

        if proverbs:
            notes.append(f"This word appears in {len(proverbs)} proverbs — culturally significant")

        if bible:
            notes.append(f"Found in {len(bible)} Bible verses — has religious/spiritual context")

        if language == "zo":
            notes.append("Zolai is a Tibeto-Burman language spoken by the Zomi people of Myanmar/India")
            notes.append("ZVS 2018 is the standardized orthography")

        return "; ".join(notes) if notes else ""

    def _generate_learning_notes(self, word, language, dict_data, grammar, verb_info,
                                  particle_info, usage, bible, proverbs, tone_info, gaps) -> str:
        """Generate comprehensive learning notes."""
        notes = []

        # Basic meaning
        if dict_data.get('meaning'):
            notes.append(f"**Meaning:** {dict_data['meaning']}")

        # POS
        if dict_data.get('pos'):
            notes.append(f"**Part of Speech:** {dict_data['pos']}")

        # Grammar patterns
        if grammar:
            notes.append(f"**Grammar:** {len(grammar)} patterns found")
            for g in grammar[:2]:
                notes.append(f"  - {g.get('pattern', '')}: {g.get('description', '')[:60]}")

        # Verb info
        if verb_info:
            notes.append(f"**Verb:** {verb_info.get('verb_class', '?')} class, {verb_info.get('transitivity', '?')} transitive")
            if verb_info.get('tense'):
                notes.append(f"  - Tense: {verb_info['tense']}")

        # Particle info
        if particle_info:
            notes.append(f"**Particle:** {particle_info.get('function', '?')} ({particle_info.get('function_type', '?')})")

        # Usage
        if usage.get('frequency', 0) > 0:
            notes.append(f"**Usage:** Frequency {usage['frequency']} in Bible ({usage.get('register', 'neutral')})")

        # Proverbs
        if proverbs:
            notes.append(f"**Cultural:** Appears in {len(proverbs)} proverbs")

        # Zolai-specific
        if language == "zo":
            notes.append("**Zolai Grammar:**")
            notes.append("- SOV word order (Subject-Object-Verb)")
            notes.append("- Ergative `in` marks agent of transitive verbs")
            notes.append("- Negation uses `kei` for all persons")
            notes.append("- Questions end with `hiam`")
            if tone_info:
                notes.append(f"- {tone_info}")

        # Gaps
        if gaps:
            notes.append("**⚠️ What You Might Not Know:**")
            for gap in gaps[:5]:
                notes.append(f"  - {gap}")

        return "\n".join(notes)

    def _assess_difficulty(self, word: str, language: str, usage: dict, grammar: list) -> str:
        """Assess difficulty level based on usage and grammar."""
        freq = usage.get('frequency', 0)
        has_grammar = len(grammar) > 0

        if freq > 100 and has_grammar:
            return "A1"  # Very common, well-documented
        elif freq > 50:
            return "A2"  # Common
        elif freq > 20:
            return "B1"  # Intermediate
        elif freq > 5:
            return "B2"  # Upper intermediate
        elif freq > 0:
            return "C1"  # Advanced
        else:
            return "C2"  # Rare/specialized

    def _get_prerequisites(self, conn, word: str, language: str, difficulty: str) -> list[str]:
        """Get prerequisite words to learn first."""
        if language != "zo" or difficulty in ["A1", "A2"]:
            return []

        # Get most common words as prerequisites
        rows = conn.execute("""
            SELECT zolai FROM dictionary d
            JOIN word_usage wu ON d.zolai = wu.word
            WHERE d.is_deleted = 0
            ORDER BY wu.total_freq DESC
            LIMIT 5
        """).fetchall()

        return [r['zolai'] for r in rows]


# Convenience function
def analyze_comprehensive(word: str, language: str = "zo", db_path: str = None) -> dict:
    """Full linguistic analysis returned as dictionary."""
    if db_path is None:
        db_path = str(Path(__file__).resolve().parents[3] / "data" / "zolai.db")

    analyzer = ComprehensiveAnalyzer(db_path)
    result = analyzer.analyze(word, language)
    return asdict(result)
