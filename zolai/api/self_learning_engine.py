"""
Self-Learning Engine — Continuously improves from database data.

Features:
- Auto-discovers new words/patterns from Bible verses
- Builds grammar patterns from sentence analysis
- Updates word frequency from corpus
- Detects meaning shifts across books
- Generates training exercises from real data
- No Gemini dependency — uses pure database intelligence
"""

import sqlite3
import json
import logging
from pathlib import Path
from collections import Counter, defaultdict
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class LearningResult:
    """Result of a self-learning cycle."""
    new_words_found: int
    patterns_discovered: int
    exercises_generated: int
    meaning_shifts_detected: int
    frequency_updates: int
    errors: list[str]


class SelfLearningEngine:
    """
    Database-first self-learning engine.
    
    Analyzes existing data to discover new knowledge without external AI.
    Runs continuously to keep the system up-to-date.
    """
    
    def __init__(self, db_path: str):
        self.db_path = db_path
    
    def run_learning_cycle(self) -> LearningResult:
        """Run a complete learning cycle."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        result = LearningResult(
            new_words_found=0,
            patterns_discovered=0,
            exercises_generated=0,
            meaning_shifts_detected=0,
            frequency_updates=0,
            errors=[]
        )
        
        try:
            # 1. Discover new words from Bible
            result.new_words_found = self._discover_new_words(conn)
            
            # 2. Build grammar patterns from sentences
            result.patterns_discovered = self._build_grammar_patterns(conn)
            
            # 3. Generate training exercises
            result.exercises_generated = self._generate_exercises(conn)
            
            # 4. Detect meaning shifts
            result.meaning_shifts_detected = self._detect_meaning_shifts(conn)
            
            # 5. Update word frequencies
            result.frequency_updates = self._update_frequencies(conn)
            
            conn.commit()
            
        except Exception as e:
            result.errors.append(str(e))
            logger.error("Learning cycle failed: %s", e)
        finally:
            conn.close()
        
        return result
    
    def _discover_new_words(self, conn) -> int:
        """Find words in Bible not in dictionary."""
        new_words = conn.execute("""
            SELECT DISTINCT word FROM (
                SELECT word FROM (
                    SELECT LOWER(SUBSTR(zo_tdb77, 1, 50)) as word FROM bible_verses
                    WHERE zo_tdb77 IS NOT NULL
                )
            )
            WHERE word NOT IN (SELECT zolai FROM dictionary WHERE is_deleted = 0)
            LIMIT 100
        """).fetchall()
        
        count = 0
        for w in new_words:
            word = w['word'].strip()
            if len(word) > 2 and word.isalpha():
                # Try to find English translation from parallel verse
                verse = conn.execute("""
                    SELECT en_kJV FROM bible_verses
                    WHERE zo_tdb77 LIKE ?
                    LIMIT 1
                """, (f"%{word}%",)).fetchone()
                
                if verse:
                    conn.execute("""
                        INSERT OR IGNORE INTO dictionary (zolai, english, english_clean, source)
                        VALUES (?, ?, ?, 'auto_discovered')
                    """, (word, verse['en_kJV'][:100], verse['en_kJV'][:100]))
                    count += 1
        
        return count
    
    def _build_grammar_patterns(self, conn) -> int:
        """Extract grammar patterns from Bible sentences."""
        patterns = Counter()
        
        # Get common sentence structures
        verses = conn.execute("""
            SELECT zo_tdb77 FROM bible_verses
            WHERE zo_tdb77 IS NOT NULL AND LENGTH(zo_tdb77) > 20
            LIMIT 1000
        """).fetchall()
        
        for v in verses:
            text = v['zo_tdb77']
            words = text.split()
            
            if len(words) >= 3:
                # Detect SOV pattern
                if 'hi' in words or 'hen' in words:
                    patterns['S + O + V (hi/hen)'] += 1
                
                # Detect ergative
                if 'in' in words:
                    idx = words.index('in')
                    if idx > 0 and idx < len(words) - 1:
                        patterns['S + in + O + V'] += 1
                
                # Detect negation
                if 'kei' in words:
                    patterns['negation (kei)'] += 1
                if 'lo' in words:
                    patterns['negation (lo)'] += 1
                
                # Detect question
                if 'hiam' in words:
                    patterns['question (hiam)'] += 1
        
        # Save top patterns
        count = 0
        for pattern, freq in patterns.most_common(20):
            existing = conn.execute("""
                SELECT id FROM grammar_patterns WHERE pattern = ?
            """, (pattern,)).fetchone()
            
            if not existing:
                conn.execute("""
                    INSERT INTO grammar_patterns (pattern_id, pattern, description, function, examples, frequency)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    f"AUTO_{pattern[:20]}",
                    pattern,
                    f"Auto-discovered from Bible corpus (freq: {freq})",
                    'auto_discovered',
                    '',
                    freq
                ))
                count += 1
        
        return count
    
    def _generate_exercises(self, conn) -> int:
        """Generate training exercises from Bible verses."""
        count = 0
        
        # Get verses without exercises
        verses = conn.execute("""
            SELECT ref, zo_tdb77, en_kJV FROM bible_verses b
            LEFT JOIN training_exercises te ON b.zo_tdb77 = te.zolai
            WHERE te.id IS NULL
            AND b.zo_tdb77 IS NOT NULL AND b.zo_tdb77 != ''
            LIMIT 100
        """).fetchall()
        
        for v in verses:
            # Create translation exercise
            conn.execute("""
                INSERT INTO training_exercises (exercise_type, zolai, english, source, difficulty)
                VALUES (?, ?, ?, ?, ?)
            """, (
                'translate_en_zo',
                v['en_kJV'][:200],
                v['zo_tdb77'],
                f"bible_{v['ref']}",
                'B1'
            ))
            count += 1
            
            # Create reverse exercise
            conn.execute("""
                INSERT INTO training_exercises (exercise_type, zolai, english, source, difficulty)
                VALUES (?, ?, ?, ?, ?)
            """, (
                'translate_zo_en',
                v['zo_tdb77'],
                v['en_kJV'][:200],
                f"bible_{v['ref']}",
                'B1'
            ))
            count += 1
        
        return count
    
    def _detect_meaning_shifts(self, conn) -> int:
        """Detect words with different meanings across Bible books."""
        count = 0
        
        # Get words that appear in multiple books with different translations
        shifts = conn.execute("""
            SELECT w.word, w.book, d.english_clean
            FROM word_usage w
            JOIN dictionary d ON w.word = d.zolai
            WHERE d.is_deleted = 0
            GROUP BY w.word
            HAVING COUNT(DISTINCT w.book) > 3
            LIMIT 50
        """).fetchall()
        
        for s in shifts:
            # Check if translations differ across books
            books = conn.execute("""
                SELECT DISTINCT b.ref, b.en_kJV
                FROM bible_verses b
                WHERE b.zo_tdb77 LIKE ?
                LIMIT 5
            """, (f"%{s['word']}%",)).fetchall()
            
            translations = set(b['en_kJV'][:50] for b in books if b['en_kJV'])
            if len(translations) > 1:
                # Meaning shift detected
                shift_data = {
                    'word': s['word'],
                    'books': [b['ref'] for b in books],
                    'translations': list(translations)
                }
                
                existing = conn.execute("""
                    SELECT id FROM word_usage WHERE word = ? AND book = 'MEANING_SHIFT'
                """, (s['word'],)).fetchone()
                
                if not existing:
                    conn.execute("""
                        INSERT INTO word_usage (word, book, total_freq, meaning_shifts)
                        VALUES (?, 'MEANING_SHIFT', 1, ?)
                    """, (s['word'], json.dumps(shift_data)))
                    count += 1
        
        return count
    
    def _update_frequencies(self, conn) -> int:
        """Update word frequencies from Bible corpus."""
        count = 0
        
        # Get all words from Bible
        freq = conn.execute("""
            SELECT word, COUNT(*) as cnt FROM (
                SELECT word FROM (
                    SELECT LOWER(SUBSTR(zo_tdb77, 1, 100)) as word FROM bible_verses
                    WHERE zo_tdb77 IS NOT NULL
                )
            ) GROUP BY word
        """).fetchall()
        
        for f in freq:
            word = f['word'].strip()
            if len(word) > 2:
                existing = conn.execute("""
                    SELECT id FROM word_usage WHERE word = ? AND book = 'BIBLE'
                """, (word,)).fetchone()
                
                if existing:
                    conn.execute("""
                        UPDATE word_usage SET total_freq = ? WHERE id = ?
                    """, (f['cnt'], existing['id']))
                else:
                    conn.execute("""
                        INSERT INTO word_usage (word, book, total_freq)
                        VALUES (?, 'BIBLE', ?)
                    """, (word, f['cnt']))
                count += 1
        
        return count


# CLI entry point
def main():
    """Run self-learning cycle."""
    import sys
    db_path = str(Path(__file__).resolve().parents[3] / "data" / "zolai.db")
    
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    
    engine = SelfLearningEngine(db_path)
    result = engine.run_learning_cycle()
    
    print(f"=== Self-Learning Cycle Complete ===")
    print(f"New words found: {result.new_words_found}")
    print(f"Patterns discovered: {result.patterns_discovered}")
    print(f"Exercises generated: {result.exercises_generated}")
    print(f"Meaning shifts detected: {result.meaning_shifts_detected}")
    print(f"Frequency updates: {result.frequency_updates}")
    if result.errors:
        print(f"Errors: {result.errors}")

if __name__ == "__main__":
    main()
