"""
RAG Context Injector for Zolai Bilingual Conversation.

Extracts Zolai words from user input, looks up the dictionary, finds Bible
examples, and injects concise context (<500 tokens). All reads go through the
canonical DB (DatabaseManager) — no runtime JSONL.
"""
from pathlib import Path
from typing import Optional

from ..config import config
from ..data.database import get_manager

DATA_DIR = Path(__file__).parent.parent.parent.parent.parent / "data"


class ZolaiRAGContext:
    """Lightweight RAG context injector for Zolai conversations."""

    def __init__(self):
        # Database-backed access (canonical data store).
        db_path = config.paths.data / "zolai.db"
        self._db = get_manager(f"sqlite:///{db_path}")

    def extract_zolai_words(self, text: str) -> list[str]:
        """Extract potential Zolai words from user input."""
        import re

        words = re.findall(r'\b[a-z][a-z]*\b', text.lower())
        english_stop = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'what', 'how',
                       'do', 'does', 'did', 'can', 'could', 'will', 'would', 'should',
                       'i', 'you', 'he', 'she', 'it', 'we', 'they', 'my', 'your', 'his',
                       'this', 'that', 'these', 'those', 'in', 'on', 'at', 'to', 'for',
                       'and', 'or', 'but', 'not', 'no', 'yes', 'hello', 'hi', 'hey'}
        return [w for w in words if w not in english_stop and len(w) >= 2]

    def lookup_dictionary(self, word: str, limit: int = 3) -> list[dict]:
        """Look up a word in the Zolai→English dictionary (DB)."""
        return self._db.lookup_word(word)[:limit]

    def find_bible_examples(self, word: str, limit: int = 3) -> list[dict]:
        """Find Bible verses containing the word (DB)."""
        results = self._db.search_bible(word)
        return [
            {
                'reference': r.get('ref', ''),
                'zolai': r.get('zo_tdb77', ''),
                'english': r.get('en_kJV', ''),
            }
            for r in results[:limit]
        ]

    def search_phrases(self, word: str, limit: int = 3) -> list[dict]:
        """Search phrases containing the word (DB)."""
        return self._db.match_phrase(word)[:limit]

    def get_grammar_hint(self, word: str) -> Optional[str]:
        """Get grammar pattern description for a word (DB)."""
        try:
            results = self._db.get_grammar(word)
        except Exception:
            return None
        for r in results:
            return r.get("rule") or r.get("pattern") or r.get("description", "")
        return None

    def lookup_myanmar(self, query: str, limit: int = 10) -> list[dict]:
        """Search Myanmar dictionary for a query string (DB)."""
        return self._db.lookup_myanmar(query, limit=limit)

    def translate_zo_my(self, word: str) -> Optional[dict]:
        """Translate Zolai → Myanmar using the database."""
        return self._db.translate_zo_my(word)

    def translate_my_zo(self, word: str) -> Optional[dict]:
        """Translate Myanmar → Zolai using the database."""
        return self._db.translate_my_zo(word)

    def search_myanmar_bible(self, query: str, limit: int = 5) -> list[dict]:
        """Search Judson Bible by Myanmar text via database."""
        return self._db.search_judson(query, limit=limit)

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

        final_context = "\n".join(context_parts)
        if token_estimate > max_tokens and context_parts:
            final_context = context_parts[0]
        return final_context if final_context else "No Myanmar context found."

    def build_context(self, user_input: str, max_tokens: int = 500) -> str:
        """Build concise RAG context for user input."""
        has_myanmar = any('\u1000' <= c <= '\u109f' for c in user_input)

        if has_myanmar:
            return self._build_myanmar_context(user_input, max_tokens)

        words = self.extract_zolai_words(user_input)
        context_parts = []
        token_estimate = 0

        dict_results = []
        for word in words[:3]:
            results = self.lookup_dictionary(word, limit=2)
            dict_results.extend(results)

        if dict_results:
            dict_section = "## Dictionary\n"
            for r in dict_results[:5]:
                entry = f"- **{r.get('zolai', '?')}** → {r.get('english', '?')} ({r.get('pos', '?')})\n"
                dict_section += entry
                token_estimate += len(entry.split())
            context_parts.append(dict_section)

        bible_results = []
        for word in words[:2]:
            verses = self.find_bible_examples(word, limit=2)
            bible_results.extend(verses)

        if bible_results:
            bible_section = "## Bible Examples\n"
            for v in bible_results[:3]:
                entry = f"- **{v['zolai']}**\n  EN: {v['english']}\n  Ref: {v['reference']}\n"
                bible_section += entry
                token_estimate += len(entry.split())
            context_parts.append(bible_section)

        grammar_hints = []
        for word in words[:2]:
            hint = self.get_grammar_hint(word)
            if hint:
                grammar_hints.append(f"- {word}: {hint}")

        if grammar_hints:
            grammar_section = "## Grammar\n" + "\n".join(grammar_hints) + "\n"
            context_parts.append(grammar_section)
            token_estimate += len(grammar_section.split())

        phrase_matches = []
        for word in words:
            for p in self.search_phrases(word, limit=2):
                phrase_matches.append(f"- {p.get('zolai', '')} = {p.get('english', '')}")
        if phrase_matches:
            phrase_section = "## Phrases\n" + "\n".join(phrase_matches[:3]) + "\n"
            context_parts.append(phrase_section)
            token_estimate += len(phrase_section.split())

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

        final_context = "\n".join(context_parts)
        if token_estimate > max_tokens:
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
