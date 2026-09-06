"""Tests for Zolai bilingual conversation modules."""
from zolai.api.conversation_memory import ConversationMemory
from zolai.api.rag_context import ZolaiRAGContext, build_zolai_context
from zolai.api.zvs_checker import ZVSComplianceChecker


class TestZVSComplianceChecker:
    def test_forbidden_form_detected(self):
        checker = ZVSComplianceChecker(use_default_exceptions=False)
        result = checker.check_response("Pasian in ram a piangsak hi.")
        assert not result['is_compliant']
        assert len(result['violations']) == 1
        assert result['violations'][0][0] == 'ram'
        assert result['violations'][0][1] == 'gam'

    def test_correct_form_passes(self):
        checker = ZVSComplianceChecker()
        result = checker.check_response("Pasian in gam a piangsak hi.")
        assert result['is_compliant']
        assert len(result['violations']) == 0

    def test_multiple_violations(self):
        checker = ZVSComplianceChecker(use_default_exceptions=False)
        result = checker.check_response("pathian in ram a piangsak hi.")
        assert not result['is_compliant']
        assert len(result['violations']) == 2

    def test_correction_applied(self):
        checker = ZVSComplianceChecker(use_default_exceptions=False)
        result = checker.check_response("pathian in ram a piangsak hi.")
        assert 'pasian' in result['corrected_text']
        assert 'gam' in result['corrected_text']

    def test_exception_registry(self):
        checker = ZVSComplianceChecker(use_default_exceptions=True)
        result = checker.check_response("pathian in gam a piangsak hi.")
        assert result['is_compliant']  # pathian is in exceptions


class TestConversationMemory:
    def test_add_turn(self):
        mem = ConversationMemory(max_turns=3)
        mem.add_turn("test", "user", "hello")
        history = mem.get_history("test")
        assert len(history) == 1
        assert history[0]['role'] == 'user'

    def test_max_turns(self):
        mem = ConversationMemory(max_turns=2)
        mem.add_turn("test", "user", "turn1")
        mem.add_turn("test", "assistant", "turn2")
        mem.add_turn("test", "user", "turn3")
        history = mem.get_history("test")
        assert len(history) == 2
        assert history[0]['text'] == 'turn2'

    def test_vocabulary_tracking(self):
        mem = ConversationMemory()
        mem.add_vocabulary("test", ["pasian", "topa"])
        vocab = mem.get_vocabulary("test")
        assert "pasian" in vocab
        assert "topa" in vocab

    def test_context_summary(self):
        mem = ConversationMemory()
        mem.add_vocabulary("test", ["pasian"])
        mem.add_turn("test", "user", "What is God?")
        summary = mem.get_context_summary("test")
        assert "pasian" in summary
        assert "What is God?" in summary


class TestRAGContext:
    def test_extract_words(self):
        rag = ZolaiRAGContext()
        words = rag.extract_zolai_words("Na dam na?")
        # Should extract Zolai-looking words
        assert isinstance(words, list)

    def test_build_context_no_data(self):
        # When data files don't exist, should return graceful message
        context = build_zolai_context("pasian", max_tokens=100)
        assert isinstance(context, str)
        assert len(context) > 0
