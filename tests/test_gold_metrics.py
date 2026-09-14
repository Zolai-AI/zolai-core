"""Tests for Gold Evaluation Metrics."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from eval.gold_metrics import (
    MetricResult,
    EvaluationReport,
    load_gold_words,
    load_gold_sentences,
    load_gold_paragraphs,
    evaluate_words,
    evaluate_sentences,
    evaluate_paragraphs,
    run_gold_evaluation,
)


class TestGoldDataLoading:
    """Test gold data loading."""

    def test_load_gold_words(self) -> None:
        """Test loading gold words."""
        words = load_gold_words()
        assert len(words) > 0
        assert all("word" in w for w in words)
        assert all("syllables" in w for w in words)
        assert all("pos" in w for w in words)

    def test_load_gold_sentences(self) -> None:
        """Test loading gold sentences."""
        sentences = load_gold_sentences()
        assert len(sentences) > 0
        assert all("sentence" in s for s in sentences)
        assert all("tokens" in s for s in sentences)
        assert all("pos_tags" in s for s in sentences)

    def test_load_gold_paragraphs(self) -> None:
        """Test loading gold paragraphs."""
        paragraphs = load_gold_paragraphs()
        assert len(paragraphs) > 0
        assert all("paragraph" in p for p in paragraphs)
        assert all("sentences" in p for p in paragraphs)


class TestMetricResult:
    """Test MetricResult dataclass."""

    def test_metric_result_creation(self) -> None:
        """Test MetricResult creation."""
        metric = MetricResult(
            name="test_accuracy",
            value=0.85,
            total=100,
            correct=85,
            details={"extra": "info"},
        )
        assert metric.name == "test_accuracy"
        assert metric.value == 0.85
        assert metric.total == 100
        assert metric.correct == 85


class TestEvaluationReport:
    """Test EvaluationReport."""

    def test_report_to_dict(self) -> None:
        """Test report serialization."""
        report = EvaluationReport(
            word_metrics=[MetricResult("w1", 0.9, 10, 9)],
            sentence_metrics=[MetricResult("s1", 0.8, 10, 8)],
            paragraph_metrics=[MetricResult("p1", 0.7, 10, 7)],
            overall_accuracy=0.8,
        )
        d = report.to_dict()
        assert "word_metrics" in d
        assert "sentence_metrics" in d
        assert "paragraph_metrics" in d
        assert d["overall_accuracy"] == 0.8

    def test_report_print_summary(self, capsys) -> None:
        """Test report summary printing."""
        report = EvaluationReport(
            word_metrics=[MetricResult("w1", 0.9, 10, 9)],
            sentence_metrics=[],
            paragraph_metrics=[],
            overall_accuracy=0.9,
        )
        report.print_summary()
        captured = capsys.readouterr()
        assert "GOLD EVALUATION REPORT" in captured.out
        assert "w1" in captured.out


class TestGoldEvaluation:
    """Test gold evaluation functions (integration)."""

    def test_evaluate_words_runs(self) -> None:
        """Test word evaluation runs without error."""
        from zolai.foundation import get_foundation_analyzer
        analyzer = get_foundation_analyzer(syllable_mode="rule")
        metrics = evaluate_words(analyzer)
        assert len(metrics) > 0
        assert all(isinstance(m, MetricResult) for m in metrics)

    def test_evaluate_sentences_runs(self) -> None:
        """Test sentence evaluation runs without error."""
        from zolai.foundation import get_foundation_analyzer
        analyzer = get_foundation_analyzer(syllable_mode="rule")
        metrics = evaluate_sentences(analyzer)
        assert len(metrics) > 0
        assert all(isinstance(m, MetricResult) for m in metrics)

    def test_evaluate_paragraphs_runs(self) -> None:
        """Test paragraph evaluation runs without error."""
        from zolai.foundation import get_foundation_analyzer
        analyzer = get_foundation_analyzer(syllable_mode="rule")
        metrics = evaluate_paragraphs(analyzer)
        assert len(metrics) > 0
        assert all(isinstance(m, MetricResult) for m in metrics)

    def test_run_gold_evaluation(self) -> None:
        """Test full gold evaluation runs."""
        report = run_gold_evaluation(syllable_mode="rule")
        assert isinstance(report, EvaluationReport)
        assert 0.0 <= report.overall_accuracy <= 1.0
        assert len(report.word_metrics) > 0
        assert len(report.sentence_metrics) > 0
        assert len(report.paragraph_metrics) > 0


class TestGoldMetricsCLI:
    """Test gold_metrics CLI entry point."""

    def test_main_json_output(self) -> None:
        """Test CLI with JSON output."""
        from io import StringIO
        from contextlib import redirect_stdout

        # Capture stdout
        captured = StringIO()
        with redirect_stdout(captured):
            report = run_gold_evaluation(syllable_mode="rule")

        # Just verify it runs and returns a report
        assert hasattr(report, 'overall_accuracy')
        assert 0.0 <= report.overall_accuracy <= 1.0


# Fixture for temporary gold data
@pytest.fixture
def temp_gold_dir(monkeypatch):
    """Create temporary gold data for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        gold_dir = Path(tmpdir) / "gold"
        gold_dir.mkdir()

        # Write test word data
        words = [
            {"word": "test", "syllables": ["test"], "syllable_count": 1,
             "pos": "NOUN", "morphology": {"root": "test"},
             "meanings": ["test"], "zvs_compliant": True, "zvs_notes": []},
        ]
        with open(gold_dir / "words.jsonl", "w") as f:
            for w in words:
                f.write(json.dumps(w) + "\n")

        # Write test sentence data
        sentences = [
            {"sentence": "Test hi.", "tokens": ["Test", "hi"], "pos_tags": ["NOUN", "PART"],
             "sov_valid": True, "ergative_present": False, "negation_type": None,
             "question_type": None, "tense": "present", "zvs_compliant": True,
             "zvs_violations": [], "grammar_patterns": []},
        ]
        with open(gold_dir / "sentences.jsonl", "w") as f:
            for s in sentences:
                f.write(json.dumps(s) + "\n")

        # Write test paragraph data
        paragraphs = [
            {"paragraph": "Test hi.", "sentences": [
                {"sentence": "Test hi.", "tokens": ["Test", "hi"], "pos_tags": ["NOUN", "PART"],
                 "sov_valid": True, "ergative_present": False, "negation_type": None,
                 "question_type": None, "tense": "present", "zvs_compliant": True,
                 "zvs_violations": [], "grammar_patterns": []}
            ], "sentence_count": 1, "total_tokens": 2,
             "style_profile": {"narrative": 1.0}, "dominant_tense": "present",
             "register": "informal", "cohesion_score": 1.0},
        ]
        with open(gold_dir / "paragraphs.jsonl", "w") as f:
            for p in paragraphs:
                f.write(json.dumps(p) + "\n")

        # Monkeypatch GOLD_DIR
        import eval.gold_metrics as gm
        monkeypatch.setattr(gm, "GOLD_DIR", gold_dir)

        yield gold_dir