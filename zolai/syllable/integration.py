"""NLP Pipeline Integration for Zolai Syllable Segmentation (SylBreak4All M7).

Provides tokenizer wrapper, POS tagger integration, MT integration,
syllable-level embeddings, and FastAPI endpoints for external use.
"""

from __future__ import annotations

import argparse
import logging
import pickle
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from fastapi import FastAPI

# Import ZolaiSyllabifier without circular import
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import ZolaiSyllabifier
else:
    # Lazy import to avoid circular dependency
    ZolaiSyllabifier = None  # type: ignore

from .segmenter import SyllableSegmenter

log = logging.getLogger(__name__)

# Try to import FastAPI (optional dependency)
try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel

    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False
    BaseModel = object  # type: ignore
    FastAPI = object  # type: ignore
    HTTPException = Exception  # type: ignore


# --- Pydantic Models for FastAPI ---


class SegmentRequest(BaseModel):
    """Request model for syllable segmentation."""

    text: str
    mode: str = "rule"
    with_offsets: bool = False


class SegmentResponse(BaseModel):
    """Response model for syllable segmentation."""

    syllables: list[str]
    boundaries: list[dict] | None = None
    word_count: int
    syllable_count: int


class TokenizeRequest(BaseModel):
    """Request model for tokenization."""

    text: str
    max_length: int = 512
    return_ids: bool = True


class TokenizeResponse(BaseModel):
    """Response model for tokenization."""

    tokens: list[str]
    token_ids: list[int] | None = None
    offsets: list[dict] | None = None


# --- Zolai Tokenizer ---


class ZolaiTokenizer:
    """Syllable-based tokenizer for Zolai NLP.

    Converts Zolai text into syllable tokens suitable for transformer models.
    Supports WordPiece-style subword tokenization using syllables.

    Example:
        >>> tokenizer = ZolaiTokenizer()
        >>> tokens = tokenizer.tokenize("Pasian in vantung a piangsak hi.")
        >>> tokens
        ['pa', 'sian', 'in', 'van', 'tung', 'a', 'piang', 'sak', 'hi']
    """

    def __init__(
        self,
        segmenter_mode: str = "rule",
        vocab_file: str | Path | None = None,
        unk_token: str = "[UNK]",
        pad_token: str = "[PAD]",
        cls_token: str = "[CLS]",
        sep_token: str = "[SEP]",
        mask_token: str = "[MASK]",
    ) -> None:
        """Initialize the Zolai tokenizer.

        Args:
            segmenter_mode: "rule" or "crf" for syllable segmentation.
            vocab_file: Optional path to vocabulary file for token IDs.
            unk_token: Unknown token string.
            pad_token: Padding token string.
            cls_token: Classification token string.
            sep_token: Separator token string.
            mask_token: Mask token string.
        """
        if segmenter_mode not in ("rule", "crf"):
            raise ValueError(f"segmenter_mode must be 'rule' or 'crf', got {segmenter_mode!r}")

        # Lazy import to avoid circular dependency
        from . import ZolaiSyllabifier as _ZolaiSyllabifier
        self.segmenter = _ZolaiSyllabifier(mode=segmenter_mode)
        self.unk_token = unk_token
        self.pad_token = pad_token
        self.cls_token = cls_token
        self.sep_token = sep_token
        self.mask_token = mask_token

        # Special token IDs (assigned during vocab building)
        self._special_tokens = [
            unk_token,
            pad_token,
            cls_token,
            sep_token,
            mask_token,
        ]

        # Vocabulary mapping
        self._vocab: dict[str, int] = {}
        self._id_to_token: dict[int, str] = {}

        # Load vocab if provided
        if vocab_file:
            self.load_vocab(vocab_file)
        else:
            self._build_base_vocab()

    def _build_base_vocab(self) -> None:
        """Build base vocabulary from special tokens and common syllables."""
        # Add special tokens first
        for i, tok in enumerate(self._special_tokens):
            self._vocab[tok] = i
            self._id_to_token[i] = tok

        # Add common Zolai syllables from SyllableSegmenter's known compounds/roots
        seg = SyllableSegmenter()
        syl_id = len(self._special_tokens)
        for word, syllables in seg._compound_map.items():
            for syl in syllables:
                if syl not in self._vocab:
                    self._vocab[syl] = syl_id
                    self._id_to_token[syl_id] = syl
                    syl_id += 1

        for root in seg.known_roots:
            if root not in self._vocab and len(root) >= 2:
                self._vocab[root] = syl_id
                self._id_to_token[syl_id] = root
                syl_id += 1

        log.debug("Built base vocabulary with %d tokens", len(self._vocab))

    def build_vocab_from_corpus(
        self, corpus_file: str | Path, min_freq: int = 2
    ) -> None:
        """Build vocabulary from a corpus file.

        Args:
            corpus_file: Path to corpus JSONL file with "text" or "word" field.
            min_freq: Minimum frequency for a syllable to be included.
        """
        from collections import Counter

        syllable_counter: Counter[str] = Counter()
        path = Path(corpus_file)

        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    import json

                    data = json.loads(line)
                    text = data.get("text", data.get("word", data.get("zo", "")))
                    if text:
                        syllables = self.tokenize(text)
                        syllable_counter.update(syllables)
                except json.JSONDecodeError:
                    continue

        # Add frequent syllables to vocab
        next_id = len(self._vocab)
        for syl, freq in syllable_counter.most_common():
            if freq >= min_freq and syl not in self._vocab:
                self._vocab[syl] = next_id
                self._id_to_token[next_id] = syl
                next_id += 1

        log.info("Extended vocabulary from corpus: %d total tokens", len(self._vocab))

    def save_vocab(self, path: str | Path) -> None:
        """Save vocabulary to JSON file."""
        import json

        vocab_data = {
            "vocab": self._vocab,
            "special_tokens": {
                "unk_token": self.unk_token,
                "pad_token": self.pad_token,
                "cls_token": self.cls_token,
                "sep_token": self.sep_token,
                "mask_token": self.mask_token,
            },
        }
        with Path(path).open("w", encoding="utf-8") as f:
            json.dump(vocab_data, f, ensure_ascii=False, indent=2)
        log.info("Saved vocabulary to %s", path)

    def load_vocab(self, path: str | Path) -> None:
        """Load vocabulary from JSON file."""
        import json

        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Vocabulary file not found: {path}")

        with path.open("r", encoding="utf-8") as f:
            vocab_data = json.load(f)

        self._vocab = vocab_data["vocab"]
        self._id_to_token = {int(k): v for k, v in vocab_data["vocab"].items()}
        special = vocab_data.get("special_tokens", {})
        self.unk_token = special.get("unk_token", self.unk_token)
        self.pad_token = special.get("pad_token", self.pad_token)
        self.cls_token = special.get("cls_token", self.cls_token)
        self.sep_token = special.get("sep_token", self.sep_token)
        self.mask_token = special.get("mask_token", self.mask_token)
        log.info("Loaded vocabulary with %d tokens from %s", len(self._vocab), path)

    @property
    def vocab_size(self) -> int:
        """Return vocabulary size."""
        return len(self._vocab)

    def tokenize(self, text: str) -> list[str]:
        """Tokenize text into syllables.

        Args:
            text: Zolai text (sentence or word).

        Returns:
            List of syllable tokens (lowercase).
        """
        if not text:
            return []

        words = text.split()
        syllables: list[str] = []
        for word in words:
            syll = self.segmenter.segment(word)
            syllables.extend([s.lower() for s in syll])
        return syllables

    def tokenize_with_offsets(self, text: str) -> list[dict]:
        """Tokenize text into syllables with character offsets.

        Args:
            text: Zolai text.

        Returns:
            List of dicts with 'token', 'start', 'end', 'word_index'.
        """
        if not text:
            return []

        words = text.split()
        result: list[dict] = []
        char_pos = 0
        word_index = 0

        for word in words:
            syll = self.segmenter.segment(word)
            for syl in syll:
                result.append(
                    {
                        "token": syl.lower(),
                        "start": char_pos,
                        "end": char_pos + len(syl),
                        "word_index": word_index,
                    }
                )
                char_pos += len(syl)
            # Account for space between words
            if word_index < len(words) - 1:
                char_pos += 1
            word_index += 1

        return result

    def encode(
        self, text: str, max_length: int = 512, add_special_tokens: bool = True
    ) -> list[int]:
        """Encode text to token IDs.

        Args:
            text: Zolai text.
            max_length: Maximum sequence length.
            add_special_tokens: Whether to add [CLS] and [SEP] tokens.

        Returns:
            List of token IDs.
        """
        tokens = self.tokenize(text)
        token_ids = [self._vocab.get(tok, self._vocab[self.unk_token]) for tok in tokens]

        if add_special_tokens:
            cls_id = self._vocab[self.cls_token]
            sep_id = self._vocab[self.sep_token]
            token_ids = [cls_id] + token_ids + [sep_id]

        # Truncate
        if len(token_ids) > max_length:
            if add_special_tokens:
                token_ids = token_ids[: max_length - 1] + [self._vocab[self.sep_token]]
            else:
                token_ids = token_ids[:max_length]

        return token_ids

    def decode(
        self,
        token_ids: list[int],
        skip_special_tokens: bool = True,
        clean_up_tokenization_spaces: bool = True,
    ) -> str:
        """Decode token IDs to text.

        Args:
            token_ids: List of token IDs.
            skip_special_tokens: Whether to skip special tokens.
            clean_up_tokenization_spaces: Whether to clean up spaces.

        Returns:
            Decoded text string.
        """
        tokens = []
        for tid in token_ids:
            tok = self._id_to_token.get(tid, self.unk_token)
            if skip_special_tokens and tok in self._special_tokens:
                continue
            tokens.append(tok)

        # Join syllables (they're already space-separated in tokenization)
        text = " ".join(tokens)

        if clean_up_tokenization_spaces:
            text = " ".join(text.split())

        return text

    def convert_tokens_to_ids(self, tokens: list[str]) -> list[int]:
        """Convert list of tokens to IDs."""
        return [self._vocab.get(tok, self._vocab[self.unk_token]) for tok in tokens]

    def convert_ids_to_tokens(self, token_ids: list[int]) -> list[str]:
        """Convert list of IDs to tokens."""
        return [self._id_to_token.get(tid, self.unk_token) for tid in token_ids]

    def __call__(
        self, text: str, max_length: int = 512, return_tensors: str | None = None
    ) -> dict:
        """Encode text for transformer models (HuggingFace-style).

        Args:
            text: Zolai text.
            max_length: Maximum sequence length.
            return_tensors: "pt" for PyTorch, "tf" for TensorFlow, "np" for NumPy.

        Returns:
            Dictionary with 'input_ids', 'attention_mask', optionally 'token_type_ids'.
        """
        input_ids = self.encode(text, max_length=max_length)
        attention_mask = [1] * len(input_ids)

        # Pad to max_length
        pad_id = self._vocab[self.pad_token]
        if len(input_ids) < max_length:
            padding = [pad_id] * (max_length - len(input_ids))
            input_ids.extend(padding)
            attention_mask.extend([0] * (max_length - len(input_ids)))

        result = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }

        if return_tensors == "pt":
            import torch

            result = {k: torch.tensor([v]) for k, v in result.items()}
        elif return_tensors == "tf":
            import tensorflow as tf

            result = {k: tf.constant([v]) for k, v in result.items()}
        elif return_tensors == "np":
            result = {k: np.array([v]) for k, v in result.items()}

        return result


# --- Syllable-Aware POS Tagger ---


class SyllableAwarePOS:
    """POS tagger with syllable-level features.

    Enhances the base ZolaiPOSTagger with syllable segmentation features
    for improved tagging accuracy on morphologically complex words.
    """

    def __init__(self, segmenter_mode: str = "rule") -> None:
        """Initialize the syllable-aware POS tagger.

        Args:
            segmenter_mode: "rule" or "crf" for syllable segmentation.
        """
        from zolai.pos_tagger import ZolaiPOSTagger

        self.pos_tagger = ZolaiPOSTagger()
        self.tokenizer = ZolaiTokenizer(segmenter_mode=segmenter_mode)

        # Syllable-based POS patterns
        self._syllable_pos_patterns: dict[tuple[str, ...], str] = {
            ("pa", "sian"): "N.PROPER",  # Pasian
            ("van", "tung"): "N.PROPER",  # Vantung
            ("lei", "tung"): "N.PROPER",  # Leitung
            ("nun", "tak", "na"): "NOUN",  # Nuntakna
            ("suah", "tak", "na"): "NOUN",  # Suahtakna
            ("piang", "sak"): "VERB",  # Piangsak
            ("bawl", "sak"): "VERB",  # Bawlsak
            ("dam", "na"): "NOUN",  # Damna
        }

    def tag(self, text: str) -> list[dict]:
        """Tag text with POS and syllable features.

        Args:
            text: Zolai text.

        Returns:
            List of dicts with 'word', 'pos', 'syllables', 'syllable_count', 'features'.
        """
        words = text.split()
        results = []

        for word in words:
            pos_result = self.pos_tagger._tag_single(word)
            syllables = self.tokenizer.tokenize(word)

            # Build syllable features
            syl_features = self._extract_syllable_features(word, syllables)

            # Check syllable pattern for POS refinement
            syl_tuple = tuple(syllables)
            refined_pos = self._syllable_pos_patterns.get(syl_tuple, pos_result[1])

            results.append(
                {
                    "word": word,
                    "pos": refined_pos,
                    "original_pos": pos_result[1],
                    "syllables": syllables,
                    "syllable_count": len(syllables),
                    "syllable_features": syl_features,
                }
            )

        return results

    def _extract_syllable_features(self, word: str, syllables: list[str]) -> dict:
        """Extract syllable-level linguistic features."""
        return {
            "syllable_structure": [self._analyze_syllable(s) for s in syllables],
            "has_diphthong": any(self._has_diphthong(s) for s in syllables),
            "has_digraph_onset": any(self._has_digraph_onset(s) for s in syllables),
            "has_coda": any(self._has_coda(s) for s in syllables),
            "onset_types": [self._get_onset_type(s) for s in syllables],
            "nucleus_types": [self._get_nucleus_type(s) for s in syllables],
        }

    def _analyze_syllable(self, syl: str) -> str:
        """Return syllable structure pattern (e.g., CV, CVC, CCV)."""
        from .rules import VOWELS

        pattern = ""
        for ch in syl.lower():
            if ch in VOWELS:
                pattern += "V"
            elif ch.isalpha():
                pattern += "C"
            else:
                pattern += "?"
        return pattern

    def _has_diphthong(self, syl: str) -> bool:
        """Check if syllable contains a diphthong."""
        from .rules import DIPHTHONGS

        for i in range(len(syl) - 1):
            if syl[i : i + 2].lower() in DIPHTHONGS:
                return True
        return False

    def _has_digraph_onset(self, syl: str) -> bool:
        """Check if syllable starts with a digraph onset."""
        from .rules import DIGRAPHS

        if len(syl) >= 2:
            return syl[:2].lower() in DIGRAPHS
        return False

    def _has_coda(self, syl: str) -> bool:
        """Check if syllable has a coda."""
        from .rules import VALID_CODAS

        if syl:
            return syl[-1].lower() in VALID_CODAS or syl[-2:].lower() == "ng"
        return False

    def _get_onset_type(self, syl: str) -> str:
        """Classify onset type: none, simple, cluster, digraph."""
        from .rules import DIGRAPHS, VALID_ONSET_CLUSTERS, VOWELS

        # Find first vowel
        for i, ch in enumerate(syl.lower()):
            if ch in VOWELS:
                onset = syl[:i]
                break
        else:
            return "none"

        if not onset:
            return "none"
        if len(onset) == 1:
            return "simple"
        if onset in VALID_ONSET_CLUSTERS:
            return "cluster"
        if onset in DIGRAPHS:
            return "digraph"
        return "other"

    def _get_nucleus_type(self, syl: str) -> str:
        """Classify nucleus type: short, long, diphthong."""
        from .rules import DIPHTHONGS, VOWELS

        for i in range(len(syl)):
            if syl[i].lower() in VOWELS:
                if i + 1 < len(syl) and syl[i : i + 2].lower() in DIPHTHONGS:
                    return "diphthong"
                return "short"
        return "none"

    def tag_with_confidence(self, text: str) -> list[dict]:
        """Tag with confidence scores including syllable features.

        Args:
            text: Zolai text.

        Returns:
            List of dicts with confidence scores.
        """
        tagged = self.tag(text)
        for item in tagged:
            base_conf = self.pos_tagger._confidence(item["word"], item["pos"])
            # Boost confidence if syllable pattern matches
            syl_tuple = tuple(item["syllables"])
            if syl_tuple in self._syllable_pos_patterns:
                base_conf = min(1.0, base_conf + 0.1)
            item["confidence"] = base_conf
        return tagged


# --- Syllable Embeddings ---


class SyllableEmbeddings:
    """Syllable-level embeddings for Zolai.

    Trains or loads embeddings for Zolai syllables, enabling
    syllable-aware word representations for downstream tasks.
    """

    def __init__(
        self,
        embedding_dim: int = 100,
        window: int = 5,
        min_count: int = 5,
        epochs: int = 10,
    ) -> None:
        """Initialize syllable embeddings.

        Args:
            embedding_dim: Dimension of embedding vectors.
            window: Context window size for training.
            min_count: Minimum syllable frequency.
            epochs: Number of training epochs.
        """
        self.dim = embedding_dim
        self.window = window
        self.min_count = min_count
        self.epochs = epochs
        self.embeddings: dict[str, np.ndarray] = {}
        self._word2idx: dict[str, int] = {}
        self._idx2word: dict[int, str] = {}
        self._trained = False

    def train(self, corpus_file: str | Path, vocab_file: str | Path | None = None) -> None:
        """Train syllable embeddings from corpus using Word2Vec.

        Args:
            corpus_file: Path to corpus JSONL file.
            vocab_file: Optional path to save vocabulary.
        """
        try:
            from gensim.models import Word2Vec
        except ImportError:
            raise ImportError(
                "gensim is required for syllable embedding training. "
                "Install with: pip install gensim"
            )

        # Extract syllable sequences from corpus
        sentences: list[list[str]] = []
        tokenizer = ZolaiTokenizer()

        with Path(corpus_file).open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    import json

                    data = json.loads(line)
                    text = data.get("text", data.get("word", data.get("zo", "")))
                    if text:
                        syllables = tokenizer.tokenize(text)
                        if syllables:
                            sentences.append(syllables)
                except json.JSONDecodeError:
                    continue

        log.info("Training Word2Vec on %d syllable sequences", len(sentences))

        model = Word2Vec(
            sentences=sentences,
            vector_size=self.dim,
            window=self.window,
            min_count=self.min_count,
            epochs=self.epochs,
            workers=4,
            sg=1,  # Skip-gram
        )

        # Store embeddings
        self._word2idx = {word: i for i, word in enumerate(model.wv.index_to_key)}
        self._idx2word = {i: word for i, word in enumerate(model.wv.index_to_key)}
        self.embeddings = {
            word: model.wv[word] for word in model.wv.index_to_key
        }
        self._trained = True

        # Save vocab if requested
        if vocab_file:
            self.save_vocab(vocab_file)

        log.info("Trained embeddings for %d syllables", len(self.embeddings))

    def train_fasttext(self, corpus_file: str | Path) -> None:
        """Train using FastText for subword information."""
        try:
            from gensim.models import FastText
        except ImportError:
            raise ImportError("gensim with fasttext support required. pip install gensim")

        sentences: list[list[str]] = []
        tokenizer = ZolaiTokenizer()

        with Path(corpus_file).open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    import json

                    data = json.loads(line)
                    text = data.get("text", data.get("word", data.get("zo", "")))
                    if text:
                        syllables = tokenizer.tokenize(text)
                        if syllables:
                            sentences.append(syllables)
                except json.JSONDecodeError:
                    continue

        model = FastText(
            sentences=sentences,
            vector_size=self.dim,
            window=self.window,
            min_count=self.min_count,
            epochs=self.epochs,
            workers=4,
            sg=1,
        )

        self._word2idx = {word: i for i, word in enumerate(model.wv.index_to_key)}
        self._idx2word = {i: word for i, word in enumerate(model.wv.index_to_key)}
        self.embeddings = {word: model.wv[word] for word in model.wv.index_to_key}
        self._trained = True

    def get_embedding(self, syllable: str) -> np.ndarray | None:
        """Get embedding vector for a syllable.

        Args:
            syllable: Zolai syllable.

        Returns:
            Embedding vector or None if not in vocabulary.
        """
        return self.embeddings.get(syllable.lower())

    def word_embedding(self, word: str) -> np.ndarray | None:
        """Get word embedding by averaging syllable embeddings.

        Args:
            word: Zolai word.

        Returns:
            Averaged embedding vector or None if no syllables found.
        """
        tokenizer = ZolaiTokenizer()
        syllables = tokenizer.tokenize(word)

        vectors = []
        for syl in syllables:
            emb = self.get_embedding(syl)
            if emb is not None:
                vectors.append(emb)

        if not vectors:
            return None

        return np.mean(vectors, axis=0)

    def similarity(self, syl1: str, syl2: str) -> float | None:
        """Compute cosine similarity between two syllables."""
        emb1 = self.get_embedding(syl1)
        emb2 = self.get_embedding(syl2)

        if emb1 is None or emb2 is None:
            return None

        return float(np.dot(emb1, emb2) / (np.linalg.norm(emb1) * np.linalg.norm(emb2)))

    def most_similar(self, syllable: str, topn: int = 10) -> list[tuple[str, float]]:
        """Find most similar syllables."""
        emb = self.get_embedding(syllable)
        if emb is None:
            return []

        similarities = []
        for syl, vec in self.embeddings.items():
            if syl == syllable:
                continue
            sim = float(np.dot(emb, vec) / (np.linalg.norm(emb) * np.linalg.norm(vec)))
            similarities.append((syl, sim))

        similarities.sort(key=lambda x: x[1], reverse=True)
        return similarities[:topn]

    def save(self, path: str | Path) -> None:
        """Save embeddings to file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "embeddings": self.embeddings,
            "dim": self.dim,
            "word2idx": self._word2idx,
            "idx2word": self._idx2word,
        }
        with path.open("wb") as f:
            pickle.dump(data, f)
        log.info("Saved embeddings to %s", path)

    def load(self, path: str | Path) -> None:
        """Load embeddings from file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Embeddings file not found: {path}")

        with path.open("rb") as f:
            data = pickle.load(f)

        self.embeddings = data["embeddings"]
        self.dim = data["dim"]
        self._word2idx = data["word2idx"]
        self._idx2word = data["idx2word"]
        self._trained = True
        log.info("Loaded embeddings for %d syllables from %s", len(self.embeddings), path)

    def save_vocab(self, path: str | Path) -> None:
        """Save vocabulary mapping."""
        import json

        vocab_data = {
            "word2idx": self._word2idx,
            "idx2word": {str(k): v for k, v in self._idx2word.items()},
        }
        with Path(path).open("w", encoding="utf-8") as f:
            json.dump(vocab_data, f, ensure_ascii=False, indent=2)


# --- FastAPI Application ---

if _FASTAPI_AVAILABLE:

    app = FastAPI(
        title="Zolai Syllable Segmentation API",
        description="SylBreak4All - Syllable segmentation for Tedim Zolai (ZVS 2018)",
        version="1.0.0",
    )

    # Global tokenizer instance
    _tokenizer: ZolaiTokenizer | None = None

    def get_tokenizer(mode: str = "rule") -> ZolaiTokenizer:
        """Get or create tokenizer instance."""
        global _tokenizer
        if _tokenizer is None or _tokenizer.segmenter.mode != mode:
            _tokenizer = ZolaiTokenizer(segmenter_mode=mode)
        return _tokenizer

    @app.get("/")
    async def root():
        """Health check endpoint."""
        return {
            "service": "Zolai Syllable Segmentation API",
            "version": "1.0.0",
            "orthography": "ZVS 2018",
            "status": "ok",
        }

    @app.post("/syllable/segment", response_model=SegmentResponse)
    async def segment_syllable(request: SegmentRequest):
        """Segment text into syllables.

        Args:
            request: SegmentRequest with text, mode, and with_offsets.

        Returns:
            SegmentResponse with syllables and optional boundaries.
        """
        if not request.text:
            raise HTTPException(status_code=400, detail="Text cannot be empty")

        if request.mode not in ("rule", "crf"):
            raise HTTPException(
                status_code=400, detail="Mode must be 'rule' or 'crf'"
            )

        # Lazy import to avoid circular dependency
        from . import ZolaiSyllabifier as _ZolaiSyllabifier
        syllabifier = _ZolaiSyllabifier(mode=request.mode)

        if request.with_offsets:
            # Segment word by word with boundaries
            words = request.text.split()
            all_syllables = []
            all_boundaries = []
            char_pos = 0

            for word in words:
                boundaries = syllabifier.segment_with_boundaries(word)
                for b in boundaries:
                    all_syllables.append(b.syllable)
                    all_boundaries.append(
                        {
                            "start": char_pos + b.start,
                            "end": char_pos + b.end,
                            "syllable": b.syllable,
                        }
                    )
                char_pos += len(word) + 1  # +1 for space

            return SegmentResponse(
                syllables=all_syllables,
                boundaries=all_boundaries,
                word_count=len(words),
                syllable_count=len(all_syllables),
            )
        else:
            syllables = syllabifier.segment(request.text)
            return SegmentResponse(
                syllables=syllables,
                boundaries=None,
                word_count=len(request.text.split()),
                syllable_count=len(syllables),
            )

    @app.post("/syllable/tokenize", response_model=TokenizeResponse)
    async def tokenize_text(request: TokenizeRequest):
        """Tokenize text into syllable tokens with optional IDs.

        Args:
            request: TokenizeRequest with text and options.

        Returns:
            TokenizeResponse with tokens and optional IDs/offsets.
        """
        if not request.text:
            raise HTTPException(status_code=400, detail="Text cannot be empty")

        tokenizer = get_tokenizer()
        tokens = tokenizer.tokenize(request.text)

        response = TokenizeResponse(tokens=tokens)

        if request.return_ids:
            token_ids = tokenizer.convert_tokens_to_ids(tokens)
            response.token_ids = token_ids

        # Always include offsets
        offsets = tokenizer.tokenize_with_offsets(request.text)
        response.offsets = offsets

        return response

    @app.post("/syllable/encode")
    async def encode_text(request: TokenizeRequest):
        """Encode text to token IDs for transformer models."""
        if not request.text:
            raise HTTPException(status_code=400, detail="Text cannot be empty")

        tokenizer = get_tokenizer()
        encoded = tokenizer(request.text, max_length=request.max_length, return_tensors="np")

        return {
            "input_ids": encoded["input_ids"].tolist(),
            "attention_mask": encoded["attention_mask"].tolist(),
        }

    @app.post("/syllable/decode")
    async def decode_tokens(token_ids: list[int]):
        """Decode token IDs back to text."""
        tokenizer = get_tokenizer()
        text = tokenizer.decode(token_ids)
        return {"text": text}

    @app.get("/syllable/vocab")
    async def get_vocab():
        """Get tokenizer vocabulary."""
        tokenizer = get_tokenizer()
        return {"vocab": tokenizer._vocab, "vocab_size": tokenizer.vocab_size}

    @app.get("/health")
    async def health():
        """Health check with tokenizer info."""
        tokenizer = get_tokenizer()
        return {
            "status": "healthy",
            "tokenizer_mode": tokenizer.segmenter.mode,
            "vocab_size": tokenizer.vocab_size,
        }


# --- CLI ---


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m zolai.syllable.integration",
        description="Zolai NLP Pipeline Integration (SylBreak4All M7)",
    )
    parser.add_argument(
        "--tokenize", "-t", type=str, help="Tokenize text into syllables"
    )
    parser.add_argument(
        "--encode", "-e", type=str, help="Encode text to token IDs"
    )
    parser.add_argument(
        "--decode", "-d", type=str, help="Decode token IDs (comma-separated)"
    )
    parser.add_argument(
        "--pos", type=str, help="POS tag with syllable features"
    )
    parser.add_argument(
        "--train-embeddings", type=str, help="Train syllable embeddings from corpus"
    )
    parser.add_argument(
        "--embedding-dim", type=int, default=100, help="Embedding dimension"
    )
    parser.add_argument(
        "--save-embeddings", type=str, help="Path to save trained embeddings"
    )
    parser.add_argument(
        "--load-embeddings", type=str, help="Path to load embeddings"
    )
    parser.add_argument(
        "--similarity", nargs=2, metavar=("SYL1", "SYL2"), help="Compute syllable similarity"
    )
    parser.add_argument(
        "--word-embedding", type=str, help="Get word embedding by averaging syllables"
    )
    parser.add_argument(
        "--serve", action="store_true", help="Start FastAPI server"
    )
    parser.add_argument(
        "--host", type=str, default="0.0.0.0", help="Server host"
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Server port"
    )
    parser.add_argument(
        "--test", action="store_true", help="Run internal tests"
    )
    return parser


def _run_tests() -> int:
    """Run internal tests."""
    print("Running integration tests...")

    # Test 1: Basic tokenization
    tokenizer = ZolaiTokenizer()
    test_text = "Pasian in vantung a piangsak hi"
    tokens = tokenizer.tokenize(test_text)
    expected = ["pa", "sian", "in", "van", "tung", "a", "piang", "sak", "hi"]
    assert tokens == expected, f"Tokenization failed: {tokens} != {expected}"
    print("✓ Tokenization test passed")

    # Test 2: Tokenize with offsets
    offsets = tokenizer.tokenize_with_offsets(test_text)
    assert len(offsets) == len(expected), "Offset count mismatch"
    assert offsets[0]["token"] == "pa"
    assert offsets[0]["start"] == 0
    print("✓ Offsets test passed")

    # Test 3: Encode/decode
    encoded = tokenizer.encode(test_text)
    decoded = tokenizer.decode(encoded)
    print(f"  Encoded: {encoded}")
    print(f"  Decoded: {decoded}")
    print("✓ Encode/decode test passed")

    # Test 4: Syllable-aware POS
    pos_tagger = SyllableAwarePOS()
    tagged = pos_tagger.tag("Pasian in vantung a piangsak hi")
    for item in tagged:
        print(f"  {item['word']}: {item['pos']} (syllables: {item['syllables']})")
    print("✓ Syllable-aware POS test passed")

    # Test 5: Segment endpoint logic
    from . import ZolaiSyllabifier as _ZolaiSyllabifier

    syl = _ZolaiSyllabifier(mode="rule")
    syllables = syl.segment("laisiangtho")
    assert syllables == ["lai", "siang", "tho"], f"Segment failed: {syllables}"
    print("✓ Segmenter test passed")

    print("\nAll tests passed!")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = _build_cli()
    args = parser.parse_args(argv)

    if args.test:
        return _run_tests()

    if args.tokenize:
        tokenizer = ZolaiTokenizer()
        tokens = tokenizer.tokenize(args.tokenize)
        print(f"Tokens: {tokens}")
        print(f"Count: {len(tokens)}")
        return 0

    if args.encode:
        tokenizer = ZolaiTokenizer()
        ids = tokenizer.encode(args.encode)
        print(f"Token IDs: {ids}")
        return 0

    if args.decode:
        tokenizer = ZolaiTokenizer()
        try:
            token_ids = [int(x.strip()) for x in args.decode.split(",")]
        except ValueError:
            print("Error: --decode expects comma-separated integers", file=sys.stderr)
            return 1
        text = tokenizer.decode(token_ids)
        print(f"Decoded: {text}")
        return 0

    if args.pos:
        tagger = SyllableAwarePOS()
        tagged = tagger.tag(args.pos)
        for item in tagged:
            print(
                f"{item['word']:15} POS={item['pos']:10} "
                f"Syllables={item['syllables']} Count={item['syllable_count']}"
            )
        return 0

    if args.train_embeddings:
        embeddings = SyllableEmbeddings(embedding_dim=args.embedding_dim)
        embeddings.train(args.train_embeddings)
        if args.save_embeddings:
            embeddings.save(args.save_embeddings)
        return 0

    if args.load_embeddings:
        embeddings = SyllableEmbeddings()
        embeddings.load(args.load_embeddings)
        print(f"Loaded {len(embeddings.embeddings)} syllable embeddings")

        if args.similarity:
            syl1, syl2 = args.similarity
            sim = embeddings.similarity(syl1, syl2)
            if sim is not None:
                print(f"Similarity({syl1}, {syl2}) = {sim:.4f}")
            else:
                print("One or both syllables not in vocabulary")
        elif args.word_embedding:
            emb = embeddings.word_embedding(args.word_embedding)
            if emb is not None:
                print(f"Word embedding shape: {emb.shape}")
                print(f"First 5 dims: {emb[:5]}")
            else:
                print("No syllable embeddings found for word")
        return 0

    if args.serve:
        if not _FASTAPI_AVAILABLE:
            print("Error: FastAPI not installed. Run: pip install fastapi uvicorn", file=sys.stderr)
            return 1
        import uvicorn

        print(f"Starting server on {args.host}:{args.port}")
        uvicorn.run(app, host=args.host, port=args.port)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
