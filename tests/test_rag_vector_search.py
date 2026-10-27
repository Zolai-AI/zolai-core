"""Vector fallback in the unified retriever — scoring, filters, kill switch."""

from __future__ import annotations

import json

import numpy as np
import pytest

from zolai.rag.retrieve import UnifiedRetriever
from zolai.rag.vectors import decode_embedding, encode_embedding


class _FakeEmbedder:
    def __init__(self, vector: list[float]) -> None:
        self._vector = np.asarray(vector, dtype=np.float32)

    def encode(self, _text: str, normalize_embeddings: bool = True) -> np.ndarray:
        return self._vector


INDEX = {
    "ids": ["a", "b", "c"],
    "texts": ["dictionary hit", "bible hit", "grammar hit"],
    "sources": ["dictionary", "bible_verses", "grammar_patterns"],
    "metas": [{}, {}, {}],
    "matrix": np.asarray(
        [
            [1.0, 0.0],
            [0.8, 0.6],
            [0.0, 1.0],
        ],
        dtype=np.float32,
    ),
}


@pytest.fixture()
def retriever(monkeypatch: pytest.MonkeyPatch) -> UnifiedRetriever:
    r = UnifiedRetriever(engine=None)  # type: ignore[arg-type]  # index is injected
    monkeypatch.setattr(r, "_get_embedder", lambda: _FakeEmbedder([1.0, 0.0]))
    monkeypatch.setattr(r, "_load_vector_index", lambda: INDEX)
    return r


def test_returns_hits_above_threshold_ranked(retriever: UnifiedRetriever) -> None:
    hits = retriever._vector_search("query", limit=5)

    assert [h.id for h in hits] == ["a", "b"]
    assert hits[0].score == pytest.approx(1.0)
    assert hits[1].score == pytest.approx(0.8)
    assert hits[1].metadata["match_type"] == "vector"


def test_source_filter_excludes_other_sources(retriever: UnifiedRetriever) -> None:
    hits = retriever._vector_search("query", limit=5, source_filter=["bible_verses"])

    assert [h.id for h in hits] == ["b"]


def test_limit_is_respected(retriever: UnifiedRetriever) -> None:
    hits = retriever._vector_search("query", limit=1)

    assert [h.id for h in hits] == ["a"]


def test_env_kill_switch(
    retriever: UnifiedRetriever, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZOLAI_VECTOR_SEARCH", "0")

    assert retriever._vector_search("query", limit=5) == []


def test_embedding_codec_reads_both_encodings() -> None:
    vector = [0.5, -1.25, 3.0]

    encoded = encode_embedding(vector)
    assert encoded.startswith('"')  # a JSON string, not an array
    np.testing.assert_allclose(decode_embedding(encoded), vector, rtol=0, atol=1e-7)
    np.testing.assert_allclose(
        decode_embedding(json.dumps(vector)), vector, rtol=0, atol=1e-7
    )
    assert decode_embedding(None) is None
    assert decode_embedding("not-json") is None
