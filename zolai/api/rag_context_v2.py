"""
RAG Context V2 — integrates all data sources for comprehensive context.

Uses: Bible + dictionary + corpus + grammar patterns + knowledge vectors.
Token-efficient: <500 tokens per context injection.
"""
import json
import re
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"


class ZolaiRAGContextV2:
    """Comprehensive RAG context using all data sources."""

    def __init__(self):
        self.dict_zo_en = self._load_jsonl(DATA_DIR / "dictionary" / "processed" / "dict_zo_en_clean.jsonl")
        self.bible = self._load_jsonl(DATA_DIR / "bible" / "parallel_corpus_v1.jsonl")
        self.parallel = self._load_jsonl(DATA_DIR / "parallel" / "zo_en_pairs_combined_v1.jsonl")
        grammar_path = DATA_DIR / "bible" / "grammar_patterns_v2.jsonl"
        self.grammar = self._load_jsonl(grammar_path) if grammar_path.exists() else []

    def _load_jsonl(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        data = []
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        data.append(json.loads(line))
                    except Exception:
                        continue
        return data

    def _load_json(self, path: Path) -> dict:
        if not path.exists():
            return {}
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def extract_zolai_words(self, text: str) -> list[str]:
        """Extract potential Zolai words from user input."""
        words = re.findall(r'\b[a-z][a-z]*\b', text.lower())
        english_stop = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'what', 'how',
                       'do', 'does', 'did', 'can', 'could', 'will', 'would', 'should',
                       'i', 'you', 'he', 'she', 'it', 'we', 'they', 'my', 'your', 'his',
                       'this', 'that', 'these', 'those', 'in', 'on', 'at', 'to', 'for',
                       'and', 'or', 'but', 'not', 'no', 'yes', 'hello', 'hi', 'hey'}
        return [w for w in words if w not in english_stop and len(w) >= 2]

    def lookup_dictionary(self, word: str, limit: int = 3) -> list[dict]:
        """Look up a word in the dictionary."""
        results = []
        for entry in self.dict_zo_en:
            zolai = str(entry.get('zolai', '')).lower()
            english = str(entry.get('english', '')).lower()
            if word in zolai or word in english:
                results.append(entry)
                if len(results) >= limit:
                    break
        return results

    def find_bible_examples(self, word: str, limit: int = 3) -> list[dict]:
        """Find Bible verses containing the word."""
        results = []
        for verse in self.bible:
            zo = verse.get('zo_tdb77') or ''
            en = verse.get('en_kJV') or ''
            if word in zo.lower() or word in en.lower():
                results.append({
                    'reference': verse.get('ref', ''),
                    'zolai': zo,
                    'english': en,
                    'source': 'bible'
                })
                if len(results) >= limit:
                    break
        return results

    def find_parallel_examples(self, word: str, limit: int = 3) -> list[dict]:
        """Find parallel corpus examples containing the word."""
        results = []
        for pair in self.parallel:
            zo = pair.get('zolai', '')
            en = pair.get('english', '')
            if word in zo.lower() or word in en.lower():
                results.append({
                    'zolai': zo,
                    'english': en,
                    'source': 'parallel'
                })
                if len(results) >= limit:
                    break
        return results

    def build_context(self, user_input: str, max_tokens: int = 500) -> str:
        """Build comprehensive RAG context for user input."""
        words = self.extract_zolai_words(user_input)
        context_parts = []
        token_estimate = 0

        # Dictionary lookups (highest priority)
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

        # Bible examples (high priority)
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

        # Parallel examples (medium priority)
        parallel_results = []
        for word in words[:2]:
            pairs = self.find_parallel_examples(word, limit=2)
            parallel_results.extend(pairs)

        if parallel_results:
            parallel_section = "## Parallel Examples\n"
            for p in parallel_results[:3]:
                entry = f"- **{p['zolai']}**\n  EN: {p['english']}\n"
                parallel_section += entry
                token_estimate += len(entry.split())
            context_parts.append(parallel_section)

        # Truncate if over token limit
        final_context = "\n".join(context_parts)
        if token_estimate > max_tokens:
            # Keep only dictionary + 1 Bible verse
            final_context = context_parts[0] if context_parts else ""
            if len(context_parts) > 1:
                final_context += context_parts[1].split("\n")[0] + "\n"

        return final_context if final_context else "No Zolai context found."


# Singleton instance
_rag_v2_instance: Optional[ZolaiRAGContextV2] = None

def get_rag_context_v2() -> ZolaiRAGContextV2:
    global _rag_v2_instance
    if _rag_v2_instance is None:
        _rag_v2_instance = ZolaiRAGContextV2()
    return _rag_v2_instance

def build_zolai_context_v2(user_input: str, max_tokens: int = 500) -> str:
    """Main entry point: build comprehensive RAG context."""
    return get_rag_context_v2().build_context(user_input, max_tokens)
