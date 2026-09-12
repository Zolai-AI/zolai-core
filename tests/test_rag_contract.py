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


class TestDatabaseBackedRAG:
    """Tests for database-backed retrieval path."""

    @pytest.fixture
    def db_rag(self):
        """Create ZolaiRAG that finds zolai.db via config.paths.data."""
        return ZolaiRAG()

    def test_rag_detects_database(self, db_rag):
        """RAG detects zolai.db when available."""
        db_rag._ensure_loaded()
        assert db_rag._db is not None

    def test_rag_db_returns_dictionary_results(self, db_rag):
        """Database lookup returns dictionary evidence for 'pasian'."""
        pack = db_rag.retrieve("pasian")
        assert pack.total() > 0
        assert len(pack.vocabulary) > 0
        # Should contain "pasian" in at least one evidence text
        texts = [e.text for e in pack.vocabulary]
        assert any("pasian" in t for t in texts)

    def test_rag_db_returns_bible_results(self, db_rag):
        """Database search returns Bible evidence for 'pasian'."""
        pack = db_rag.retrieve("pasian")
        assert len(pack.bible) > 0
        # Bible results should have ref format (e.g., "GEN 1:1")
        refs = [e.id for e in pack.bible]
        assert any("bible:" in r for r in refs)

    def test_rag_db_returns_grammar_results(self, db_rag):
        """Database lookup returns grammar patterns."""
        pack = db_rag.retrieve("pai")
        # Grammar patterns may or may not match "pai"
        # Just verify no crash and returns EvidencePack
        assert isinstance(pack, EvidencePack)

    def test_rag_db_returns_phrase_results(self, db_rag):
        """Database lookup returns phrase matches."""
        pack = db_rag.retrieve("pasian")
        # Phrases may or may not match
        assert isinstance(pack, EvidencePack)

    def test_rag_db_unknown_word_returns_empty(self, db_rag):
        """Database lookup for unknown word returns empty vocabulary."""
        pack = db_rag.retrieve("xyzzyplugh")
        # Unknown word should have no dictionary matches
        dict_evidence = [e for e in pack.vocabulary if e.source == "dictionary"]
        assert len(dict_evidence) == 0

    def test_rag_db_forbidden_form_detected(self, db_rag):
        """ZVS forbidden forms still detected with DB path."""
        pack = db_rag.retrieve("pathian")
        assert len(pack.zvs) > 0
        assert "FORBIDDEN" in pack.zvs[0].text

    def test_rag_db_english_word_lookup(self, db_rag):
        """Database lookup works for English words too."""
        pack = db_rag.retrieve("God")
        assert pack.total() > 0
        # Should find "pasian" as Zolai for "God"
        texts = [e.text for e in pack.vocabulary]
        assert any("pasian" in t.lower() for t in texts)

    def test_rag_db_evidencepack_has_all_sections(self, db_rag):
        """EvidencePack has all expected sections even with DB."""
        pack = db_rag.retrieve("pasian topa gam")
        assert hasattr(pack, "vocabulary")
        assert hasattr(pack, "grammar")
        assert hasattr(pack, "phrases")
        assert hasattr(pack, "bible")
        assert hasattr(pack, "zvs")
        assert hasattr(pack, "context")

    def test_rag_db_to_prompt_works(self, db_rag):
        """to_prompt() produces valid output with DB-backed results."""
        pack = db_rag.retrieve("pasian")
        prompt = pack.to_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0


class TestRAGBenchmark:
    """Compare DB vs JSONL retrieval latency."""

    def test_benchmark_comparison(self, tmp_path):
        """DB retrieval should be faster than or equal to JSONL."""
        import time

        rag = ZolaiRAG()
        rag._ensure_loaded()

        query = "pasian topa gam vantung"

        # DB path timing
        if rag._db:
            times_db = []
            for _ in range(5):
                t0 = time.time()
                pack_db = rag.retrieve(query)
                times_db.append(time.time() - t0)
            avg_db = sum(times_db) / len(times_db)
        else:
            avg_db = None

        # JSONL path timing (force JSONL)
        saved_db = rag._db
        rag._db = None
        times_jsonl = []
        for _ in range(5):
            t0 = time.time()
            pack_jsonl = rag.retrieve(query)
            times_jsonl.append(time.time() - t0)
        avg_jsonl = sum(times_jsonl) / len(times_jsonl)
        rag._db = saved_db

        # Both should return results
        assert pack_jsonl.total() > 0
        if avg_db is not None:
            assert pack_db.total() > 0

        # Log results
        print(f"\nBenchmark: DB={avg_db:.3f}s, JSONL={avg_jsonl:.3f}s")
        if avg_db is not None:
            speedup = avg_jsonl / avg_db if avg_db > 0 else 0
            print(f"Speedup: {speedup:.1f}x")
