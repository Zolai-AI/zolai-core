"""
Word Attestation — verifies Zolai words exist in Bible or dictionary.

CRITICAL: The AI must NEVER use unattested words.
Lazy loading: data loads on first query, not at import.
Set ZOLAI_LOAD_CORPUS=1 to include  corpus (~208MB, slow load).

§27 (Phase 2) — cold-start fix, additive only; public API shapes unchanged:

- ``attestation_index(word, source)`` is materialized by
  :func:`build_attestation_index` from the *same* loader queries the
  in-memory sets use.  When it is populated (and the opt-in corpus flag is
  off) the sets are filled from that index instead of re-tokenizing the
  source tables — same content by construction, one indexed read per source.
- a bounded LRU membership cache makes repeat ``attest_word`` calls O(1);
  only the source *tuple* is cached, so the returned dict still carries the
  caller's original casing.
- an optional Bloom artifact (``data/attestation/bloom-v1.{json,bin}``,
  blake2b) short-circuits *negative* lookups only: "definitely absent" is
  final, "maybe" falls through to the exact set check — verdicts can never
  change.
- ``ZOLAI_LOAD_CORPUS=1`` bypasses both the index and the Bloom shortcut:
  the optional file corpus is not indexed, and the flag keeps every lookup
  on the (byte-identical) query path.
"""
import hashlib
import json
import math
import os
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Optional

from ..shared.text import tokenize_words

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"

#: (index source, attribute on :class:`WordAttestation`) — the four loaders.
_INDEX_SOURCES: tuple[tuple[str, str], ...] = (
    ("bible", "bible_words"),
    ("dict", "dict_words"),
    ("corpus", "corpus_words"),
    ("extra", "_words"),
)

#: Bounded membership cache (mirrors the rate-limiter prune pattern).
_CACHE_MAX = 4096

#: Bloom artifact naming (plan §27: ``bloom-v1.{bin,json}``).
_BLOOM_NAME = "bloom-v1"
_BLOOM_VERSION = 1
_BLOOM_FP_RATE = 0.01


# ---------------------------------------------------------------------------
# §27 — negative-only Bloom short-circuit
# ---------------------------------------------------------------------------


class _BloomFilter:
    """blake2b Bloom filter.

    Only :meth:`maybe` is exposed to the verdict path and it is used one way:
    a ``False`` answer is *definitely absent* (Bloom filters have no false
    negatives), a ``True`` answer falls through to the exact set membership
    check — so a present artifact can never change a verdict.
    """

    __slots__ = ("bits", "hashes", "count", "_data")

    def __init__(self, bits: int, hashes: int, count: int, data: bytearray) -> None:
        self.bits = bits
        self.hashes = hashes
        self.count = count
        self._data = data

    @staticmethod
    def _indexes(word: str, bits: int, hashes: int) -> Iterable[int]:
        digest = hashlib.blake2b(word.encode("utf-8"), digest_size=16).digest()
        h1 = int.from_bytes(digest[:8], "big")
        # odd stride → full coverage under a power-of-two modulus
        h2 = int.from_bytes(digest[8:], "big") | 1
        for i in range(hashes):
            yield (h1 + i * h2) % bits

    @classmethod
    def build(cls, words: Iterable[str], *, false_positive_rate: float = _BLOOM_FP_RATE) -> "_BloomFilter":
        unique = set(words)
        count = len(unique)
        if count == 0:
            bits, hashes = 1024, 1
        else:
            raw = -count * math.log(false_positive_rate) / (math.log(2) ** 2)
            bits = max(1024, 1 << (int(math.ceil(raw)) - 1).bit_length())
            hashes = max(1, round((bits / count) * math.log(2)))
        bloom = cls(bits, hashes, count, bytearray((bits + 7) // 8))
        for word in unique:
            bloom.add(word)
        return bloom

    @classmethod
    def load(cls, json_path: Path) -> Optional["_BloomFilter"]:
        """Read a saved artifact; any mismatch/IO problem → ``None`` (no-op)."""
        try:
            meta = json.loads(json_path.read_text(encoding="utf-8"))
            if meta.get("version") != _BLOOM_VERSION or meta.get("algorithm") != "blake2b":
                return None
            data = bytearray((json_path.parent / meta["bin"]).read_bytes())
            bits = int(meta["bits"])
            hashes = int(meta["hashes"])
            if len(data) != (bits + 7) // 8:
                return None
            return cls(bits, hashes, int(meta.get("count", 0)), data)
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def add(self, word: str) -> None:
        for idx in self._indexes(word, self.bits, self.hashes):
            self._data[idx >> 3] |= 1 << (idx & 7)

    def maybe(self, word: str) -> bool:
        """``False`` = definitely absent; ``True`` = maybe present."""
        for idx in self._indexes(word, self.bits, self.hashes):
            if not self._data[idx >> 3] & (1 << (idx & 7)):
                return False
        return True

    def save(self, json_path: Path) -> tuple[Path, Path]:
        bin_path = json_path.with_suffix(".bin")
        bin_path.write_bytes(bytes(self._data))
        json_path.write_text(
            json.dumps(
                {
                    "version": _BLOOM_VERSION,
                    "algorithm": "blake2b",
                    "bits": self.bits,
                    "hashes": self.hashes,
                    "count": self.count,
                    "bin": bin_path.name,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return json_path, bin_path


def _bloom_json_path(out_dir: Path | None = None) -> Path:
    base = Path(out_dir) if out_dir is not None else DATA_DIR / "attestation"
    return base / f"{_BLOOM_NAME}.json"


class WordAttestation:
    """Verify if a Zolai word is attested in Bible, dictionary, or corpus."""

    def __init__(self, *, db_path=None, prefer_index: bool = True) -> None:
        self.bible_words: set[str] = set()
        self.dict_words: set[str] = set()
        self.corpus_words: set[str] = set()
        self._words: set[str] = set()
        self._loaded = False
        #: "index" / "queries" once loaded — provenance for callers and tests.
        self.loaded_via = ""
        self._db_path = str(db_path) if db_path is not None else None
        self._prefer_index = prefer_index
        # LRU membership cache: key = normalized word, value = source tuple.
        self._membership_cache: dict[str, tuple[bool, bool, bool, bool]] = {}
        self._stats_cache: Optional[dict] = None
        self._bloom: Optional[_BloomFilter] = None
        self._bloom_checked = False

    def _ensure_loaded(self) -> None:
        """Load data on first access (lazy loading)."""
        if self._loaded:
            return
        self._loaded = True
        corpus_opt_in = os.environ.get("ZOLAI_LOAD_CORPUS", "") == "1"
        # Index/Bloom cover the default (flag-off) path only — §27 parity.
        if not corpus_opt_in and self._prefer_index and self._load_index():
            self.loaded_via = "index"
            return
        self._load_bible()
        self._load_dict()
        self._load_parallel()
        if corpus_opt_in:
            self._load_corpus()
        self._load_()
        self.loaded_via = "queries"

    # -- Loaders (unchanged queries — these are also what the index stores) --

    def _load_bible(self) -> None:
        from ..data.repositories import get_repositories

        repos = get_repositories(self._db_path)
        for r in repos["bible"].all_records(["zo_tdb77", "zo_tedim2010"]):
            zo = (r.get("zo_tdb77") or r.get("zo_tedim2010") or "")
            words = tokenize_words(zo)
            self.bible_words.update(w for w in words if len(w) >= 2)

    def _load_dict(self) -> None:
        from ..data.repositories import get_repositories

        repos = get_repositories(self._db_path)
        for r in repos["dictionary"].all_records(["zolai"]):
            zolai = (r.get("zolai") or "").lower().strip()
            if zolai and len(zolai) >= 2:
                self.dict_words.add(zolai)

    def _load_parallel(self) -> None:
        from ..data.repositories import get_repositories

        repos = get_repositories(self._db_path)
        for r in repos["translation"].all_records(["target"]):
            zo = r.get("target") or ""
            words = tokenize_words(zo)
            self.corpus_words.update(w for w in words if len(w) >= 2)

    def _load_corpus(self) -> None:
        corpus_dir = DATA_DIR / "online" / "zolai-web-corpus"
        if not corpus_dir.exists():
            return
        for txt_file in corpus_dir.glob("zomi_clean_p*.txt"):
            try:
                with open(txt_file, "r", encoding="utf-8") as f:
                    for line in f:
                        words = line.strip().split()
                        self.corpus_words.update(w.lower() for w in words if len(w) > 1)
            except Exception:
                continue

    def _load_(self) -> None:
        _path = DATA_DIR / "online" / "zolai-extra-dictionary" / "words.json"
        if not _path.exists():
            return
        try:
            with open(_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    words_list = data.get("words", [])
                    if isinstance(words_list, list):
                        for entry in words_list:
                            if isinstance(entry, dict):
                                word = entry.get("word", "").strip().lower()
                                if word:
                                    self._words.add(word)
        except Exception:
            pass

    # -- §27 index-backed load ---------------------------------------------

    def _load_index(self) -> bool:
        """Fill the sets from ``attestation_index``; ``False`` = use queries.

        Staged into temporary sets first: a partially readable index never
        contaminates the fallback path.
        """
        from sqlalchemy.exc import SQLAlchemyError

        from ..data.repositories import get_engine
        from ..data.repositories.observation import AttestationIndexRepository

        staged: dict[str, set[str]] = {attr: set() for _, attr in _INDEX_SOURCES}
        engine = get_engine(self._db_path)
        try:
            try:
                repo = AttestationIndexRepository(engine)
                if repo.count() == 0:
                    return False
                with engine.connect() as conn:
                    for source, attr in _INDEX_SOURCES:
                        staged[attr] = repo.words_for_source(source, conn=conn)
            except SQLAlchemyError:
                # Missing/unreadable index → fall back to the loader queries.
                return False
        finally:
            engine.dispose()
        if not any(staged.values()):
            return False
        for attr, words in staged.items():
            getattr(self, attr).update(words)
        return True

    # -- Membership + verdicts ---------------------------------------------

    def _bloom_filter(self) -> Optional["_BloomFilter"]:
        if not self._bloom_checked:
            self._bloom_checked = True
            self._bloom = _BloomFilter.load(_bloom_json_path())
        return self._bloom

    def _membership_get(self, key: str) -> Optional[tuple[bool, bool, bool, bool]]:
        if key in self._membership_cache:
            value = self._membership_cache.pop(key)
            self._membership_cache[key] = value  # most-recently-used to the end
            return value
        return None

    def _membership_put(self, key: str, value: tuple[bool, bool, bool, bool]) -> None:
        while len(self._membership_cache) >= _CACHE_MAX:
            self._membership_cache.pop(next(iter(self._membership_cache)))
        self._membership_cache[key] = value

    def _membership_uncached(self, key: str) -> tuple[bool, bool, bool, bool]:
        # Bloom: negative-only shortcut (skipped with the file corpus flag on).
        if os.environ.get("ZOLAI_LOAD_CORPUS", "") != "1":
            bloom = self._bloom_filter()
            if bloom is not None and not bloom.maybe(key):
                return (False, False, False, False)
        self._ensure_loaded()
        return (
            key in self.bible_words,
            key in self.dict_words,
            key in self.corpus_words,
            key in self._words,
        )

    @staticmethod
    def _confidence(source_count: int) -> str:
        if source_count >= 2:
            return "VERIFIED"
        if source_count == 1:
            return "ATTESTED"
        return "UNATTESTED"

    def _verdict_to_dict(
        self, word: str, verdict: tuple[bool, bool, bool, bool]
    ) -> dict:
        in_bible, in_dict, in_corpus, in_ = verdict
        sources = sum(verdict)
        return {
            "word": word,
            "confidence": self._confidence(sources),
            "in_bible": in_bible,
            "in_dict": in_dict,
            "in_corpus": in_corpus,
            "in_": in_,
            "source_count": sources,
        }

    def attest_word(self, word: str) -> dict:
        """Check if a word is attested in any source."""
        key = word.lower().strip()
        verdict = self._membership_get(key)
        if verdict is None:
            verdict = self._membership_uncached(key)
            self._membership_put(key, verdict)
        return self._verdict_to_dict(word, verdict)

    def attest_sentence(self, sentence: str) -> dict:
        """Check if all words in a sentence are attested."""
        self._ensure_loaded()
        words = tokenize_words(sentence)

        results: list[dict] = []
        unattested: list[str] = []

        for word in words:
            if len(word) < 2:
                continue
            result = self.attest_word(word)
            results.append(result)
            if result["confidence"] == "UNATTESTED":
                unattested.append(word)

        verified = sum(1 for r in results if r["confidence"] == "VERIFIED")
        attested = sum(1 for r in results if r["confidence"] == "ATTESTED")
        total = len(results)

        if total == 0:
            score = 0.0
        else:
            score = (verified * 1.0 + attested * 0.5) / total

        if score >= 0.8:
            overall = "PASS"
        elif score >= 0.5:
            overall = "PARTIAL"
        else:
            overall = "FAIL"

        return {
            "sentence": sentence,
            "overall": overall,
            "score": round(score, 3),
            "total_words": total,
            "verified": verified,
            "attested": attested,
            "unattested": unattested,
            "word_results": results,
        }

    def get_suggestion(self, word: str) -> Optional[str]:
        """Suggest correct word for unattested word."""
        self._ensure_loaded()
        word_lower = word.lower().strip()
        similar: list[str] = []
        for bw in self.bible_words:
            if len(bw) >= 3 and (word_lower in bw or bw in word_lower):
                similar.append(bw)
            elif self._levenshtein(word_lower, bw) <= 2:
                similar.append(bw)
        if similar:
            return similar[0]
        return None

    @staticmethod
    def _levenshtein(s1: str, s2: str) -> int:
        if len(s1) < len(s2):
            return WordAttestation._levenshtein(s2, s1)
        if len(s2) == 0:
            return len(s1)
        prev_row = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            curr_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = prev_row[j + 1] + 1
                deletions = curr_row[j] + 1
                substitutions = prev_row[j] + (c1 != c2)
                curr_row.append(min(insertions, deletions, substitutions))
            prev_row = curr_row
        return prev_row[-1]

    def get_stats(self) -> dict:
        """Get attestation statistics (5 keys; cached after first load)."""
        self._ensure_loaded()
        if self._stats_cache is None:
            self._stats_cache = {
                "bible_words": len(self.bible_words),
                "dict_words": len(self.dict_words),
                "corpus_words": len(self.corpus_words),
                "_words": len(self._words),
                "total": len(self.bible_words)
                + len(self.dict_words)
                + len(self.corpus_words)
                + len(self._words),
            }
        return dict(self._stats_cache)


# ---------------------------------------------------------------------------
# §27 builders — index materialization + optional Bloom artifact
# ---------------------------------------------------------------------------


def _ensure_index_table(engine) -> None:
    """Idempotently create ``attestation_index`` + its source index if absent."""
    from sqlalchemy import text as sa_text

    from ..data.migrations import ATTESTATION_INDEX_DDL, OBSERVATION_TABLE_INDEXES

    with engine.begin() as conn:
        conn.execute(sa_text(ATTESTATION_INDEX_DDL))
        for index_name, ddl in OBSERVATION_TABLE_INDEXES:
            if index_name == "ix_attestation_source":
                conn.execute(sa_text(ddl))
                break


def build_attestation_index(db_path=None, *, force: bool = True) -> dict:
    """Materialize ``attestation_index`` from the loader queries (§27).

    Uses a ``prefer_index=False`` instance, so the pairs come from exactly the
    queries the in-memory sets use — parity by construction.

    The optional file corpus (``ZOLAI_LOAD_CORPUS=1``) is deliberately built
    *out* of the index: runtime loads with that flag bypass the index, so the
    indexed content must equal the default (flag-off) query path.

    Args:
        db_path: Store path (default: canonical DB).
        force: Rebuild unconditionally.  ``False`` keeps a populated index.

    Returns:
        Summary dict (``rebuilt``, ``pairs_written``, per-source counts).
    """
    started = time.monotonic()
    saved_flag = os.environ.pop("ZOLAI_LOAD_CORPUS", None)
    try:
        attestation = WordAttestation(db_path=db_path, prefer_index=False)
        attestation._ensure_loaded()
        per_source = {
            "bible": sorted(attestation.bible_words),
            "dict": sorted(attestation.dict_words),
            "corpus": sorted(attestation.corpus_words),
            "extra": sorted(attestation._words),
        }
    finally:
        if saved_flag is not None:
            os.environ["ZOLAI_LOAD_CORPUS"] = saved_flag
    pairs = [
        (word, source) for source, words in per_source.items() for word in words
    ]

    from ..data.repositories import get_engine
    from ..data.repositories.observation import AttestationIndexRepository

    engine = get_engine(db_path)
    try:
        _ensure_index_table(engine)
        repo = AttestationIndexRepository(engine)
        existing = int(repo.count())
        if existing and not force:
            return {
                "rebuilt": False,
                "reason": f"index populated ({existing} rows); pass force=True",
                "pairs_written": 0,
                "sources": {},
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
        written = repo.replace_all(pairs)
    finally:
        engine.dispose()
    return {
        "rebuilt": True,
        "pairs_written": written,
        "sources": {name: len(words) for name, words in per_source.items()},
        "total_before": existing,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def _index_union(db_path) -> set[str]:
    """Every indexed word across all sources (``set()`` when absent/empty)."""
    from sqlalchemy.exc import SQLAlchemyError

    from ..data.repositories import get_engine
    from ..data.repositories.observation import AttestationIndexRepository

    engine = get_engine(db_path)
    try:
        try:
            repo = AttestationIndexRepository(engine)
            if repo.count() == 0:
                return set()
            union: set[str] = set()
            with engine.connect() as conn:
                for source, _attr in _INDEX_SOURCES:
                    union |= repo.words_for_source(source, conn=conn)
            return union
        except SQLAlchemyError:
            return set()
    finally:
        engine.dispose()


def build_attestation_bloom(db_path=None, *, out_dir: Path | None = None) -> dict:
    """Write the optional negative-only Bloom artifact (``bloom-v1.{json,bin}``).

    Word universe: the populated index when available (fast), otherwise the
    loader queries.  Like the index builder, the optional file corpus is
    excluded — the artifact must never diverge from the default query path.

    Returns:
        Summary dict (``count``, ``bits``, ``hashes``, ``origin``, paths).
    """
    started = time.monotonic()
    saved_flag = os.environ.pop("ZOLAI_LOAD_CORPUS", None)
    try:
        words = _index_union(db_path)
        origin = "index"
        if not words:
            origin = "queries"
            attestation = WordAttestation(db_path=db_path, prefer_index=False)
            attestation._ensure_loaded()
            words = (
                set(attestation.bible_words)
                | set(attestation.dict_words)
                | set(attestation.corpus_words)
                | set(attestation._words)
            )
    finally:
        if saved_flag is not None:
            os.environ["ZOLAI_LOAD_CORPUS"] = saved_flag

    bloom = _BloomFilter.build(words)
    json_path = _bloom_json_path(out_dir)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    bloom.save(json_path)
    return {
        "count": bloom.count,
        "bits": bloom.bits,
        "hashes": bloom.hashes,
        "origin": origin,
        "json": str(json_path),
        "bin": str(json_path.with_suffix(".bin")),
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


_attestation: Optional[WordAttestation] = None


def get_word_attestation() -> WordAttestation:
    global _attestation  # noqa: PLW0603
    if _attestation is None:
        _attestation = WordAttestation()
    return _attestation
