"""
Tests for Zolai AI memory layers, learning engine, accuracy scorer,
learning report, and rules reference.
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Memory Layers
# ---------------------------------------------------------------------------

class TestL1WorkingMemory:
    def test_add_and_get_context(self):
        from zolai.api.memory_layers import L1WorkingMemory
        mem = L1WorkingMemory(max_turns=5)
        mem.add("user", "hello")
        mem.add("assistant", "hi there")
        ctx = mem.get_context()
        assert "Current Conversation" in ctx
        assert "hello" in ctx

    def test_max_turns_eviction(self):
        from zolai.api.memory_layers import L1WorkingMemory
        mem = L1WorkingMemory(max_turns=3)
        for i in range(5):
            mem.add("user", f"turn-{i}")
        assert len(mem.turns) == 3
        assert mem.turns[0]["text"] == "turn-2"

    def test_clear(self):
        from zolai.api.memory_layers import L1WorkingMemory
        mem = L1WorkingMemory()
        mem.add("user", "hi")
        mem.clear()
        assert len(mem.turns) == 0
        assert mem.get_context() == ""


class TestL2SessionMemory:
    def test_persistence(self, tmp_path):
        from zolai.api.memory_layers import L2SessionMemory, DATA_DIR
        with patch("zolai.api.memory_layers.DATA_DIR", tmp_path):
            mem = L2SessionMemory(session_id="test_persist")
            mem.add("user", "persist me")
            assert len(mem.turns) == 1

            # Reload from disk
            mem2 = L2SessionMemory(session_id="test_persist")
            assert len(mem2.turns) == 1
            assert mem2.turns[0]["text"] == "persist me"


class TestL3VocabularyMastery:
    def test_update_and_levels(self, tmp_path):
        from zolai.api.memory_layers import L3VocabularyMastery
        with patch("zolai.api.memory_layers.DATA_DIR", tmp_path):
            mem = L3VocabularyMastery()
            # New word: confidence 0 → level 'new'
            mem.update("gam", correct=True, context="God")
            entry = mem.get_word("gam")
            assert entry is not None
            assert entry["level"] == "learning"  # 1 attempt, 1 correct = 100% ≥ 0.7
            assert entry["attempts"] == 1

            # Make mastered: 5 attempts, 90%+ confidence
            for _ in range(9):
                mem.update("gam", correct=True)
            entry = mem.get_word("gam")
            assert entry["level"] == "mastered"
            assert entry["attempts"] == 10

    def test_stats(self, tmp_path):
        from zolai.api.memory_layers import L3VocabularyMastery
        with patch("zolai.api.memory_layers.DATA_DIR", tmp_path):
            mem = L3VocabularyMastery()
            mem.update("topa", correct=True)
            mem.update("tui", correct=False)
            stats = mem.get_stats()
            assert stats["total"] == 2


class TestL4CrossSessionPatterns:
    def test_add_correction(self, tmp_path):
        from zolai.api.memory_layers import L4CrossSessionPatterns
        with patch("zolai.api.memory_layers.DATA_DIR", tmp_path):
            l4 = L4CrossSessionPatterns()
            l4.add_correction("pathian", "pasian", "ZVS2018")
            assert len(l4.patterns) == 1
            ctx = l4.get_context()
            assert "pathian" in ctx
            assert "pasian" in ctx

    def test_max_entries_eviction(self, tmp_path):
        from zolai.api.memory_layers import L4CrossSessionPatterns
        with patch("zolai.api.memory_layers.DATA_DIR", tmp_path):
            l4 = L4CrossSessionPatterns(max_entries=3)
            for i in range(5):
                l4.add_pattern("grammar", f"pattern-{i}")
            assert len(l4.patterns) == 3


class TestMemoryLayers:
    def test_add_turn_and_stats(self, tmp_path):
        from zolai.api.memory_layers import MemoryLayers
        with patch("zolai.api.memory_layers.DATA_DIR", tmp_path):
            ml = MemoryLayers(session_id="test_unified")
            ml.add_turn("user", "test input")
            ml.add_turn("assistant", "test output")
            stats = ml.get_stats()
            assert stats["l1_turns"] == 2
            assert stats["l2_turns"] == 2

    def test_add_correction_updates_both_l3_l4(self, tmp_path):
        from zolai.api.memory_layers import MemoryLayers
        with patch("zolai.api.memory_layers.DATA_DIR", tmp_path):
            ml = MemoryLayers(session_id="test_correction")
            ml.add_correction("pathian", "pasian", "ZVS2018")
            # L4 should have the correction
            assert len(ml.l4.patterns) == 1
            # L3 should have both words
            assert ml.l3.get_word("pathian") is not None
            assert ml.l3.get_word("pasian") is not None


# ---------------------------------------------------------------------------
# Learning Engine
# ---------------------------------------------------------------------------

class TestLearningEngine:
    def test_process_feedback(self, tmp_path):
        from zolai.api.memory_layers import MemoryLayers
        from zolai.api.learning_engine import LearningEngine
        with patch("zolai.api.memory_layers.DATA_DIR", tmp_path):
            with patch("zolai.api.learning_engine.DATA_DIR", tmp_path):
                mem = MemoryLayers(session_id="test_le")
                engine = LearningEngine(mem)
                correction = engine.process_feedback(
                    original="pathian",
                    corrected="pasian",
                    rule="ZVS2018",
                )
                assert correction["rule"] == "ZVS2018"
                assert len(engine.corrections) >= 1

    def test_learning_velocity(self, tmp_path):
        from zolai.api.memory_layers import MemoryLayers
        from zolai.api.learning_engine import LearningEngine
        with patch("zolai.api.memory_layers.DATA_DIR", tmp_path):
            with patch("zolai.api.learning_engine.DATA_DIR", tmp_path):
                mem = MemoryLayers(session_id="test_velocity")
                engine = LearningEngine(mem)
                velocity = engine.get_learning_velocity()
                assert velocity["corrections_per_session"] == 0
                assert velocity["trend"] == "stable"


# ---------------------------------------------------------------------------
# Accuracy Scorer
# ---------------------------------------------------------------------------

class TestAccuracyScorer:
    def test_score_word_no_data(self, tmp_path):
        from zolai.api.accuracy_scorer import AccuracyScorer
        with patch("zolai.api.accuracy_scorer.DATA_DIR", tmp_path):
            scorer = AccuracyScorer()
            result = scorer.score_word("nonexistent")
            assert result["confidence"] == "UNCERTAIN"
            assert result["source_count"] == 0

    def test_score_phrase(self, tmp_path):
        from zolai.api.accuracy_scorer import AccuracyScorer
        with patch("zolai.api.accuracy_scorer.DATA_DIR", tmp_path):
            scorer = AccuracyScorer()
            result = scorer.score_phrase(["word1", "word2"])
            assert "overall_confidence" in result
            assert len(result["word_scores"]) == 2


# ---------------------------------------------------------------------------
# Learning Report
# ---------------------------------------------------------------------------

class TestLearningReport:
    def test_generate_text_report(self, tmp_path):
        from zolai.api.memory_layers import MemoryLayers
        from zolai.api.learning_engine import LearningEngine
        from zolai.api.learning_report import LearningReport
        with patch("zolai.api.memory_layers.DATA_DIR", tmp_path):
            with patch("zolai.api.learning_engine.DATA_DIR", tmp_path):
                with patch("zolai.api.learning_report.DATA_DIR", tmp_path):
                    mem = MemoryLayers(session_id="test_report")
                    engine = LearningEngine(mem)
                    report = LearningReport(mem, engine)
                    text = report.generate_text_report()
                    assert "Zolai AI Learning Report" in text
                    assert "Vocabulary" in text

    def test_save_report(self, tmp_path):
        from zolai.api.memory_layers import MemoryLayers
        from zolai.api.learning_engine import LearningEngine
        from zolai.api.learning_report import LearningReport
        with patch("zolai.api.memory_layers.DATA_DIR", tmp_path):
            with patch("zolai.api.learning_engine.DATA_DIR", tmp_path):
                with patch("zolai.api.learning_report.DATA_DIR", tmp_path):
                    mem = MemoryLayers(session_id="test_save_report")
                    engine = LearningEngine(mem)
                    report = LearningReport(mem, engine)
                    path = report.save_report()
                    assert path.exists()


# ---------------------------------------------------------------------------
# Zolai Rules Reference
# ---------------------------------------------------------------------------

class TestZolaiRules:
    def test_forbidden_form_detection(self):
        from zolai.rules.zolai_rules_reference import ZolaiRules
        violations = ZolaiRules.check_forbidden("He prayed to pathian in the temple.")
        assert len(violations) == 1
        assert violations[0][0] == "pathian"
        assert violations[0][1] == "pasian"

    def test_no_violations(self):
        from zolai.rules.zolai_rules_reference import ZolaiRules
        violations = ZolaiRules.check_forbidden("He prayed to pasian.")
        assert len(violations) == 0

    def test_token_efficient_summary(self):
        from zolai.rules.zolai_rules_reference import ZolaiRules
        summary = ZolaiRules.get_token_efficient_summary()
        assert "SOV" in summary
        assert "pathian" in summary  # forbidden forms listed

    def test_multiple_violations(self):
        from zolai.rules.zolai_rules_reference import ZolaiRules
        text = "pathian bawipa ram fapa siangpahrang cu cun"
        violations = ZolaiRules.check_forbidden(text)
        assert len(violations) == 7

    def test_get_rules_reference(self):
        from zolai.rules import get_rules_reference, ZolaiRules
        ref = get_rules_reference()
        assert ref.WORD_ORDER == "SOV"
        assert ref.ERGATIVE == "in"
        assert isinstance(ref.FORBIDDEN_FORMS, dict)


# ---------------------------------------------------------------------------
# py_compile guard
# ---------------------------------------------------------------------------

def test_all_modules_importable():
    """Smoke test: all new modules can be imported."""
    from zolai.api.memory_layers import (
        L1WorkingMemory, L2SessionMemory, L3VocabularyMastery,
        L4CrossSessionPatterns, MemoryLayers,
    )
    from zolai.api.learning_engine import LearningEngine, get_learning_engine
    from zolai.api.accuracy_scorer import AccuracyScorer, get_accuracy_scorer
    from zolai.api.learning_report import LearningReport, get_learning_report
    from zolai.rules import ZolaiRules, get_rules_reference
    assert L1WorkingMemory is not None
    assert LearningEngine is not None
    assert AccuracyScorer is not None
    assert LearningReport is not None
    assert ZolaiRules is not None
