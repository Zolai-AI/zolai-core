"""Tests for Gold Evaluation Metrics."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from eval.gold_metrics import (
    EvaluationReport,
    MetricResult,
    evaluate_paragraphs,
    evaluate_sentences,
    evaluate_words,
    load_gold_paragraphs,
    load_gold_sentences,
    load_gold_words,
    run_gold_evaluation,
)


class TestMetricResult:
    """Test MetricResult dataclass."""

    def test_metric_result_creation(self) -> None:
        """Test MetricResult creation."""
        metric = MetricResult(
            name="test_accuracy",
            value=0.85,
            total=100,
        )
        assert metric.name == "test_accuracy"
        assert metric.value == 0.85
        assert metric.total == 100


class TestEvaluationReport:
    """Test EvaluationReport."""

    def test_report_to_dict(self) -> None:
        """Test report serialization."""
        report = EvaluationReport(
            word_metrics={"w1": MetricResult("w1", 0.9, 10)},
            sentence_metrics={"s1": MetricResult("s1", 0.8, 10)},
            paragraph_metrics={"p1": MetricResult("p1", 0.7, 10)},
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
            word_metrics={"w1": MetricResult("w1", 0.9, 10)},
            sentence_metrics={},
            paragraph_metrics={},
            overall_accuracy=0.9,
        )
        report.print_summary()
        captured = capsys.readouterr()
        assert "GOLD EVALUATION REPORT" in captured.out
        assert "w1" in captured.out


class TestGoldDataLoading:
    """Test gold data loading."""

    def test_load_gold_words(self) -> None:
        """Test loading gold words."""
        gold = load_gold_words()
        assert len(gold) >= 10
        assert all("word" in item for item in gold)

    def test_load_gold_sentences(self) -> None:
        """Test loading gold sentences."""
        gold = load_gold_sentences()
        assert len(gold) >= 10
        assert all("sentence" in item for item in gold)

    def test_load_gold_paragraphs(self) -> None:
        """Test loading gold paragraphs."""
        gold = load_gold_paragraphs()
        assert len(gold) >= 3
        assert all("paragraph" in item for item in gold)


class TestGoldEvaluation:
    """Test gold evaluation functions (integration)."""

    def test_evaluate_words_runs(self) -> None:
        """Test word evaluation runs without error."""
        from zolai.foundation import get_foundation_analyzer
        analyzer = get_foundation_analyzer(syllable_mode="rule")
        metrics = evaluate_words(analyzer)
        assert len(metrics) > 0
        assert all(isinstance(m, MetricResult) for m in metrics.values())

    def test_evaluate_sentences_runs(self) -> None:
        """Test sentence evaluation runs without error."""
        from zolai.foundation import get_foundation_analyzer
        analyzer = get_foundation_analyzer(syllable_mode="rule")
        metrics = evaluate_sentences(analyzer)
        assert len(metrics) > 0
        assert all(isinstance(m, MetricResult) for m in metrics.values())

    def test_evaluate_paragraphs_runs(self) -> None:
        """Test paragraph evaluation runs without error."""
        from zolai.foundation import get_foundation_analyzer
        analyzer = get_foundation_analyzer(syllable_mode="rule")
        metrics = evaluate_paragraphs(analyzer)
        assert len(metrics) > 0
        assert all(isinstance(m, MetricResult) for m in metrics.values())

    def test_run_gold_evaluation(self) -> None:
        """Test full gold evaluation runs."""
        report = run_gold_evaluation(syllable_mode="rule")
        assert isinstance(report, EvaluationReport)
        assert 0.0 <= report.overall_accuracy <= 1.0


class TestGoldMetricsCLI:
    """Test gold metrics CLI."""

    def test_main_json_output(self, monkeypatch) -> None:
        """Test CLI with JSON output."""
        from contextlib import redirect_stdout
        from io import StringIO

        from eval import gold_metrics as gm

        with tempfile.TemporaryDirectory() as tmpdir:
            gold_dir = Path(tmpdir) / "gold"
            gold_dir.mkdir()

            words_file = gold_dir / "words.jsonl"
            words_file.write_text(
                '{"word": "pasian", "syllable_count": 2, '
                '"pos": "NOUN", "root": "pasian", '
                '"zvs_compliant": true, "meaning": "God"}\n'
            )
            sentences_file = gold_dir / "sentences.jsonl"
            sentences_file.write_text(
                '{"sentence": "Pasian om hi.", "sov_valid": true, '
                '"ergative_present": false, "negation_type": null, '
                '"question_type": null, "tense": "present", '
                '"zvs_compliant": true, "token_count": 3}\n'
            )
            paragraphs_file = gold_dir / "paragraphs.jsonl"
            paragraphs_file.write_text(
                '{"paragraph": "Pasian om hi.", "sentence_count": 1, '
                '"register": "biblical", "dominant_tense": "present"}\n'
            )

            monkeypatch.setattr(gm, "GOLD_DIR", gold_dir)

            stdout = StringIO()
            with redirect_stdout(stdout):
                rc = gm.main(["--json", "--syllable-mode", "rule"])

            assert rc == 0
            output = stdout.getvalue()
            data = json.loads(output)
            assert "overall_accuracy" in data
