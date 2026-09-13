"""
RAG Context V2 — integrates ALL data sources for comprehensive context.
Uses: Bible + dictionary + corpus + grammar + knowledge from the canonical DB.
Token-efficient: <500 tokens per context injection. No runtime JSONL.
"""
import re
from pathlib import Path
from typing import Optional

from ..config import config
from ..data.database import get_manager
from ..data.repositories import get_repositories

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"


class ZolaiRAGContextV2:
    """Comprehensive RAG context using all data sources (DB-first)."""

    def __init__(self):
        self._db = get_manager(f"sqlite:///{config.paths.zolai_db}")
        self._repos = get_repositories()

    def extract_zolai_words(self, text: str) -> list[str]:
        words = re.findall(r"\b[a-z][a-z]*\b", text.lower())
        english_stop = {
            "the", "a", "an", "is", "are", "was", "were", "what", "how",
            "do", "does", "did", "can", "could", "will", "would", "should",
            "i", "you", "he", "she", "it", "we", "they", "my", "your", "his",
            "this", "that", "these", "those", "in", "on", "at", "to", "for",
            "and", "or", "but", "not", "no", "yes", "hello", "hi", "hey",
        }
        return [w for w in words if w not in english_stop and len(w) >= 2]

    def lookup_dictionary(self, word: str, limit: int = 3) -> list[dict]:
        return self._db.lookup_word(word)[:limit]

    def find_bible_examples(self, word: str, limit: int = 3) -> list[dict]:
        results = self._db.search_bible(word)
        return [
            {
                "reference": r.get("ref", ""),
                "zolai": r.get("zo_tdb77", ""),
                "english": r.get("en_kJV", ""),
                "source": "bible",
            }
            for r in results[:limit]
        ]

    def find_parallel_examples(self, word: str, limit: int = 3) -> list[dict]:
        results = []
        w = word.lower()
        for r in self._repos["translation"].find(limit=1000):
            zo = str(r.get("target") or "")
            en = str(r.get("source") or "")
            if w in zo.lower() or w in en.lower():
                results.append({"zolai": zo, "english": en, "source": "parallel"})
                if len(results) >= limit:
                    break
        return results

    def lookup_vocab(self, word: str, limit: int = 2) -> list[dict]:
        w = word.lower()
        results = []
        for r in self._repos["vocabulary"].find({"headword": w}, limit=50):
            results.append(r)
            if len(results) >= limit:
                break
        return results

    def build_context(self, user_input: str, max_tokens: int = 500) -> str:
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

        parallel_results = []
        for word in words[:2]:
            pairs = self.find_parallel_examples(word, limit=2)
            parallel_results.extend(pairs)

        if parallel_results:
            parallel_section = "## Examples\n"
            for p in parallel_results[:3]:
                entry = f"- **{p['zolai']}**\n  EN: {p['english']}\n"
                parallel_section += entry
                token_estimate += len(entry.split())
            context_parts.append(parallel_section)

        vocab_results = []
        for word in words[:2]:
            v_entries = self.lookup_vocab(word, limit=1)
            vocab_results.extend(v_entries)

        if vocab_results:
            vocab_section = "## Vocabulary\n"
            for v in vocab_results[:3]:
                text = v.get("headword") or v.get("text") or str(v)
                if len(text) > 100:
                    text = text[:100] + "..."
                entry = f"- {text}\n"
                vocab_section += entry
                token_estimate += len(entry.split())
            context_parts.append(vocab_section)

        final_context = "\n".join(context_parts)
        if token_estimate > max_tokens:
            final_context = context_parts[0] if context_parts else ""
            if len(context_parts) > 1:
                final_context += context_parts[1].split("\n")[0] + "\n"

        return final_context if final_context else "No Zolai context found."

    def get_stats(self) -> dict:
        return {
            "dict_entries": self._repos["dictionary"].count(),
            "bible_verses": self._repos["bible"].count(),
            "parallel_pairs": self._repos["translation"].count(),
            "grammar_patterns": self._repos["grammar"].count(),
            "vocab_entries": self._repos["vocabulary"].count(),
        }


_rag_v2_instance: Optional[ZolaiRAGContextV2] = None


def get_rag_context_v2() -> ZolaiRAGContextV2:
    global _rag_v2_instance  # noqa: PLW0603
    if _rag_v2_instance is None:
        _rag_v2_instance = ZolaiRAGContextV2()
    return _rag_v2_instance


def build_zolai_context_v2(user_input: str, max_tokens: int = 500) -> str:
    return get_rag_context_v2().build_context(user_input, max_tokens)
