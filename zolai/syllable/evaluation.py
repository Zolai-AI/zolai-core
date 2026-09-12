"""Comprehensive evaluation framework for syllable segmentation (SylBreak4All M6).

Provides:
- Standard metrics: boundary/syllable/word precision, recall, F1
- Per-syllable-count breakdown
- Error analysis: confusion matrix, over/under-segmentation, boundary shift
- Statistical significance testing (McNemar's test)
- Comparison report: rule-based vs CRF vs SentencePiece baseline
- HTML/Markdown report generation with visualizations
"""

# ruff: noqa: F821  # False positives from CSS in HTML template strings

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Re-use existing metrics from eval.py
from .eval import (
    EvalReport,
    evaluate,
)


@dataclass(frozen=True, slots=True)
class ErrorAnalysis:
    """Detailed error analysis for a segmenter."""
    over_segmentation: int = 0
    under_segmentation: int = 0
    boundary_shift: int = 0
    correct_syllable_count: int = 0
    total_errors: int = 0
    by_syllable_count: dict[int, dict[str, int]] = field(default_factory=dict)
    by_word_frequency: dict[str, dict[str, int]] = field(default_factory=dict)
    common_errors: list[dict] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StatisticalTestResult:
    """Result of statistical significance test."""
    test_name: str
    statistic: float
    p_value: float
    significant: bool
    interpretation: str


@dataclass
class SegmenterResult:
    """Complete evaluation result for one segmenter."""
    name: str
    eval_report: EvalReport
    error_analysis: ErrorAnalysis
    predictions: list[list[str]]
    gold: list[list[str]]
    words: list[str]


class SyllableEvaluator:
    """Comprehensive evaluation for syllable segmenters."""

    def __init__(self) -> None:
        self.segmenters: dict[str, Any] = {}

    def add_segmenter(self, name: str, segmenter: Any) -> None:
        """Register a segmenter for evaluation.

        Args:
            name: Display name for the segmenter.
            segmenter: Object with segment(word: str) -> list[str] method.
        """
        self.segmenters[name] = segmenter

    def _load_test_data(self, test_path: str | Path) -> list[dict]:
        """Load test data from JSONL file."""
        data = []
        with Path(test_path).open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    word = entry.get("word", "").lower().strip()
                    syllables = entry.get("syllables", [])
                    if word and syllables:
                        data.append({"word": word, "syllables": syllables, **entry})
                except json.JSONDecodeError:
                    continue
        return data

    def evaluate_single(
        self,
        segmenter: Any,
        test_data: list[dict],
        word_frequencies: dict[str, int] | None = None,
    ) -> SegmenterResult:
        """Evaluate one segmenter on test data.

        Args:
            segmenter: Segmenter with segment(word) -> list[str] method.
            test_data: List of dicts with "word" and "syllables" keys.
            word_frequencies: Optional dict mapping word -> frequency.

        Returns:
            SegmenterResult with all metrics and error analysis.
        """
        words = [entry["word"] for entry in test_data]
        gold = [entry["syllables"] for entry in test_data]

        # Get predictions
        predictions = []
        for word in words:
            try:
                pred = segmenter.segment(word)
            except Exception:
                pred = [word]  # Fallback: whole word as one syllable
            predictions.append(pred)

        # Compute standard metrics
        eval_report = evaluate(predictions, gold)

        # Error analysis
        error_analysis = self._analyze_errors(
            words, predictions, gold, word_frequencies
        )

        return SegmenterResult(
            name=type(segmenter).__name__,
            eval_report=eval_report,
            error_analysis=error_analysis,
            predictions=predictions,
            gold=gold,
            words=words,
        )

    def _analyze_errors(
        self,
        words: list[str],
        predictions: list[list[str]],
        gold: list[list[str]],
        word_frequencies: dict[str, int] | None = None,
    ) -> ErrorAnalysis:
        """Analyze error patterns in detail."""
        over_seg = 0
        under_seg = 0
        boundary_shift = 0
        correct_syl_count = 0
        total_errors = 0

        by_syl_count: dict[int, dict[str, int]] = defaultdict(
            lambda: {"total": 0, "correct": 0, "over": 0, "under": 0, "shift": 0}
        )

        freq_buckets: dict[str, dict[str, int]] = defaultdict(
            lambda: {"total": 0, "correct": 0, "errors": 0}
        )

        common_errors: list[dict] = []

        for i, (word, pred, gld) in enumerate(zip(words, predictions, gold)):
            pred_count = len(pred)
            gold_count = len(gld)

            syl_key = gold_count
            by_syl_count[syl_key]["total"] += 1

            freq = word_frequencies.get(word, 0) if word_frequencies else 0
            if freq >= 100:
                freq_key = "high (>=100)"
            elif freq >= 10:
                freq_key = "medium (10-99)"
            elif freq >= 1:
                freq_key = "low (1-9)"
            else:
                freq_key = "unseen (0)"
            freq_buckets[freq_key]["total"] += 1

            if pred == gld:
                by_syl_count[syl_key]["correct"] += 1
                freq_buckets[freq_key]["correct"] += 1
                correct_syl_count += 1
                continue

            total_errors += 1
            freq_buckets[freq_key]["errors"] += 1

            # Classify error type
            if pred_count > gold_count:
                over_seg += 1
                by_syl_count[syl_key]["over"] += 1
                error_type = "over-segmentation"
            elif pred_count < gold_count:
                under_seg += 1
                by_syl_count[syl_key]["under"] += 1
                error_type = "under-segmentation"
            else:
                # Same syllable count but different boundaries
                boundary_shift += 1
                by_syl_count[syl_key]["shift"] += 1
                error_type = "boundary shift"

            # Track common error patterns (limit to top 20)
            if len(common_errors) < 20:
                common_errors.append({
                    "word": word,
                    "gold": gld,
                    "predicted": pred,
                    "error_type": error_type,
                    "frequency": freq,
                })

        # Convert defaultdicts to regular dicts
        by_syl_final = {
            k: v for k, v in sorted(by_syl_count.items())
        }
        freq_final = dict(freq_buckets)

        return ErrorAnalysis(
            over_segmentation=over_seg,
            under_segmentation=under_seg,
            boundary_shift=boundary_shift,
            correct_syllable_count=correct_syl_count,
            total_errors=total_errors,
            by_syllable_count=by_syl_final,
            by_word_frequency=freq_final,
            common_errors=common_errors,
        )

    def compare_segmenters(
        self,
        test_data: list[dict],
        word_frequencies: dict[str, int] | None = None,
    ) -> dict[str, SegmenterResult]:
        """Compare all registered segmenters.

        Args:
            test_data: List of dicts with "word" and "syllables" keys.
            word_frequencies: Optional dict mapping word -> frequency.

        Returns:
            Dict mapping segmenter name to SegmenterResult.
        """
        results = {}
        for name, segmenter in self.segmenters.items():
            print(f"Evaluating {name}...")
            result = self.evaluate_single(segmenter, test_data, word_frequencies)
            result.name = name
            results[name] = result
        return results

    def error_analysis(
        self,
        segmenter: Any,
        test_data: list[dict],
        word_frequencies: dict[str, int] | None = None,
    ) -> ErrorAnalysis:
        """Analyze error patterns for a single segmenter."""
        result = self.evaluate_single(segmenter, test_data, word_frequencies)
        return result.error_analysis

    def statistical_test(
        self,
        seg1_results: SegmenterResult,
        seg2_results: SegmenterResult,
    ) -> StatisticalTestResult:
        """McNemar's test for significance between two segmenters.

        Tests if the difference in word-level accuracy is statistically significant.

        Args:
            seg1_results: Results from first segmenter.
            seg2_results: Results from second segmenter.

        Returns:
            StatisticalTestResult with test statistic and p-value.
        """
        # McNemar's test: compare paired nominal data
        # b = seg1 correct, seg2 wrong
        # c = seg1 wrong, seg2 correct
        b = 0  # seg1 right, seg2 wrong
        c = 0  # seg1 wrong, seg2 right

        for p1, p2, g in zip(
            seg1_results.predictions,
            seg2_results.predictions,
            seg1_results.gold,
        ):
            correct1 = p1 == g
            correct2 = p2 == g
            if correct1 and not correct2:
                b += 1
            elif not correct1 and correct2:
                c += 1

        # McNemar's test with continuity correction
        # chi^2 = (|b - c| - 1)^2 / (b + c)
        if b + c == 0:
            return StatisticalTestResult(
                test_name="McNemar's test",
                statistic=0.0,
                p_value=1.0,
                significant=False,
                interpretation="No disagreements between segmenters",
            )

        statistic = (abs(b - c) - 1) ** 2 / (b + c)

        # p-value from chi-squared distribution with 1 df
        # Using approximation: p = 2 * (1 - Phi(sqrt(chi2)))
        from math import erf, sqrt
        chi2 = statistic
        p_value = 2 * (1 - 0.5 * (1 + erf(sqrt(chi2 / 2))))

        significant = p_value < 0.05

        if significant:
            winner = seg1_results.name if b > c else seg2_results.name
            interpretation = f"{winner} significantly outperforms the other (p={p_value:.4f})"
        else:
            interpretation = f"No significant difference (p={p_value:.4f})"

        return StatisticalTestResult(
            test_name="McNemar's test (with continuity correction)",
            statistic=statistic,
            p_value=p_value,
            significant=significant,
            interpretation=interpretation,
        )

    def _load_word_frequencies(self, freq_path: str | Path | None) -> dict[str, int]:
        """Load word frequencies from JSONL file."""
        if not freq_path:
            return {}
        freq: dict[str, int] = {}
        path = Path(freq_path)
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    word = data.get("word", "").lower().strip()
                    count = data.get("frequency", 0)
                    if word:
                        freq[word] = count
                except json.JSONDecodeError:
                    continue
        return freq

    def generate_report(
        self,
        results: dict[str, SegmenterResult],
        output_path: str | Path,
        format: str = "html",
        title: str = "Syllable Segmentation Evaluation Report",
    ) -> None:
        """Generate evaluation report in HTML or Markdown format.

        Args:
            results: Dict from compare_segmenters().
            output_path: Output file path.
            format: "html" or "markdown".
            title: Report title.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if format.lower() == "html":
            content = self._generate_html_report(results, title)
        else:
            content = self._generate_markdown_report(results, title)

        output_path.write_text(content, encoding="utf-8")
        print(f"Report written to {output_path}")

    def _generate_markdown_report(
        self, results: dict[str, SegmenterResult], title: str
    ) -> str:
        """Generate Markdown evaluation report."""
        lines = [f"# {title}\n"]

        # Summary table
        lines.append("## Summary Metrics\n")
        lines.append(
            "| Segmenter | Boundary P | Boundary R | Boundary F1 | Syllable Acc | Word Acc | Total Words |"
        )
        lines.append(
            "|-----------|------------|------------|-------------|--------------|----------|-------------|"
        )

        for name, result in results.items():
            r = result.eval_report
            lines.append(
                f"| {name} | {r.boundary_precision:.4f} | {r.boundary_recall:.4f} | "
                f"{r.boundary_f1:.4f} | {r.syllable_acc:.4f} | {r.word_acc:.4f} | {r.total_words} |"
            )
        lines.append("")

        # Per-syllable-count breakdown
        lines.append("## Per-Syllable-Count Accuracy\n")
        lines.append(
            "| Segmenter | Syllable Count | Accuracy | Total | Correct |"
        )
        lines.append(
            "|-----------|----------------|----------|-------|---------|"
        )

        for name, result in results.items():
            for syl_count, stats in result.error_analysis.by_syllable_count.items():
                total = stats.get("total", 0)
                correct = stats.get("correct", 0)
                acc = correct / total if total > 0 else 0
                lines.append(
                    f"| {name} | {syl_count} | {acc:.4f} | {total} | {correct} |"
                )
        lines.append("")

        # Error analysis
        lines.append("## Error Analysis\n")
        for name, result in results.items():
            ea = result.error_analysis
            lines.append(f"### {name}")
            lines.append(
                f"- **Total words**: {result.eval_report.total_words}"
            )
            lines.append(f"- **Correct syllable count**: {ea.correct_syllable_count}")
            lines.append(f"- **Total errors**: {ea.total_errors}")
            lines.append(f"- **Over-segmentation**: {ea.over_segmentation}")
            lines.append(f"- **Under-segmentation**: {ea.under_segmentation}")
            lines.append(f"- **Boundary shift**: {ea.boundary_shift}")
            lines.append("")

            lines.append("#### By Word Frequency")
            lines.append(
                "| Frequency Bucket | Total | Correct | Errors | Accuracy |"
            )
            lines.append(
                "|------------------|-------|---------|--------|----------|"
            )
            for bucket, stats in result.error_analysis.by_word_frequency.items():
                total = stats.get("total", 0)
                correct = stats.get("correct", 0)
                errors = stats.get("errors", 0)
                acc = correct / total if total > 0 else 0
                lines.append(
                    f"| {bucket} | {total} | {correct} | {errors} | {acc:.4f} |"
                )
            lines.append("")

            lines.append("#### Common Errors (Top 10)")
            lines.append("| Word | Gold | Predicted | Error Type | Frequency |")
            lines.append("|------|------|-----------|------------|-----------|")
            for err in result.error_analysis.common_errors[:10]:
                lines.append(
                    f"| {err['word']} | {err['gold']} | {err['predicted']} | "
                    f"{err['error_type']} | {err['frequency']} |"
                )
            lines.append("")

        # Statistical tests
        lines.append("## Statistical Significance Tests\n")
        names = list(results.keys())
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                test_result = self.statistical_test(
                    results[names[i]], results[names[j]]
                )
                lines.append(f"### {names[i]} vs {names[j]}")
                lines.append(f"- **Test**: {test_result.test_name}")
                lines.append(f"- **Statistic**: {test_result.statistic:.4f}")
                lines.append(f"- **p-value**: {test_result.p_value:.6f}")
                lines.append(f"- **Significant**: {test_result.significant}")
                lines.append(f"- **Interpretation**: {test_result.interpretation}")
                lines.append("")

        return "\n".join(lines)

    def _generate_html_report(
        self, results: dict[str, SegmenterResult], title: str
    ) -> str:
        """Generate HTML evaluation report with visualizations."""
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        /* ruff: noqa: F821 */
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI',
                Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }}
        h1 {{ color: #1a1a2e; border-bottom: 3px solid #16213e; padding-bottom: 10px; }}
        h2 {{ color: #16213e; margin-top: 30px; }}
        h3 {{ color: #0f3460; }}
        table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 10px; text-align: left; }}
        th {{ background-color: #16213e; color: white; }}
        tr:nth-child(even) {{ background-color: #f2f2f2; }}
        .metric-card {{ display: inline-block; background: #f8f9fa;
            border: 1px solid #dee2e6; border-radius: 8px;
            padding: 15px; margin: 10px; min-width: 180px; }}
        .metric-value {{ font-size: 2em; font-weight: bold; color: #16213e; }}
        .metric-label {{ font-size: 0.9em; color: #6c757d; }}
        .good {{ color: #28a745; }} .bad {{ color: #dc3545; }}
        .warn {{ color: #ffc107; }}
        .error-table {{ font-family: monospace; font-size: 0.9em; }}
        .chart-container {{ margin: 20px 0; }}
        .progress-bar {{ height: 20px; background: #e9ecef;
            border-radius: 10px; overflow: hidden; }}
        .progress-fill {{ height: 100%;
            background: linear-gradient(90deg, #16213e, #0f3460);
            transition: width 0.5s; }}
        .comparison-table {{ font-size: 0.9em; }}
    </style>
</head>
<body>
    <h1>{title}</h1>
"""

        # Summary metric cards
        html += '<div class="metrics-grid">'
        for name, result in results.items():
            r = result.eval_report
            bf1_class = 'good' if r.boundary_f1 > 0.9 else 'warn' if r.boundary_f1 > 0.8 else 'bad'
            wa_class = 'good' if r.word_acc > 0.9 else 'warn' if r.word_acc > 0.8 else 'bad'
            html += f"""
    <div class="metric-card">
        <div class="metric-label">{name} - Boundary F1</div>
        <div class="metric-value {bf1_class}">{r.boundary_f1:.4f}</div>
    </div>
    <div class="metric-card">
        <div class="metric-label">{name} - Word Accuracy</div>
        <div class="metric-value {wa_class}">{r.word_acc:.4f}</div>
    </div>"""
        html += "</div>\n"

        # Summary table
        html += """
    <h2>Summary Metrics</h2>
    <table class="comparison-table">
        <thead>
            <tr>
                <th>Segmenter</th>
                <th>Boundary P</th>
                <th>Boundary R</th>
                <th>Boundary F1</th>
                <th>Syllable Acc</th>
                <th>Word Acc</th>
                <th>Total Words</th>
            </tr>
        </thead>
        <tbody>"""
        for name, result in results.items():
            r = result.eval_report
            html += f"""
            <tr>
                <td><strong>{name}</strong></td>
                <td>{r.boundary_precision:.4f}</td>
                <td>{r.boundary_recall:.4f}</td>
                <td>{r.boundary_f1:.4f}</td>
                <td>{r.syllable_acc:.4f}</td>
                <td>{r.word_acc:.4f}</td>
                <td>{r.total_words}</td>
            </tr>"""
        html += "</tbody></table>\n"

        # Per-syllable-count breakdown with visual bars
        html += """
    <h2>Per-Syllable-Count Breakdown</h2>
    <table class="comparison-table">
        <thead>
            <tr>
                <th>Segmenter</th>
                <th>Syllable Count</th>
                <th>Accuracy</th>
                <th>Visual</th>
                <th>Total</th>
                <th>Correct</th>
            </tr>
        </thead>
        <tbody>"""
        for name, result in results.items():
            for syl_count, stats in result.error_analysis.by_syllable_count.items():
                total = stats.get("total", 0)
                correct = stats.get("correct", 0)
                acc = correct / total if total > 0 else 0
                bar_width = int(acc * 100)
                html += f"""
            <tr>
                <td>{name}</td>
                <td>{syl_count}</td>
                <td>{acc:.4f}</td>
                <td><div class="progress-bar">
                    <div class="progress-fill" style="width: {bar_width}%"></div>
                </div></td>
                <td>{total}</td>
                <td>{correct}</td>
            </tr>"""
        html += "</tbody></table>\n"

        # Error analysis
        html += """
    <h2>Error Analysis</h2>"""
        for name, result in results.items():
            ea = result.error_analysis
            html += f"""
    <h3>{name}</h3>
    <div class="metrics-grid">
        <div class="metric-card">
            <div class="metric-label">Total Words</div>
            <div class="metric-value">{result.eval_report.total_words}</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Correct Syllable Count</div>
            <div class="metric-value good">{ea.correct_syllable_count}</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Total Errors</div>
            <div class="metric-value bad">{ea.total_errors}</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Over-segmentation</div>
            <div class="metric-value warn">{ea.over_segmentation}</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Under-segmentation</div>
            <div class="metric-value warn">{ea.under_segmentation}</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Boundary Shift</div>
            <div class="metric-value warn">{ea.boundary_shift}</div>
        </div>
    </div>"""

            # By frequency
            html += """
    <h4>By Word Frequency</h4>
    <table class="comparison-table">
        <thead>
            <tr>
                <th>Frequency Bucket</th>
                <th>Total</th>
                <th>Correct</th>
                <th>Errors</th>
                <th>Accuracy</th>
            </tr>
        </thead>
        <tbody>"""
            for bucket, stats in result.error_analysis.by_word_frequency.items():
                total = stats.get("total", 0)
                correct = stats.get("correct", 0)
                errors = stats.get("errors", 0)
                acc = correct / total if total > 0 else 0
                html += f"""
        <tr>
            <td>{bucket}</td>
            <td>{total}</td>
            <td>{correct}</td>
            <td>{errors}</td>
            <td>{acc:.4f}</td>
        </tr>"""
            html += "</tbody></table>"

            # Common errors
            if result.error_analysis.common_errors:
                html += """
    <h4>Common Errors (Top 15)</h4>
    <table class="error-table">
        <thead><tr><th>Word</th><th>Gold</th><th>Predicted</th><th>Error Type</th><th>Frequency</th></tr></thead>
        <tbody>"""
                for err in result.error_analysis.common_errors[:15]:
                    html += f"""
        <tr><td>{err['word']}</td><td>{err['gold']}</td><td>{err['predicted']}</td><td>{err['error_type']}</td><td>{err['frequency']}</td></tr>"""
                html += "</tbody></table>"

        # Statistical tests
        html += """
    <h2>Statistical Significance Tests (McNemar's)</h2>"""
        names = list(results.keys())
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                test_result = self.statistical_test(
                    results[names[i]], results[names[j]]
                )
                sig_class = "good" if test_result.significant else "bad"
                html += f"""
    <div class="metric-card">
        <div class="metric-label">{names[i]} vs {names[j]}</div>
        <div class="metric-value {sig_class}">p = {test_result.p_value:.6f}</div>
        <div style="font-size: 0.8em; color: #6c757d;">{test_result.interpretation}</div>
    </div>"""

        html += """
</body>
</html>"""
        return html


# --- SentencePiece Baseline (for comparison) ----------------------------------

class SentencePieceBaseline:
    """SentencePiece baseline segmenter for comparison.

    Uses a pre-trained SentencePiece model if available,
    otherwise falls back to character-level segmentation.
    """

    def __init__(self, model_path: str | None = None) -> None:
        self.model_path = model_path
        self.sp = None
        if model_path and Path(model_path).exists():
            try:
                import sentencepiece as spm
                self.sp = spm.SentencePieceProcessor()
                self.sp.load(model_path)
            except ImportError:
                pass

    def segment(self, word: str) -> list[str]:
        """Segment using SentencePiece or fallback."""
        if self.sp:
            try:
                pieces = self.sp.encode_as_pieces(word)
                # Remove SentencePiece markers
                return [p.replace("▁", "") for p in pieces if p]
            except Exception:
                pass
        # Fallback: character-level
        return list(word)


# --- CLI ----------------------------------------------------------------------


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m zolai.syllable.evaluation",
        description="Comprehensive evaluation framework for syllable segmentation (SylBreak4All M6)",
    )
    parser.add_argument(
        "--test",
        "-t",
        type=str,
        default="/home/peter/Documents/Projects/zolai-ai/data/syllable/splits/gold_test.jsonl",
        help="Path to test data JSONL",
    )
    parser.add_argument(
        "--freq",
        type=str,
        help="Path to word frequency JSONL for frequency-weighted analysis",
    )
    parser.add_argument(
        "--compare-all",
        action="store_true",
        help="Compare rule-based, CRF, and SentencePiece baseline",
    )
    parser.add_argument(
        "--rule-only",
        action="store_true",
        help="Evaluate only rule-based segmenter",
    )
    parser.add_argument(
        "--crf-model",
        type=str,
        help="Path to trained CRF model",
    )
    parser.add_argument(
        "--sp-model",
        type=str,
        help="Path to SentencePiece model for baseline",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="report/evaluation_report",
        help="Output path (without extension)",
    )
    parser.add_argument(
        "--format",
        choices=["html", "markdown", "both"],
        default="both",
        help="Output format",
    )
    parser.add_argument(
        "--stat-test",
        action="store_true",
        help="Run statistical significance tests",
    )
    parser.add_argument(
        "--error-analysis",
        action="store_true",
        help="Show detailed error analysis",
    )
    return parser


def _create_segmenters(args) -> dict[str, Any]:
    """Create segmenters based on CLI args."""
    segmenters = {}

    # Rule-based
    from .segmenter import SyllableSegmenter
    segmenters["Rule-based"] = SyllableSegmenter()

    # CRF
    if args.crf_model or not args.rule_only:
        from .crf_segmenter import CRFSyllableSegmenter
        if args.crf_model:
            segmenters["CRF"] = CRFSyllableSegmenter(model_path=args.crf_model)
        else:
            segmenters["CRF"] = CRFSyllableSegmenter()

    # SentencePiece baseline
    if args.sp_model or not args.rule_only:
        segmenters["SentencePiece"] = SentencePieceBaseline(model_path=args.sp_model)

    return segmenters


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = _build_cli()
    args = parser.parse_args(argv)

    # Load test data
    test_path = Path(args.test)
    if not test_path.exists():
        print(f"Error: Test file not found: {test_path}", file=sys.stderr)
        print("Run gold_dataset.py --build --splits first to generate test splits", file=sys.stderr)
        return 1

    print(f"Loading test data from {test_path}...")
    evaluator = SyllableEvaluator()

    test_data = evaluator._load_test_data(test_path)
    print(f"Loaded {len(test_data)} test examples")

    # Load word frequencies if provided
    word_freqs = evaluator._load_word_frequencies(args.freq) if args.freq else {}
    if word_freqs:
        print(f"Loaded {len(word_freqs)} word frequencies")

    # Create segmenters
    segmenters = _create_segmenters(args)
    for name, seg in segmenters.items():
        evaluator.add_segmenter(name, seg)

    # Run evaluation
    if args.compare_all or len(segmenters) > 1:
        print("\nRunning comparison evaluation...")
        results = evaluator.compare_segmenters(test_data, word_freqs)

        # Print summary
        print("\n" + "=" * 60)
        print("EVALUATION SUMMARY")
        print("=" * 60)
        for name, result in results.items():
            r = result.eval_report
            print(f"\n{name}:")
            print(f"  Boundary Precision: {r.boundary_precision:.4f}")
            print(f"  Boundary Recall:    {r.boundary_recall:.4f}")
            print(f"  Boundary F1:        {r.boundary_f1:.4f}")
            print(f"  Syllable Accuracy:  {r.syllable_acc:.4f}")
            print(f"  Word Accuracy:      {r.word_acc:.4f}")

        # Statistical tests
        if args.stat_test:
            print("\n" + "=" * 60)
            print("STATISTICAL SIGNIFICANCE TESTS")
            print("=" * 60)
            names = list(results.keys())
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    test_result = evaluator.statistical_test(
                        results[names[i]], results[names[j]]
                    )
                    sig = "✓" if test_result.significant else "✗"
                    print(f"\n{names[i]} vs {names[j]}: {sig}")
                    print(f"  χ² = {test_result.statistic:.4f}, p = {test_result.p_value:.6f}")
                    print(f"  {test_result.interpretation}")

        # Error analysis
        if args.error_analysis:
            print("\n" + "=" * 60)
            print("ERROR ANALYSIS")
            print("=" * 60)
            for name, result in results.items():
                ea = result.error_analysis
                print(f"\n{name}:")
                print(f"  Over-segmentation: {ea.over_segmentation}")
                print(f"  Under-segmentation: {ea.under_segmentation}")
                print(f"  Boundary shift: {ea.boundary_shift}")
                print("  By syllable count:")
                for count, stats in ea.by_syllable_count.items():
                    total = stats.get("total", 0)
                    correct = stats.get("correct", 0)
                    print(f"    {count}-syl: {correct}/{total} = {correct/total:.4f}")

        # Generate reports
        output_base = Path(args.output)
        if args.format in ("html", "both"):
            evaluator.generate_report(
                results, output_base.with_suffix(".html"), format="html"
            )
        if args.format in ("markdown", "both"):
            evaluator.generate_report(
                results, output_base.with_suffix(".md"), format="markdown"
            )

    else:
        # Single segmenter evaluation
        name = list(segmenters.keys())[0]
        segmenter = list(segmenters.values())[0]
        print(f"\nEvaluating {name}...")
        result = evaluator.evaluate_single(segmenter, test_data, word_freqs)
        result.name = name

        r = result.eval_report
        print("\n" + "=" * 60)
        print(f"EVALUATION RESULTS: {name}")
        print("=" * 60)
        print(f"Boundary Precision: {r.boundary_precision:.4f}")
        print(f"Boundary Recall:    {r.boundary_recall:.4f}")
        print(f"Boundary F1:        {r.boundary_f1:.4f}")
        print(f"Syllable Accuracy:  {r.syllable_acc:.4f}")
        print(f"Word Accuracy:      {r.word_acc:.4f}")
        print(f"Total Words:        {r.total_words}")

        if args.error_analysis:
            ea = result.error_analysis
            print("\nError Analysis:")
            print(f"  Over-segmentation: {ea.over_segmentation}")
            print(f"  Under-segmentation: {ea.under_segmentation}")
            print(f"  Boundary shift: {ea.boundary_shift}")
            print("  By syllable count:")
            for count, stats in ea.by_syllable_count.items():
                total = stats.get("total", 0)
                correct = stats.get("correct", 0)
                print(f"    {count}-syl: {correct}/{total} = {correct/total:.4f}")

        # Generate report
        output_base = Path(args.output)
        if args.format in ("html", "both"):
            evaluator.generate_report(
                {name: result}, output_base.with_suffix(".html"), format="html"
            )
        if args.format in ("markdown", "both"):
            evaluator.generate_report(
                {name: result}, output_base.with_suffix(".md"), format="markdown"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
