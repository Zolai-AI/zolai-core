"""Syllable segmenter protocol and implementations.

Provides the SyllableSegmenter protocol (shared interface) and two
concrete implementations: RuleBasedSegmenter and CRFBasedSegmenter.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SyllableSegmenter(Protocol):
    """Protocol for syllable segmentation backends."""

    def segment(self, word: str) -> list[str]:
        """Segment a word into syllables.

        Args:
            word: A single Zolai word.

        Returns:
            List of syllable strings.
        """
        ...


class RuleBasedSegmenter:
    """Rule-based segmentation using Zolai phonotactics.

    Deterministic — no model file needed. Handles digraphs
    (th, kh, gh, ng, ch, ph, bh), diphthongs (aw, ai, ei, ou),
    and onset clusters per Zolai phonotactic constraints.

    Target: ~85% boundary F1 on well-spelled Zolai text.
    """

    def __init__(self) -> None:
        self._engine = None

    def _ensure_engine(self) -> None:
        """Lazily import the rule engine."""
        if self._engine is None:
            from .rules import segment_word
            self._engine = segment_word

    def segment(self, word: str) -> list[str]:
        """Segment a word using phonotactic rules.

        Args:
            word: A single Zolai word.

        Returns:
            List of syllable strings.
        """
        self._ensure_engine()
        assert self._engine is not None
        return self._engine(word)


class CRFBasedSegmenter:
    """CRF-based segmentation using sklearn-crfsuite.

    Trained on gold-annotated Zolai data. Higher accuracy on
    ambiguous cases (e.g., onset clusters, diphthong boundaries).

    Requires a trained model loaded via ``load()`` before use.
    Target: >94% boundary F1 after training.
    """

    def __init__(self) -> None:
        self._model = None

    def segment(self, word: str) -> list[str]:
        """Segment a word using the trained CRF model.

        Args:
            word: A single Zolai word.

        Returns:
            List of syllable strings.

        Raises:
            RuntimeError: If no model has been loaded.
        """
        if self._model is None:
            raise RuntimeError(
                "CRF model not loaded. Call load(path) first."
            )
        from .gold_dataset import extract_features
        features = [extract_features(word)]
        tags_list = self._model.predict(features)
        return self._tags_to_syllables(word, tags_list[0])

    def train(
        self, gold_data: list[tuple[str, list[str]]]
    ) -> None:
        """Train the CRF model on gold syllable data.

        Args:
            gold_data: List of (word, syllables) pairs.
        """
        from .gold_dataset import extract_features, word_to_bio

        words = [w for w, _ in gold_data]
        tag_sequences = [
            word_to_bio(w, syls) for w, syls in gold_data
        ]
        features = [extract_features(w) for w in words]

        try:
            import sklearn_crfsuite  # noqa: F811
        except ImportError as exc:
            raise ImportError(
                "sklearn-crfsuite is required for CRF mode. "
                "Install with: pip install sklearn-crfsuite"
            ) from exc

        self._model = sklearn_crfsuite.CRF(
            algorithm="lbfgs",
            c1=0.1,
            c2=0.1,
            max_iterations=100,
            all_possible_transitions=True,
        )
        self._model.fit(features, tag_sequences)

    def save(self, path: str) -> None:
        """Serialize the trained CRF model to disk.

        Args:
            path: File path for the model.
        """
        if self._model is None:
            raise RuntimeError("No model to save.")
        import pickle
        with open(path, "wb") as f:
            pickle.dump(self._model, f)

    def load(self, path: str) -> None:
        """Load a trained CRF model from disk.

        Args:
            path: File path of the model.
        """
        import pickle
        with open(path, "rb") as f:
            self._model = pickle.load(f)  # noqa: S301

    @staticmethod
    def _tags_to_syllables(
        word: str, tags: list[str]
    ) -> list[str]:
        """Convert BIO tags to syllable list."""
        syllables: list[str] = []
        current: list[str] = []
        for ch, tag in zip(word, tags):
            if tag == "B":
                if current:
                    syllables.append("".join(current))
                current = [ch]
            else:
                current.append(ch)
        if current:
            syllables.append("".join(current))
        return syllables if syllables else [word]
