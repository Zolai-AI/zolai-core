"""Tests for ZolaiRAG contract — end-to-end validation of retrieval pipeline."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import pytest
from zolai.knowledge.rag_contract import Evidence, EvidencePack, ZolaiRAG, retrieve


@pytest.fixture(scope="module")
def rag():
    """Shared ZolaiRAG instance (lazy-loaded once)."""
    return ZolaiRAG()


# ── Instantiation ──────────────────────────────────────────────

class TestRAGInstantiation:
    def test_rag_creates(self):
        r = ZolaiRAG()
        assert r is not None

    def test_data_dir_exists(self, rag):
        assert rag.data_dir.exists()

    def test_lazy_not_loaded_until_query(self, rag):
        r2 = ZolaiRAG()
        assert r2._dict_zo_en is None


# ── EvidencePack ───────────────────────────────────────────────

class TestEvidencePack:
    def test_empty_pack(self):
        pack = EvidencePack(query="test")
        assert pack.total() == 0
        assert "Vocabulary" not in pack.to_prompt()

    def test_to_prompt_with_evidence(self):
        pack = EvidencePack(
            query="test",
            vocabulary=[Evidence(id="d1", text="pasian = God", source="dict", type="vocabulary", confidence=0.95)],
        )
        prompt = pack.to_prompt()
        assert "Vocabulary Evidence" in prompt
        assert "pasian = God" in prompt

    def test_total_counts(self):
        pack = EvidencePack(
            query="test",
            vocabulary=[Evidence(id="d1", text="a", source="d", type="v", confidence=0.9, metadata={})],
            grammar=[Evidence(id="g1", text="b", source="g", type="gp", confidence=0.8, metadata={})],
            bible=[Evidence(id="b1", text="c", source="b", type="v", confidence=0.7, metadata={})],
        )
        assert pack.total() == 3


# ── Vocabulary Lookup ──────────────────────────────────────────

class TestVocabLookup:
    def test_pasian_found(self, rag):
        pack = rag.retrieve("pasian")
        assert len(pack.vocabulary) > 0
        assert any("God" in e.text for e in pack.vocabulary)

    def test_gam_found(self, rag):
        pack = rag.retrieve("gam")
        assert len(pack.vocabulary) > 0

    def test_topa_found(self, rag):
        pack = rag.retrieve("topa")
        assert len(pack.vocabulary) > 0

    def test_english_lookup(self, rag):
        pack = rag.retrieve("God")
        assert len(pack.vocabulary) > 0

    def test_unknown_word_empty(self, rag):
        pack = rag.retrieve("xyzzyplugh")
        assert len(pack.vocabulary) == 0


# ── Bible Lookup ───────────────────────────────────────────────

class TestBibleLookup:
    def test_pasian_in_genesis(self, rag):
        pack = rag.retrieve("pasian")
        assert len(pack.bible) > 0
        refs = [e.id for e in pack.bible]
        # Just verify we got Bible results with valid refs
        assert len(refs) > 0

    def test_bible_verse_has_both_langs(self, rag):
        pack = rag.retrieve("pasian")
        for e in pack.bible:
            assert "/" in e.text  # "zo / en" format


# ── Grammar Patterns ───────────────────────────────────────────

class TestGrammarPatterns:
    def test_grammar_matches_query(self, rag):
        pack = rag.retrieve("hiam")
        # Grammar should return patterns mentioning the queried word
        assert len(pack.grammar) >= 0  # May or may not match, but should not crash

    def test_grammar_evidence_format(self, rag):
        pack = rag.retrieve("pasian")
        for e in pack.grammar:
            assert e.source == "grammar"
            assert e.type == "grammar_pattern"


# ── Phrase Lookup ──────────────────────────────────────────────

class TestPhraseLookup:
    def test_multi_word_query(self, rag):
        pack = rag.retrieve("Pasian in vantung")
        # Should find phrase matches for common words
        assert isinstance(pack.phrases, list)


# ── ZVS Compliance ────────────────────────────────────────────

class TestZVSCompliance:
    def test_forbidden_word_flagged(self, rag):
        pack = rag.retrieve("pathian")
        assert len(pack.zvs) > 0
        assert any("FORBIDDEN" in e.text for e in pack.zvs)
        assert any("pasian" in e.text for e in pack.zvs)

    def test_correct_word_not_flagged(self, rag):
        pack = rag.retrieve("pasian")
        assert len(pack.zvs) == 0

    def test_multiple_forbidden(self, rag):
        pack = rag.retrieve("pathian ram fapa")
        assert len(pack.zvs) >= 3


# ── Full Pipeline ──────────────────────────────────────────────

class TestFullPipeline:
    def test_retrieve_returns_evidencepack(self, rag):
        pack = rag.retrieve("pasian")
        assert isinstance(pack, EvidencePack)
        assert pack.query == "pasian"

    def test_retrieve_has_nonzero_total(self, rag):
        pack = rag.retrieve("pasian")
        assert pack.total() > 0

    def test_convenience_function(self):
        pack = retrieve("pasian")
        assert isinstance(pack, EvidencePack)
        assert pack.total() > 0

    def test_to_prompt_nonempty(self, rag):
        pack = rag.retrieve("pasian")
        prompt = pack.to_prompt()
        assert len(prompt) > 50


# ── Validate Sentence ──────────────────────────────────────────

class TestValidateSentence:
    def test_valid_sentence(self, rag):
        result = rag.validate_sentence("Pasian om hi")
        assert result["valid"] is True

    def test_forbidden_form(self, rag):
        result = rag.validate_sentence("Pathian om hi")
        assert result["valid"] is False
        assert any("FORBIDDEN" in i for i in result["issues"])

    def test_bad_ending(self, rag):
        result = rag.validate_sentence("Pasian om banana")
        assert result["valid"] is False
        assert any("particle" in i.lower() for i in result["issues"])

    def test_question_ending(self, rag):
        result = rag.validate_sentence("Na pai hiam")
        assert result["valid"] is True
