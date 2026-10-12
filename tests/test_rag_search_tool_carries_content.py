"""`rag_search` must hand the model something it can actually read.

It used to return only ``{source, ref, text, score}`` — and ``text`` is the
matched *headword*. So for "nang bang hih thei" the model received

    [{'source': 'dictionary', 'ref': 'dictionary:bang', 'text': 'bang', ...}]

and, having been given a list of words with no meanings, reported "I have no
evidence for bang" while ``bang = what`` sat in the corpus the whole time. The
tool was working; it was returning a list of words.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zolai.agent.tools import executor as ex
from zolai.rag.retrieve import SearchResult


class _StubRetriever:
    def __init__(self, results):
        self._results = results

    def search(self, query, limit=10, source_filter=None):
        return self._results


def _with_results(monkeypatch, results):
    monkeypatch.setattr(ex, "_retriever", lambda: _StubRetriever(results))


def test_definition_travels_with_the_hit(monkeypatch):
    _with_results(
        monkeypatch,
        [
            SearchResult(
                id="dictionary:bang",
                text="bang",
                score=0.95,
                source="dictionary",
                metadata={"match_type": "exact", "definition": "what", "pos": "noun"},
            )
        ],
    )

    out = ex._rag_search({"query": "bang"})[0]

    assert out["text"] == "bang"
    assert out["definition"] == "what", "the gloss was dropped - the model cannot read it"
    assert out["pos"] == "noun"


def test_empty_and_unknown_metadata_is_omitted_not_emitted(monkeypatch):
    _with_results(
        monkeypatch,
        [
            SearchResult(
                id="dictionary:x",
                text="x",
                score=0.6,
                source="dictionary",
                metadata={"definition": "", "pos": "unknown"},
            )
        ],
    )

    out = ex._rag_search({"query": "x"})[0]

    assert "definition" not in out, "an empty gloss is noise the model must not read"
    assert "pos" not in out, "'unknown' is not information"


def test_bible_rows_carry_their_verse_text(monkeypatch):
    _with_results(
        monkeypatch,
        [
            SearchResult(
                id="bible_verses:1",
                text="genesis 3",
                score=0.8,
                source="bible_verses",
                metadata={"context": "Dust thou art, and unto dust shalt thou return."},
            )
        ],
    )

    assert ex._rag_search({"query": "genesis 3"})[0]["context"].startswith("Dust thou art")
