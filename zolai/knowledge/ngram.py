"""Zolai word + bigram prediction tables.

Builds word-frequency and bigram count tables from the dictionary headwords and
bilingual wordlists. Output is stored in the ``ngram`` table (DB-first); the
same records may also be exported to newline-delimited JSON for downstream
tooling. Prediction reads come from the repository layer, never raw JSONL.
"""
from __future__ import annotations

import functools
import json
import re
from collections import Counter
from pathlib import Path

from ..config import config
from ..data.repositories import get_engine
from ..data.repositories.extended import NgramRepository

ART = config.paths.data_knowledge
DICT = config.paths.data / "dictionary" / "processed" / "dict_master_v2.json"
WORDLISTS = config.paths.root.parent / "zolai-wiki" / "vocabulary" / "wordlists"

_WORD = re.compile(r"[a-zA-Z][a-zA-Z'']*")


def build_ngram_tables(out_dir=ART) -> Path:
    """Extract unigrams + bigrams and persist to the ``ngram`` table (and JSONL)."""
    unigrams: Counter[str] = Counter()
    bigrams: Counter[tuple[str, str]] = Counter()

    # 1. dictionary headwords as vocabulary
    dict_words: list[str] = []
    if DICT.exists():
        try:
            data = json.loads(DICT.read_text(encoding="utf-8"))
            dict_words = list(data.keys())
        except Exception as e:
            print(f"  ! dict read failed: {e}")

    # 2. wordlist rows (zolai column) as usage-bearing sentences (bigram source)
    sentence_texts: list[str] = []
    if WORDLISTS.exists():
        for tsv in sorted(WORDLISTS.glob("*.tsv")):
            try:
                lines = tsv.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for line in lines[1:]:
                if "\t" not in line:
                    continue
                cols = line.split("\t")
                unigrams[cols[0].strip().lower()] += 1  # zolai headword
                if len(cols) > 4 and cols[4].strip():
                    sentence_texts.append(cols[4].strip())  # example sentence

    for tok in dict_words:
        norm = tok.strip().lower()
        if 2 < len(norm) <= 30:
            unigrams[norm] += 1

    for s in sentence_texts:
        toks = _WORD.findall(s.lower())
        for a, b in zip(toks, toks[1:]):
            bigrams[(a, b)] += 1

    tables = {
        "unigrams": dict(unigrams),
        "bigrams": {f"{a}\t{b}": c for (a, b), c in bigrams.items()},
    }
    # Persist to the ngram table (DB-first serving path).
    _save_tables_to_db(tables, bigrams)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "ngrams.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for (a, b), cnt in bigrams.most_common():
            f.write(json.dumps({"type": "bigram", "a": a, "b": b, "count": cnt}, ensure_ascii=False) + "\n")
        for tok, cnt in unigrams.most_common():
            f.write(json.dumps({"type": "unigram", "word": tok, "count": cnt}, ensure_ascii=False) + "\n")

    print(f"ngram: {len(unigrams)} unigrams, {len(bigrams)} bigrams -> DB + {out_path}")
    return out_path


def _save_tables_to_db(tables: dict, keyed_bigrams: Counter) -> None:
    """Persist unigram/bigram tables to the ``ngram`` table (idempotent build)."""
    engine = get_engine()
    repo = NgramRepository(engine)
    # Re-shape bigrams back to (a, b) tuples for the repository save.
    unigrams = tables.get("unigrams", {})
    bigrams = {
        (a, b): c for (a, b), c in keyed_bigrams.items()
    }
    repo.save_tables({"unigrams": unigrams, "bigrams": bigrams})


@functools.lru_cache(maxsize=1)
def load_ngram_tables(path: str | None = None) -> dict[str, dict]:
    """Load ngram tables from the canonical ``ngram`` table.

    Returns {"unigrams": {word: count}, "bigrams": {(a, b): count}}.
    Returns empty dicts if the table is empty.

    ``path`` is accepted only for backward compatibility with callers that used
    to pass a JSONL path; it is ignored and the DB is read instead.
    """
    try:
        engine = get_engine()
        ngram = NgramRepository(engine)
        tables = ngram.as_tables()
    except Exception:
        return {"unigrams": {}, "bigrams": {}}
    return tables


def predict_next(
    word: str, top_k: int = 5, tables: dict | None = None
) -> list[tuple[str, int]]:
    """Predict the most likely next word after `word` using bigram counts.

    Finds all bigrams where the first token equals `word`, sorts by count
    descending, and returns the top_k results as (next_word, count).
    Falls back to top unigrams if no bigrams match. Returns an empty list
    if tables are empty.

    Example:
        >>> tables = load_ngram_tables("artifacts/kg/ngrams.jsonl")
        >>> predict_next("uh", top_k=3, tables=tables)[:2]
        [('hi', 1849), ...]
    """
    if tables is None:
        tables = load_ngram_tables()
    unigrams = tables.get("unigrams", {})
    bigrams = tables.get("bigrams", {})
    if not unigrams and not bigrams:
        return []
    word = word.lower()
    matches = [(b, c) for (a, b), c in bigrams.items() if a == word]
    matches.sort(key=lambda x: x[1], reverse=True)
    if matches:
        return matches[:top_k]
    # Fallback: top unigrams
    top = sorted(unigrams.items(), key=lambda x: x[1], reverse=True)[:top_k]
    return [(w, c) for w, c in top]


def predict_completion(
    prefix: str, top_k: int = 5, tables: dict | None = None
) -> list[tuple[str, float]]:
    """Generate word completions for a prefix by chaining bigram predictions.

    Tokenizes `prefix`, then greedily extends using predict_next up to 20
    tokens. Returns the top_k completions as (completion_text, score),
    where score is the sum of bigram counts along the chain.

    Example:
        >>> tables = load_ngram_tables("artifacts/kg/ngrams.jsonl")
        >>> results = predict_completion("le", top_k=3, tables=tables)
        >>> len(results) > 0
        True
    """
    if tables is None:
        tables = load_ngram_tables()
    unigrams = tables.get("unigrams", {})
    bigrams = tables.get("bigrams", {})
    if not unigrams and not bigrams:
        return []
    tokens = prefix.lower().split()
    if not tokens:
        return []
    # Start from the last token in the prefix
    current = tokens[-1]
    chain = list(tokens)
    score = 0.0
    for _ in range(20 - len(tokens)):
        next_words = predict_next(current, top_k=1, tables=tables)
        if not next_words:
            break
        next_word, cnt = next_words[0]
        chain.append(next_word)
        score += cnt
        current = next_word
    completion_text = " ".join(chain)
    return [(completion_text, score)][:top_k]


def suggest_corrections(
    word: str, top_k: int = 3, tables: dict | None = None
) -> list[tuple[str, int]]:
    """Suggest spelling corrections for `word` via Levenshtein distance.

    Compares `word` against unigram keys that share the first two characters
    (prefix filter for speed). Returns the top_k closest matches as
    (candidate, distance). Returns an empty list if tables are empty or
    no candidates are found.

    Example:
        >>> tables = load_ngram_tables("artifacts/kg/ngrams.jsonl")
        >>> suggest_corrections("khi", top_k=3, tables=tables)
        [('khi', 0), ...]
    """
    if tables is None:
        tables = load_ngram_tables()
    unigrams = tables.get("unigrams", {})
    if not unigrams:
        return []
    word = word.lower()
    prefix = word[:2] if len(word) >= 2 else word
    candidates = [w for w in unigrams if w.startswith(prefix)]
    if not candidates:
        # Widen search: all unigrams
        candidates = list(unigrams.keys())
    distances = [(w, _levenshtein(word, w)) for w in candidates]
    distances.sort(key=lambda x: x[1])
    return distances[:top_k]


def _levenshtein(a: str, b: str) -> int:
    """Compute the Levenshtein distance between two strings (stdlib only)."""
    m, n = len(a), len(b)
    if m == 0:
        return n
    if n == 0:
        return m
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            temp = dp[j]
            if a[i - 1] == b[j - 1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j - 1])
            prev = temp
    return dp[n]
