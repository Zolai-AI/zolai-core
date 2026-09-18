"""Word embeddings trainer and utility for Tedim Zolai.

Trains fastText-style embeddings on Zolai monolingual corpus, Bible,
and articles. Provides analogy solving, similarity search, and
vector export for DB storage.

Usage:
    from zolai.embeddings import ZolaiWordEmbeddings
    emb = ZolaiWordEmbeddings()
    emb.train("data/online/zolai-web-corpus/zomi_clean_p1.txt", "data/embeddings/")
    emb.load("data/embeddings/zolai_sg_300.bin")
    vec = emb.get_vector("pasian")
    similar = emb.get_similar("pasian", k=5)
"""
from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"


class ZolaiWordEmbeddings:
    """Word embeddings trainer and utility for Zolai.

    Provides:
    - train(): Train fastText-style embeddings from corpus
    - load(): Load pre-trained embeddings
    - get_vector(): Get word vector
    - get_similar(): Find most similar words
    - analogy(): Solve word analogies
    - save(): Export embeddings to JSONL

    Lazy-loads data on first use. Uses skip-gram/CBOW with
    negative sampling for efficiency.
    """

    def __init__(self) -> None:
        self._vectors: dict[str, list[float]] = {}
        self._word_counts: Counter[str] = Counter()
        self._dim: int = 300
        self._vocab_size: int = 0
        self._loaded = False

    def train(
        self,
        corpus_path: str,
        output_dir: str,
        dim: int = 300,
        model: str = "skipgram",
        min_count: int = 5,
        window: int = 5,
        epoch: int = 10,
        lr: float = 0.05,
        minn: int = 2,
        maxn: int = 5,
    ) -> dict[str, int]:
        """Train word embeddings on a monolingual corpus.

        Args:
            corpus_path: Path to text corpus (one sentence per line).
            output_dir: Directory to save trained model.
            dim: Embedding dimension (default 300).
            model: 'skipgram' or 'cbow'.
            min_count: Minimum word frequency.
            context window size.
            epoch: Training epochs.
            lr: Learning rate.
            minn: Minimum n-gram length (for subword info).
            maxn: Maximum n-gram length.

        Returns:
            Stats dict: vocab_size, total_words, output_path.
        """
        corpus = Path(corpus_path)
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # Step 1: Build vocabulary
        log.info("Building vocabulary from %s ...", corpus_path)
        self._word_counts = Counter()
        total_words = 0

        try:
            with open(corpus, "r", encoding="utf-8") as f:
                for line in f:
                    words = self._tokenize(line)
                    self._word_counts.update(words)
                    total_words += len(words)
        except FileNotFoundError:
            log.error("Corpus not found: %s", corpus_path)
            return {"error": 1}

        # Filter by min_count
        vocab = {
            w: c
            for w, c in self._word_counts.items()
            if c >= min_count
        }
        self._vocab_size = len(vocab)
        word2idx = {w: i for i, w in enumerate(sorted(vocab.keys()))}

        log.info(
            "Vocab: %d words (min_count=%d), total: %d tokens",
            self._vocab_size,
            min_count,
            total_words,
        )

        # Step 2: Initialize vectors randomly (seed for reproducibility)
        import random

        rng = random.Random(42)
        vectors = [[rng.gauss(0, 0.1) for _ in range(dim)] for _ in range(self._vocab_size)]

        # Step 3: Skip-gram training with negative sampling
        log.info("Training %s (dim=%d, epoch=%d, lr=%.3f) ...", model, dim, epoch, lr)
        corpus_tokens: list[list[int]] = []

        try:
            with open(corpus, "r", encoding="utf-8") as f:
                for line in f:
                    words = self._tokenize(line)
                    indices = [word2idx[w] for w in words if w in word2idx]
                    if indices:
                        corpus_tokens.append(indices)
        except FileNotFoundError:
            return {"error": 1}

        for ep in range(epoch):
            log.info("Epoch %d/%d ...", ep + 1, epoch)
            for sentence in corpus_tokens:
                for i, center_idx in enumerate(sentence):
                    # Context window
                    start = max(0, i - window)
                    end = min(len(sentence), i + window + 1)
                    for j in range(start, end):
                        if j == i:
                            continue
                        context_idx = sentence[j]
                        self._update_vector(
                            vectors, center_idx, context_idx,
                            dim, lr, len(vocab),
                        )

        # Step 4: Build vectors dict
        idx2word = {i: w for w, i in word2idx.items()}
        self._vectors = {
            idx2word[i]: [round(v, 6) for v in vectors[i]]
            for i in range(self._vocab_size)
        }
        self._dim = dim

        # Step 5: Save
        model_path = out / f"zolai_{model}_{dim}.json"
        with open(model_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "dim": dim,
                    "vocab_size": self._vocab_size,
                    "model": model,
                    "vectors": self._vectors,
                },
                f,
                ensure_ascii=False,
            )

        log.info("Saved embeddings to %s", model_path)
        return {
            "vocab_size": self._vocab_size,
            "total_words": total_words,
            "dim": dim,
            "model": model,
            "output_path": str(model_path),
        }

    def load(self, model_path: str) -> None:
        """Load pre-trained embeddings from JSON file.

        Args:
            model_path: Path to JSON embeddings file.
        """
        path = Path(model_path)
        if not path.exists():
            log.error("Model not found: %s", model_path)
            return

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self._vectors = data.get("vectors", {})
        self._dim = data.get("dim", 300)
        self._vocab_size = len(self._vectors)
        self._loaded = True

        log.info(
            "Loaded %d embeddings (dim=%d) from %s",
            self._vocab_size,
            self._dim,
            model_path,
        )

    def load_from_db(self) -> None:
        """Load embeddings from the SQLite DB word_alignments or vocab table."""
        try:
            from ..data.database import get_manager

            mgr = get_manager()
            vocab_table = mgr.metadata.tables.get("vocab")
            if vocab_table is None:
                return

            with mgr.engine.connect() as conn:
                rows = conn.execute(
                    vocab_table.select().where(
                        vocab_table.c.headword.isnot(None)
                    ).limit(50000)
                ).fetchall()

            for row in rows:
                word = getattr(row, "headword", "")
                freq = getattr(row, "frequency", 0)
                if word:
                    self._word_counts[word.lower()] = freq or 0

            self._vocab_size = len(self._word_counts)
            self._loaded = True
            log.info("Loaded %d vocab entries from DB", self._vocab_size)
        except Exception as exc:
            log.warning("Could not load from DB: %s", exc)

    def get_vector(self, word: str) -> list[float]:
        """Get the embedding vector for a word.

        Args:
            word: Zolai word.

        Returns:
            List of floats (dim-dimensional vector).
            Returns zero vector if word not found.
        """
        lower = word.lower()
        if lower in self._vectors:
            return self._vectors[lower]
        return [0.0] * self._dim

    def get_similar(self, word: str, k: int = 10) -> list[tuple[str, float]]:
        """Get the k most similar words by cosine similarity.

        Args:
            word: Query word.
            k: Number of similar words to return.

        Returns:
            List of (word, similarity_score) tuples, sorted descending.
        """
        lower = word.lower()
        if lower not in self._vectors:
            return []

        query_vec = self._vectors[lower]
        scores: list[tuple[str, float]] = []

        for other_word, other_vec in self._vectors.items():
            if other_word == lower:
                continue
            sim = self._cosine_similarity(query_vec, other_vec)
            scores.append((other_word, sim))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:k]

    def analogy(self, a: str, b: str, c: str, k: int = 5) -> list[tuple[str, float]]:
        """Solve a:b :: c:? word analogy.

        Finds words d such that: vec(b) - vec(a) + vec(c) ≈ vec(d).

        Args:
            a: First word (e.g. "king")
            b: Second word (e.g. "queen")
            c: Third word (e.g. "man")
            k: Number of results to return.

        Returns:
            List of (word, score) tuples for the top-k closest.
        """
        va = self._vectors.get(a.lower(), None)
        vb = self._vectors.get(b.lower(), None)
        vc = self._vectors.get(c.lower(), None)

        if va is None or vb is None or vc is None:
            return []

        # target = vb - va + vc
        target = [vb[i] - va[i] + vc[i] for i in range(self._dim)]

        scores: list[tuple[str, float]] = []
        exclude = {a.lower(), b.lower(), c.lower()}

        for word, vec in self._vectors.items():
            if word in exclude:
                continue
            sim = self._cosine_similarity(target, vec)
            scores.append((word, sim))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:k]

    def save(self, path: str) -> None:
        """Save embeddings to JSONL format for DB storage.

        Each line: {"word": "...", "vector": [...], "dim": N}

        Args:
            path: Output JSONL file path.
        """
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)

        with open(out, "w", encoding="utf-8") as f:
            for word, vec in self._vectors.items():
                f.write(
                    json.dumps(
                        {"word": word, "vector": vec, "dim": self._dim},
                        ensure_ascii=False,
                    )
                    + "\n"
                )

        log.info("Saved %d embeddings to %s", len(self._vectors), path)

    def save_bin(self, path: str) -> None:
        """Save embeddings in fastText-compatible binary format (header + vectors).

        Not full fastText format, but loadable for inference.
        """
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)

        with open(out, "w", encoding="utf-8") as f:
            f.write(f"{self._vocab_size} {self._dim}\n")
            for word, vec in self._vectors.items():
                vec_str = " ".join(f"{v:.6f}" for v in vec)
                f.write(f"{word} {vec_str}\n")

        log.info("Saved %d embeddings to %s (bin)", len(self._vectors), path)

    def load_bin(self, path: str) -> None:
        """Load embeddings from fastText-compatible binary format."""
        self._vectors = {}
        with open(path, "r", encoding="utf-8") as f:
            header = f.readline().strip()
            parts = header.split()
            self._vocab_size = int(parts[0])
            self._dim = int(parts[1])

            for line in f:
                tokens = line.strip().split(" ", 1)
                if len(tokens) == 2:
                    word = tokens[0]
                    vec = [float(x) for x in tokens[1].split()]
                    self._vectors[word] = vec

        self._loaded = True
        log.info("Loaded %d embeddings from %s", len(self._vectors), path)

    def get_vocab(self) -> list[str]:
        """Return list of all words with embeddings."""
        return sorted(self._vectors.keys())

    def get_stats(self) -> dict[str, int]:
        """Return embedding statistics."""
        return {
            "vocab_size": len(self._vectors),
            "dim": self._dim,
            "word_counts": len(self._word_counts),
            "loaded": self._loaded,
        }

    # ── Internal helpers ──────────────────────────────────────────────────
    def _tokenize(self, text: str) -> list[str]:
        """Tokenize Zolai text into words."""
        return [
            w.lower()
            for w in re.findall(r"[a-zA-Z\u0100-\u024F'-]+", text)
            if len(w) >= 2
        ]

    def _update_vector(
        self,
        vectors: list[list[float]],
        center: int,
        context: int,
        dim: int,
        lr: float,
        vocab_size: int,
    ) -> None:
        """Update vectors for skip-gram with negative sampling."""
        import random

        rng = random.Random(center * vocab_size + context)

        # Positive sample
        center_vec = vectors[center]
        context_vec = vectors[context]

        # Sigmoid gradient
        dot = sum(center_vec[i] * context_vec[i] for i in range(dim))
        sig = self._sigmoid(dot)
        grad = lr * (1.0 - sig)

        # Update
        for i in range(dim):
            center_vec[i] += grad * context_vec[i]
            context_vec[i] += grad * center_vec[i]

        # Negative samples (5)
        for _ in range(5):
            neg = rng.randint(0, vocab_size - 1)
            if neg == context:
                continue
            neg_vec = vectors[neg]
            dot_neg = sum(center_vec[i] * neg_vec[i] for i in range(dim))
            sig_neg = self._sigmoid(dot_neg)
            grad_neg = lr * (-sig_neg)

            for i in range(dim):
                center_vec[i] += grad_neg * neg_vec[i]
                neg_vec[i] += grad_neg * center_vec[i]

    @staticmethod
    def _sigmoid(x: float) -> float:
        """Numerically stable sigmoid."""
        if x >= 0:
            return 1.0 / (1.0 + math.exp(-x))
        exp_x = math.exp(x)
        return exp_x / (1.0 + exp_x)

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


# ── Module-level singleton ────────────────────────────────────────────────────
_embeddings: Optional[ZolaiWordEmbeddings] = None


def get_embeddings() -> ZolaiWordEmbeddings:
    """Get or create the singleton embeddings instance."""
    global _embeddings  # noqa: PLW0603
    if _embeddings is None:
        _embeddings = ZolaiWordEmbeddings()
    return _embeddings
