"""
RAG Context Injector for Zolai Bilingual Conversation.

Extracts Zolai words from user input, looks up dictionary,
finds Bible examples, and injects concise context (<500 tokens).
"""
import json
import re
from pathlib import Path
from typing import Optional

from ..config import config
from ..data.database import get_manager

# Data paths (shared across repos)
DATA_DIR = Path(__file__).parent.parent.parent.parent.parent / "data"
DICT_ZO_EN = DATA_DIR / "dictionary" / "processed" / "dict_zo_en_master_v1.jsonl"
BIBLE_CORPUS = DATA_DIR / "bible" / "parallel_corpus_v1.jsonl"
WIKI_PHRASES = DATA_DIR / "bible" / "phrases_v1.jsonl"
GRAMMAR_PATTERNS = DATA_DIR / "bible" / "grammar_patterns_v2.jsonl"
MYANMAR_DICT = DATA_DIR / "processed" / "my" / "dict_myanmar_master_v1.jsonl"

class ZolaiRAGContext:
    """Lightweight RAG context injector for Zolai conversations."""

    def __init__(self):
        self._db = None
        self._myanmar_dict: dict[str, dict] | None = None
        # Try database first (fast, indexed)
        db_path = config.paths.data / "zolai.db"
        if db_path.exists():
            try:
                self._db = get_manager(f"sqlite:///{db_path}")
            except Exception:
                self._db = None
        # JSONL fallback (legacy)
        self.dict_zo_en = self._load_jsonl(DICT_ZO_EN)
        self.bible_verses = self._load_jsonl(BIBLE_CORPUS)
        self.wiki_phrases = self._load_json(WIKI_PHRASES) if WIKI_PHRASES.exists() else {}
        self.grammar = self._load_json(GRAMMAR_PATTERNS) if GRAMMAR_PATTERNS.exists() else {}

    def _load_jsonl(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        data = []
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    data.append(json.loads(line))
        return data

    def _load_json(self, path: Path) -> dict:
        if not path.exists():
            return {}
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def extract_zolai_words(self, text: str) -> list[str]:
        """Extract potential Zolai words from user input."""
        # Common Zolai patterns: CV, CVC, CCVC syllables
        words = re.findall(r'\b[a-z][a-z]*\b', text.lower())
        # Filter out common English words
        english_stop = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'what', 'how',
                       'do', 'does', 'did', 'can', 'could', 'will', 'would', 'should',
                       'i', 'you', 'he', 'she', 'it', 'we', 'they', 'my', 'your', 'his',
                       'this', 'that', 'these', 'those', 'in', 'on', 'at', 'to', 'for',
                       'and', 'or', 'but', 'not', 'no', 'yes', 'hello', 'hi', 'hey'}
        return [w for w in words if w not in english_stop and len(w) >= 2]

    def lookup_dictionary(self, word: str, limit: int = 3) -> list[dict]:
        """Look up a word in the Zolai→English dictionary."""
        if self._db:
            results = self._db.lookup_word(word)
            return results[:limit]
        # JSONL fallback
        results = []
        for entry in self.dict_zo_en:
            zolai = entry.get('zolai', '').lower()
            english = entry.get('english', '').lower()
            if word in zolai or word in english:
                results.append(entry)
                if len(results) >= limit:
                    break
        return results

    def find_bible_examples(self, word: str, limit: int = 3) -> list[dict]:
        """Find Bible verses containing the word."""
        if self._db:
            results = self._db.search_bible(word)
            return [
                {
                    'reference': r.get('ref', ''),
                    'zolai': r.get('zo_tdb77', ''),
                    'english': r.get('en_kJV', ''),
                }
                for r in results[:limit]
            ]
        # JSONL fallback
        results = []
        for verse in self.bible_verses:
            zo = verse.get('zo', '').lower()
            en = verse.get('english', '').lower()
            if word in zo or word in en:
                results.append({
                    'reference': verse.get('reference', ''),
                    'zolai': verse.get('zo', ''),
                    'english': verse.get('english', '')
                })
                if len(results) >= limit:
                    break
        return results

    def search_phrases(self, word: str, limit: int = 3) -> list[dict]:
        """Search phrases containing the word."""
        if self._db:
            results = self._db.match_phrase(word)
            return results[:limit]
        # JSONL fallback (wiki_phrases is a dict)
        matches = []
        for phrase, meaning in self.wiki_phrases.items():
            if word in phrase.lower():
                matches.append({'zolai': phrase, 'english': meaning})
                if len(matches) >= limit:
                    break
        return matches

    def get_grammar_hint(self, word: str) -> Optional[str]:
        """Get grammar pattern for a word."""
        # Check if word appears in any grammar pattern
        for pattern in self.grammar.get('patterns', []):
            if word in pattern.get('example', '').lower():
                return pattern.get('rule', '')
        return None

    def _load_myanmar_dict(self) -> None:
        """Lazy-load Myanmar dictionary for cross-language lookups."""
        if self._myanmar_dict is not None:
            return
        self._myanmar_dict = {}
        if MYANMAR_DICT.exists():
            try:
                with open(MYANMAR_DICT, 'r', encoding='utf-8') as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            my = entry.get("myanmar", "")
                            if my:
                                self._myanmar_dict[my] = entry
                        except Exception:
                            continue
            except Exception:
                self._myanmar_dict = {}

    def lookup_myanmar(self, query: str, limit: int = 10) -> list[dict]:
        """Search Myanmar dictionary for a query string."""
        self._load_myanmar_dict()
        results: list[dict] = []
        q = query.lower()
        for my, entry in self._myanmar_dict.items():
            if q in my.lower():
                results.append(entry)
                if len(results) >= limit:
                    break
        return results

    def translate_zo_my(self, word: str) -> Optional[dict]:
        """Translate Zolai → Myanmar using database or dictionary."""
        if self._db:
            result = self._db.translate_zo_my(word)
            if result:
                return result
        # JSONL fallback
        for entry in self.dict_zo_en:
            if entry.get('zolai', '').lower() == word.lower():
                my = entry.get('myanmar', '')
                if my:
                    return {
                        'zolai': entry.get('zolai', ''),
                        'myanmar': my,
                        'english': entry.get('english', ''),
                    }
        return None

    def translate_my_zo(self, word: str) -> Optional[dict]:
        """Translate Myanmar → Zolai using database or dictionary."""
        if self._db:
            result = self._db.translate_my_zo(word)
            if result:
                return result
        # JSONL fallback
        self._load_myanmar_dict()
        for my, entry in self._myanmar_dict.items():
            if my.lower() == word.lower():
                return {
                    'myanmar': my,
                    'zolai': entry.get('zolai', ''),
                    'english': entry.get('english', ''),
                }
        return None

    def search_myanmar_bible(self, query: str, limit: int = 5) -> list[dict]:
        """Search Judson Bible by Myanmar text via database."""
        if self._db:
            results = self._db.search_judson(query, limit=limit)
            return results
        return []

    def _build_myanmar_context(self, text: str, max_tokens: int = 500) -> str:
        """Build RAG context for Myanmar script input."""
        context_parts: list[str] = []
        token_estimate = 0

        # Dictionary lookup
        my_results = self.lookup_myanmar(text, limit=5)
        if my_results:
            dict_section = "## Myanmar Dictionary\n"
            for r in my_results[:5]:
                entry = (
                    f"- **{r.get('myanmar', '?')}** → "
                    f"ZO: {r.get('zolai', '?')}, EN: {r.get('english', '?')}\n"
                )
                dict_section += entry
                token_estimate += len(entry.split())
            context_parts.append(dict_section)

        # Bible search
        bible_results = self.search_myanmar_bible(text, limit=3)
        if bible_results:
            bible_section = "## Myanmar Bible\n"
            for v in bible_results[:3]:
                entry = (
                    f"- **{v.get('ref', '?')}**\n"
                    f"  MY: {v.get('myanmar', '')}\n"
                    f"  ZO: {v.get('zo_tdb77', '')}\n"
                    f"  EN: {v.get('en_kjv', '')}\n"
                )
                bible_section += entry
                token_estimate += len(entry.split())
            context_parts.append(bible_section)

        # Truncate if over token limit
        final_context = "\n".join(context_parts)
        if token_estimate > max_tokens and context_parts:
            final_context = context_parts[0]
        return final_context if final_context else "No Myanmar context found."

    def build_context(self, user_input: str, max_tokens: int = 500) -> str:
        """Build concise RAG context for user input."""
        # Detect if input contains Myanmar script (U+1000–U+109F)
        has_myanmar = any('\u1000' <= c <= '\u109f' for c in user_input)

        if has_myanmar:
            return self._build_myanmar_context(user_input, max_tokens)

        words = self.extract_zolai_words(user_input)
        context_parts = []
        token_estimate = 0

        # Dictionary lookups
        dict_results = []
        for word in words[:3]:  # Limit to 3 words
            results = self.lookup_dictionary(word, limit=2)
            dict_results.extend(results)

        if dict_results:
            dict_section = "## Dictionary\n"
            for r in dict_results[:5]:  # Max 5 entries
                entry = f"- **{r.get('zolai', '?')}** → {r.get('english', '?')} ({r.get('pos', '?')})\n"
                dict_section += entry
                token_estimate += len(entry.split())
            context_parts.append(dict_section)

        # Bible examples
        bible_results = []
        for word in words[:2]:  # Only top 2 words
            verses = self.find_bible_examples(word, limit=2)
            bible_results.extend(verses)

        if bible_results:
            bible_section = "## Bible Examples\n"
            for v in bible_results[:3]:  # Max 3 verses
                entry = f"- **{v['zolai']}**\n  EN: {v['english']}\n  Ref: {v['reference']}\n"
                bible_section += entry
                token_estimate += len(entry.split())
            context_parts.append(bible_section)

        # Grammar hints
        grammar_hints = []
        for word in words[:2]:
            hint = self.get_grammar_hint(word)
            if hint:
                grammar_hints.append(f"- {word}: {hint}")

        if grammar_hints:
            grammar_section = "## Grammar\n" + "\n".join(grammar_hints) + "\n"
            context_parts.append(grammar_section)
            token_estimate += len(grammar_section.split())

        # Wiki phrases
        phrase_matches = []
        for phrase, meaning in self.wiki_phrases.items():
            if any(w in phrase.lower() for w in words):
                phrase_matches.append(f"- {phrase} = {meaning}")

        if phrase_matches:
            phrase_section = "## Phrases\n" + "\n".join(phrase_matches[:3]) + "\n"
            context_parts.append(phrase_section)
            token_estimate += len(phrase_section.split())

        # Myanmar translations (cross-language)
        myanmar_matches = []
        for word in words[:2]:
            my_result = self.translate_zo_my(word)
            if my_result:
                myanmar_matches.append(
                    f"- {my_result.get('zolai', word)} → "
                    f"{my_result.get('myanmar', '?')} ({my_result.get('english', '?')})"
                )

        if myanmar_matches:
            my_section = "## Myanmar\n" + "\n".join(myanmar_matches[:3]) + "\n"
            context_parts.append(my_section)
            token_estimate += len(my_section.split())

        # Truncate if over token limit
        final_context = "\n".join(context_parts)
        if token_estimate > max_tokens:
            # Keep only dictionary + 1 Bible verse
            final_context = context_parts[0] if context_parts else ""
            if len(context_parts) > 1:
                final_context += context_parts[1].split("\n")[0] + "\n"

        return final_context if final_context else "No Zolai context found."

# Singleton instance
_rag_instance: Optional[ZolaiRAGContext] = None

def get_rag_context() -> ZolaiRAGContext:
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = ZolaiRAGContext()
    return _rag_instance

def build_zolai_context(user_input: str, max_tokens: int = 500) -> str:
    """Main entry point: build RAG context for user input."""
    return get_rag_context().build_context(user_input, max_tokens)
