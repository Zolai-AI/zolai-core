"""
Intelligent Word Analyzer — Uses POS, grammar patterns, and DB context for deep analysis.

Works WITH or WITHOUT Gemini:
- Primary: Uses existing database (dictionary, grammar_patterns, word_usage, translations)
- Enhancement: Uses Gemini for missing data or deeper insights
- Fallback: Returns database-derived analysis only
"""

import sqlite3
import json
import logging
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class WordAnalysis:
    """Complete word analysis result."""
    word: str
    language: str  # zo, en, my
    primary_meaning: str
    all_meanings: list[dict]
    pos: str
    pos_description: str
    grammar_roles: list[str]
    usage_frequency: int
    bible_occurrences: list[dict]
    collocations: list[str]
    synonyms: list[str]
    antonyms: list[str]
    register: str  # formal/informal/literary
    examples: list[dict]
    tone_info: str
    etymology: str
    related_words: list[str]
    common_mistakes: list[str]
    learning_notes: str

class IntelligentAnalyzer:
    """
    Database-first word analyzer with POS awareness.

    Uses existing data to build comprehensive analysis without Gemini.
    Optionally enhances with Gemini for missing data.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def analyze_word(self, word: str, language: str = "zo", use_gemini: bool = False) -> WordAnalysis:
        """
        Analyze a word using database intelligence.

        Args:
            word: The word to analyze
            language: Source language (zo, en, my)
            use_gemini: Whether to enhance with Gemini (optional)
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # Step 1: Get basic dictionary data
        dict_data = self._get_dictionary_data(conn, word, language)

        # Step 2: Get POS-specific analysis
        pos_analysis = self._analyze_by_pos(conn, word, dict_data.get('pos', ''), language)

        # Step 3: Get grammar roles
        grammar_roles = self._get_grammar_roles(conn, word, language)

        # Step 4: Get usage patterns
        usage = self._get_usage_patterns(conn, word, language)

        # Step 5: Get Bible occurrences
        bible = self._get_bible_occurrences(conn, word, language)

        # Step 6: Get collocations
        collocations = self._get_collocations(conn, word, language)

        # Step 7: Get synonyms/antonyms
        synonyms, antonyms = self._get_synonyms_antonyms(conn, word, language)

        # Step 8: Get examples
        examples = self._get_examples(conn, word, language)

        # Step 9: Get related words
        related = self._get_related_words(conn, word, language)

        # Step 10: Get common mistakes
        mistakes = self._get_common_mistakes(conn, word, language)

        # Step 11: Generate learning notes
        learning_notes = self._generate_learning_notes(
            word, language, dict_data, pos_analysis, grammar_roles, usage, bible
        )

        conn.close()

        return WordAnalysis(
            word=word,
            language=language,
            primary_meaning=dict_data.get('meaning', ''),
            all_meanings=dict_data.get('all_meanings', []),
            pos=dict_data.get('pos', ''),
            pos_description=pos_analysis.get('description', ''),
            grammar_roles=grammar_roles,
            usage_frequency=usage.get('frequency', 0),
            bible_occurrences=bible,
            collocations=collocations,
            synonyms=synonyms,
            antonyms=antonyms,
            register=usage.get('register', 'neutral'),
            examples=examples,
            tone_info=pos_analysis.get('tone_info', ''),
            etymology=pos_analysis.get('etymology', ''),
            related_words=related,
            common_mistakes=mistakes,
            learning_notes=learning_notes
        )

    def _get_dictionary_data(self, conn, word: str, language: str) -> dict:
        """Get dictionary data for a word."""
        if language == "zo":
            row = conn.execute(
                "SELECT * FROM dictionary WHERE zolai = ? AND is_deleted = 0",
                (word,)
            ).fetchone()
            if row:
                return {
                    'meaning': row['english_clean'] or row['english'] or '',
                    'pos': row['pos'] or '',
                    'source': row['source'] or '',
                    'all_meanings': self._get_all_meanings(conn, row['id'])
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

    def _get_all_meanings(self, conn, dictionary_id: int) -> list[dict]:
        """Get all meanings from dictionary_meanings table."""
        rows = conn.execute(
            "SELECT * FROM dictionary_meanings WHERE dictionary_id = ? ORDER BY is_primary DESC",
            (dictionary_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def _analyze_by_pos(self, conn, word: str, pos: str, language: str) -> dict:
        """Analyze word based on its part of speech."""
        pos_lower = pos.lower() if pos else ''

        analysis = {
            'description': '',
            'tone_info': '',
            'etymology': '',
            'grammar_rules': []
        }

        # POS descriptions
        pos_descriptions = {
            'n': 'Noun — names a person, place, thing, or idea',
            'v': 'Verb — describes an action or state',
            'adj': 'Adjective — describes a noun',
            'adv': 'Adverb — modifies a verb, adjective, or other adverb',
            'pron': 'Pronoun — replaces a noun',
            'prep': 'Preposition — shows relationship between words',
            'conj': 'Conjunction — connects words or clauses',
            'interj': 'Interjection — expresses emotion',
            'det': 'Determiner — introduces a noun',
            'num': 'Numeral — expresses number',
            'classifier': 'Classifier — used with numerals',
            'particle': 'Particle — grammatical function word',
            'clf': 'Classifier — used with numerals',
        }

        for key, desc in pos_descriptions.items():
            if key in pos_lower:
                analysis['description'] = desc
                break

        # POS-specific grammar rules for Zolai
        if language == "zo":
            grammar_query = """
                SELECT pattern, description, function
                FROM grammar_patterns
                WHERE pattern LIKE ? OR description LIKE ?
                LIMIT 5
            """
            rows = conn.execute(grammar_query, (f"%{pos_lower}%", f"%{pos_lower}%")).fetchall()
            analysis['grammar_rules'] = [dict(r) for r in rows]

            # Tone info for Zolai
            if pos_lower in ['n', 'v', 'adj']:
                analysis['tone_info'] = 'Zolai has 4 tones (T1-T4). Check tone sandhi rules for compound words.'

        return analysis

    def _get_grammar_roles(self, conn, word: str, language: str) -> list[str]:
        """Get grammar roles from grammar_patterns table."""
        if language != "zo":
            return []

        roles = []
        # Check if word appears in grammar patterns
        rows = conn.execute("""
            SELECT DISTINCT function
            FROM grammar_patterns
            WHERE pattern LIKE ? OR description LIKE ?
        """, (f"%{word}%", f"%{word}%")).fetchall()

        for row in rows:
            if row['function']:
                roles.append(row['function'])

        # Add common Zolai grammar roles
        common_roles = {
            'transitive': 'Can take a direct object',
            'intransitive': 'Cannot take a direct object',
            'ergative': 'Agent marker in transitive sentences',
            'passive': 'Subject receives action',
            'causative': 'Causes another to do action',
            'directional': 'Indicates movement direction',
            'aspect': 'Marks time/aspect of action',
            'negation': 'Used for negation',
            'question': 'Used for question formation'
        }

        for role, desc in common_roles.items():
            if any(role in r.lower() for r in roles):
                roles.append(f"{role}: {desc}")

        return roles

    def _get_usage_patterns(self, conn, word: str, language: str) -> dict:
        """Get usage patterns from word_usage table."""
        if language != "zo":
            return {'frequency': 0, 'register': 'neutral', 'books': []}

        rows = conn.execute("""
            SELECT book, frequency, co_occurring_words
            FROM word_usage
            WHERE word = ?
            ORDER BY frequency DESC
        """, (word,)).fetchall()

        if not rows:
            return {'frequency': 0, 'register': 'neutral', 'books': []}

        total_freq = sum(r['frequency'] or 0 for r in rows)
        books = [r['book'] for r in rows if r['book']]

        # Determine register from frequency and books
        register = 'neutral'
        if total_freq > 100:
            register = 'common'
        elif total_freq < 10:
            register = 'literary/rare'

        # Check if mostly in poetry/proverbs
        if any(book in ['PSA', 'PRO', 'JOB'] for book in books):
            register = 'literary/poetic'

        return {
            'frequency': total_freq,
            'register': register,
            'books': books,
            'details': [dict(r) for r in rows]
        }

    def _get_bible_occurrences(self, conn, word: str, language: str) -> list[dict]:
        """Get Bible occurrences for a word."""
        if language != "zo":
            return []

        rows = conn.execute("""
            SELECT ref, zo_tdb77, en_kJV
            FROM bible_verses
            WHERE zo_tdb77 LIKE ?
            LIMIT 10
        """, (f"%{word}%",)).fetchall()

        return [{'ref': r['ref'], 'zo': r['zo_tdb77'][:100], 'en': r['en_kJV'][:100]} for r in rows]

    def _get_collocations(self, conn, word: str, language: str) -> list[str]:
        """Get collocations from word_collocations table."""
        if language != "zo":
            return []

        rows = conn.execute("""
            SELECT word2, frequency
            FROM word_collocations
            WHERE word1 = ?
            ORDER BY frequency DESC
            LIMIT 10
        """, (word,)).fetchall()

        # Also get reverse collocations
        reverse = conn.execute("""
            SELECT word1, frequency
            FROM word_collocations
            WHERE word2 = ?
            ORDER BY frequency DESC
            LIMIT 10
        """, (word,)).fetchall()

        collocations = [r['word2'] for r in rows if r['word2']]
        collocations += [r['word1'] for r in reverse if r['word1']]
        return list(set(collocations))

    def _get_synonyms_antonyms(self, conn, word: str, language: str) -> tuple[list[str], list[str]]:
        """Get synonyms and antonyms from translations and dictionary."""
        synonyms = []
        antonyms = []

        if language == "zo":
            # Get English translations
            row = conn.execute(
                "SELECT english_clean FROM dictionary WHERE zolai = ? AND is_deleted = 0",
                (word,)
            ).fetchone()

            if row and row['english_clean']:
                # Find other Zolai words with similar English translations
                rows = conn.execute("""
                    SELECT zolai FROM dictionary
                    WHERE english_clean LIKE ? AND zolai != ? AND is_deleted = 0
                    LIMIT 5
                """, (f"%{row['english_clean'][:20]}%", word)).fetchall()
                synonyms = [r['zolai'] for r in rows]

        return synonyms, antonyms

    def _get_examples(self, conn, word: str, language: str) -> list[dict]:
        """Get example sentences from translations and Bible."""
        examples = []

        if language == "zo":
            # From translations table
            rows = conn.execute("""
                SELECT source, target, source_lang
                FROM translations
                WHERE source LIKE ? OR target LIKE ?
                LIMIT 5
            """, (f"%{word}%", f"%{word}%")).fetchall()

            for r in rows:
                examples.append({
                    'zo': r['source'] if r['source_lang'] == 'zo' else r['target'],
                    'en': r['target'] if r['source_lang'] == 'zo' else r['source'],
                    'source': 'translations'
                })

            # From Bible verses
            rows = conn.execute("""
                SELECT ref, zo_tdb77, en_kJV
                FROM bible_verses
                WHERE zo_tdb77 LIKE ?
                LIMIT 3
            """, (f"%{word}%",)).fetchall()

            for r in rows:
                examples.append({
                    'zo': r['zo_tdb77'][:150],
                    'en': r['en_kJV'][:150],
                    'source': f"Bible {r['ref']}"
                })

        return examples

    def _get_related_words(self, conn, word: str, language: str) -> list[str]:
        """Get related words from dictionary."""
        if language != "zo":
            return []

        # Get words that share the same root
        root = word.split('-')[0] if '-' in word else word[:4]
        rows = conn.execute("""
            SELECT zolai FROM dictionary
            WHERE zolai LIKE ? AND zolai != ? AND is_deleted = 0
            LIMIT 5
        """, (f"%{root}%", word)).fetchall()

        return [r['zolai'] for r in rows]

    def _get_common_mistakes(self, conn, word: str, language: str) -> list[str]:
        """Get common mistakes from audit_findings."""
        mistakes = []

        rows = conn.execute("""
            SELECT description, severity
            FROM audit_findings
            WHERE finding_type = 'zvs_violation'
            AND description LIKE ?
            LIMIT 5
        """, (f"%{word}%",)).fetchall()

        for r in rows:
            mistakes.append(f"{r['severity']}: {r['description'][:100]}")

        return mistakes

    def _generate_learning_notes(self, word: str, language: str, dict_data: dict,
                                  pos_analysis: dict, grammar_roles: list,
                                  usage: dict, bible: list) -> str:
        """Generate intelligent learning notes based on all analysis."""
        notes = []

        # POS-based notes
        if pos_analysis.get('description'):
            notes.append(f"**Part of Speech:** {pos_analysis['description']}")

        # Grammar notes
        if grammar_roles:
            notes.append(f"**Grammar Roles:** {', '.join(grammar_roles[:3])}")

        # Usage notes
        if usage.get('frequency', 0) > 0:
            freq = usage['frequency']
            register = usage.get('register', 'neutral')
            notes.append(f"**Usage:** Frequency {freq} in Bible ({register} register)")

        # Bible notes
        if bible:
            notes.append(f"**Bible Occurrences:** Found in {len(bible)} verses")
            if len(bible) > 5:
                notes.append("Common word — appears frequently in scripture")

        # Zolai-specific notes
        if language == "zo":
            notes.append("**Zolai Grammar:**")
            notes.append("- SOV word order (Subject-Object-Verb)")
            notes.append("- Ergative `in` marks agent of transitive verbs")
            notes.append("- Negation uses `kei` for all persons")
            notes.append("- Questions end with `hiam`")

            # Tone notes
            if pos_analysis.get('tone_info'):
                notes.append(f"- {pos_analysis['tone_info']}")

        return "\n".join(notes)

# Convenience function
def analyze_word_full(word: str, language: str = "zo", db_path: str = None) -> dict:
    """Analyze a word and return as dictionary."""
    if db_path is None:
        db_path = str(Path(__file__).resolve().parents[3] / "data" / "zolai.db")

    analyzer = IntelligentAnalyzer(db_path)
    result = analyzer.analyze_word(word, language)
    return asdict(result)
