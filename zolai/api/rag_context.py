"""
RAG Context Injector for Zolai Bilingual Conversation.

Extracts Zolai words from user input, looks up dictionary,
finds Bible examples, and injects concise context (<500 tokens).
"""
import json
import re
from pathlib import Path
from typing import Optional

# Data paths (shared across repos)
DATA_DIR = Path(__file__).parent.parent.parent.parent.parent / "data"
DICT_ZO_EN = DATA_DIR / "dictionary" / "dict_zo_en_clean.jsonl"
BIBLE_CORPUS = DATA_DIR / "bible" / "parallel_corpus.jsonl"
WIKI_PHRASES = DATA_DIR / "wiki" / "common_phrases.json"
GRAMMAR_PATTERNS = DATA_DIR / "wiki" / "grammar_patterns.json"

class ZolaiRAGContext:
    """Lightweight RAG context injector for Zolai conversations."""

    def __init__(self):
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

    def get_grammar_hint(self, word: str) -> Optional[str]:
        """Get grammar pattern for a word."""
        # Check if word appears in any grammar pattern
        for pattern in self.grammar.get('patterns', []):
            if word in pattern.get('example', '').lower():
                return pattern.get('rule', '')
        return None

    def build_context(self, user_input: str, max_tokens: int = 500) -> str:
        """Build concise RAG context for user input."""
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
