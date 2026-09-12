"""End-to-end testing and validation for SylBreak4All pipeline (Milestone 9).

Provides comprehensive testing for the complete NLP pipeline:
- Raw text → syllables → tokens → POS → MT
- Regression test suite against baseline
- Stress test with large corpus
- Memory and performance benchmarking
- ZVS 2018 compliance verification
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
import time
import tracemalloc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..mt import ZolaiMT
from ..pos_tagger import ZolaiPOSTagger
from ..zvs import validate as zvs_validate

# Import pipeline components
from . import SyllableAwarePOS, ZolaiSyllabifier, ZolaiTokenizer


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Result of a single pipeline test case."""
    input_text: str
    syllables: list[str]
    tokens: list[str]
    pos_tags: list[dict]
    translation_zo_en: dict
    translation_en_zo: dict
    zvs_report: Any
    success: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RegressionResult:
    """Result of regression testing."""
    test_name: str
    passed: bool
    expected: Any
    actual: Any
    diff: str | None = None


@dataclass(frozen=True, slots=True)
class StressTestResult:
    """Result of stress testing."""
    total_words: int
    total_syllables: int
    total_time_sec: float
    words_per_sec: float
    syllables_per_sec: float
    peak_memory_mb: float
    avg_memory_mb: float
    errors: int
    error_details: list[dict] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """Result of performance benchmarking."""
    segmenter: dict[str, float]
    tokenizer: dict[str, float]
    pos_tagger: dict[str, float]
    mt: dict[str, float]
    overall: dict[str, float]


@dataclass(frozen=True, slots=True)
class ZVSComplianceResult:
    """Result of ZVS 2018 compliance verification."""
    total_words_tested: int
    violations_found: int
    violation_details: list[dict]
    compliance_rate: float


class E2ETester:
    """End-to-end testing for SylBreak4All pipeline."""

    def __init__(
        self,
        segmenter_mode: str = "rule",
        db_path: str | None = None,
        voter: Any = None,
    ) -> None:
        """Initialize the E2E tester.

        Args:
            segmenter_mode: "rule" or "crf" for syllable segmentation.
            db_path: Path to zolai.db for MT dictionary fallback.
            voter: EnsembleVoter instance for Gemini integration (optional).
        """
        self.segmenter_mode = segmenter_mode
        self.db_path = db_path
        self.voter = voter

        # Initialize pipeline components
        self.tokenizer = ZolaiTokenizer(segmenter_mode=segmenter_mode)
        self.pos_tagger = SyllableAwarePOS(segmenter_mode=segmenter_mode)
        self.mt = ZolaiMT(voter=voter, db_path=db_path)
        self.segmenter = ZolaiSyllabifier(mode=segmenter_mode)
        self.base_pos_tagger = ZolaiPOSTagger()

        # Baseline results for regression testing
        self._baseline_results: dict[str, Any] = {}

    # --- Full Pipeline Test --------------------------------------------------

    def test_pipeline(self, test_cases: list[dict]) -> dict[str, Any]:
        """Test complete NLP pipeline on test cases.

        Args:
            test_cases: List of dicts with keys:
                - "input": Input Zolai text
                - "expected_syllables" (optional): Expected syllable output
                - "expected_pos" (optional): Expected POS tags
                - "expected_translation" (optional): Expected translation

        Returns:
            Dict with test results and summary.
        """
        results: list[PipelineResult] = []
        passed = 0
        failed = 0

        for i, case in enumerate(test_cases):
            input_text = case.get("input", "")
            expected_syllables = case.get("expected_syllables")
            expected_pos = case.get("expected_pos")
            expected_translation = case.get("expected_translation")

            try:
                # Step 1: Syllable segmentation (per word, like tokenizer)
                words = input_text.split()
                syllables = []
                for word in words:
                    syll = self.segmenter.segment(word)
                    # Normalize to lowercase to match tokenizer behavior
                    syllables.extend([s.lower() for s in syll])

                # Step 2: Tokenization
                tokens = self.tokenizer.tokenize(input_text)

                # Step 3: POS tagging
                pos_tags = self.pos_tagger.tag(input_text)

                # Step 4: Translation ZO→EN
                translation_zo_en = self._safe_translate_zo_en(input_text)

                # Step 5: Translation EN→ZO (using English if available)
                en_text = case.get("english", "")
                if en_text:
                    translation_en_zo = self._safe_translate_en_zo(en_text)
                else:
                    translation_en_zo = {"translation": "", "confidence": 0.0, "method": "skipped"}

                # Step 6: ZVS compliance check (unless skipped)
                skip_zvs = case.get("skip_zvs", False)
                zvs_report = zvs_validate(input_text) if not skip_zvs else None

                # Verify against expected
                success = True
                error_parts = []

                if expected_syllables is not None:
                    if syllables != expected_syllables:
                        success = False
                        error_parts.append(f"Syllables mismatch: expected {expected_syllables}, got {syllables}")

                if expected_pos is not None:
                    actual_pos = [item["pos"] for item in pos_tags]
                    if actual_pos != expected_pos:
                        success = False
                        error_parts.append(f"POS mismatch: expected {expected_pos}, got {actual_pos}")

                if expected_translation is not None:
                    actual_trans = translation_zo_en["translation"]
                    if actual_trans != expected_translation:
                        success = False
                        error_parts.append(
                            f"Translation mismatch: expected {expected_translation}, got {actual_trans}"
                        )

                if zvs_report is not None and not zvs_report.is_valid:
                    success = False
                    error_parts.append(f"ZVS violations: {[v.forbidden for v in zvs_report.violations]}")

                result = PipelineResult(
                    input_text=input_text,
                    syllables=syllables,
                    tokens=tokens,
                    pos_tags=pos_tags,
                    translation_zo_en=translation_zo_en,
                    translation_en_zo=translation_en_zo,
                    zvs_report=zvs_report,
                    success=success,
                    error="; ".join(error_parts) if error_parts else None,
                )

                if success:
                    passed += 1
                else:
                    failed += 1

                results.append(result)

            except Exception as e:
                failed += 1
                results.append(PipelineResult(
                    input_text=input_text,
                    syllables=[],
                    tokens=[],
                    pos_tags=[],
                    translation_zo_en={},
                    translation_en_zo={},
                    zvs_report=None,
                    success=False,
                    error=str(e),
                ))

        return {
            "total": len(test_cases),
            "passed": passed,
            "failed": failed,
            "success_rate": passed / len(test_cases) if test_cases else 0,
            "results": results,
        }

    def _safe_translate_zo_en(self, text: str) -> dict:
        """Safely translate Zolai to English."""
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(self.mt.translate_zo_en(text))
            loop.close()
            return result
        except Exception as e:
            return {"translation": "", "confidence": 0.0, "method": f"error: {e}"}

    def _safe_translate_en_zo(self, text: str) -> dict:
        """Safely translate English to Zolai."""
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(self.mt.translate_en_zo(text))
            loop.close()
            return result
        except Exception as e:
            return {"translation": "", "confidence": 0.0, "method": f"error: {e}"}

    # --- Regression Testing --------------------------------------------------

    def test_regression(self, baseline_results: dict | str) -> dict[str, Any]:
        """Run regression tests against baseline results.

        Args:
            baseline_results: Either a dict of baseline results or path to JSON file.

        Returns:
            Dict with regression test results.
        """
        if isinstance(baseline_results, str):
            with open(baseline_results, "r", encoding="utf-8") as f:
                baseline = json.load(f)
        else:
            baseline = baseline_results

        regression_results: list[RegressionResult] = []
        passed = 0
        failed = 0

        for test_name, expected in baseline.items():
            if test_name == "metadata":
                continue

            try:
                # Run the test case
                result = self.test_pipeline([expected])
                actual = result["results"][0]

                # Compare key metrics
                checks = []
                if "syllables" in expected:
                    checks.append(("syllables", expected["syllables"], actual.syllables))
                if "pos" in expected:
                    actual_pos = [item["pos"] for item in actual.pos_tags]
                    checks.append(("pos", expected["pos"], actual_pos))
                if "translation" in expected:
                    checks.append(("translation", expected["translation"], actual.translation_zo_en["translation"]))

                all_passed = True
                diff_parts = []
                for check_name, exp_val, act_val in checks:
                    if exp_val != act_val:
                        all_passed = False
                        diff_parts.append(f"{check_name}: expected {exp_val}, got {act_val}")

                reg_result = RegressionResult(
                    test_name=test_name,
                    passed=all_passed,
                    expected=expected,
                    actual=actual.__dict__ if hasattr(actual, '__dict__') else str(actual),
                    diff="; ".join(diff_parts) if diff_parts else None,
                )

                if all_passed:
                    passed += 1
                else:
                    failed += 1

                regression_results.append(reg_result)

            except Exception as e:
                failed += 1
                regression_results.append(RegressionResult(
                    test_name=test_name,
                    passed=False,
                    expected=expected,
                    actual=str(e),
                    diff=f"Exception: {e}",
                ))

        return {
            "total": len(regression_results),
            "passed": passed,
            "failed": failed,
            "success_rate": passed / len(regression_results) if regression_results else 0,
            "results": regression_results,
        }

    def save_baseline(self, test_cases: list[dict], output_path: str) -> None:
        """Save current results as baseline for future regression testing."""
        results = self.test_pipeline(test_cases)
        baseline = {"metadata": {"segmenter_mode": self.segmenter_mode, "timestamp": time.time()}}

        for i, (case, result) in enumerate(zip(test_cases, results["results"])):
            if result.success:
                baseline[f"test_{i}"] = {
                    "input": case["input"],
                    "syllables": result.syllables,
                    "pos": [item["pos"] for item in result.pos_tags],
                    "translation": result.translation_zo_en["translation"],
                }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(baseline, f, ensure_ascii=False, indent=2)

    # --- Stress Testing ------------------------------------------------------

    def stress_test(
        self,
        corpus_path: str,
        max_words: int = 100000,
        sample_mode: str = "sequential",
    ) -> StressTestResult:
        """Stress test with large corpus.

        Args:
            corpus_path: Path to corpus JSONL file with "text" or "word" field.
            max_words: Maximum number of words to process.
            sample_mode: "sequential" or "random" sampling.

        Returns:
            StressTestResult with performance metrics.
        """
        # Load corpus words
        words = self._load_corpus_words(corpus_path, max_words, sample_mode)

        if not words:
            return StressTestResult(
                total_words=0,
                total_syllables=0,
                total_time_sec=0,
                words_per_sec=0,
                syllables_per_sec=0,
                peak_memory_mb=0,
                avg_memory_mb=0,
                errors=0,
                error_details=[{"error": "No words loaded from corpus"}],
            )

        # Start memory tracking
        tracemalloc.start()
        gc.collect()

        start_time = time.perf_counter()
        total_syllables = 0
        errors = 0
        error_details = []
        memory_samples = []

        # Process words in batches for memory sampling
        batch_size = 1000
        for i in range(0, len(words), batch_size):
            batch = words[i:i + batch_size]
            batch_start = time.perf_counter()

            for word in batch:
                try:
                    sylls = self.segmenter.segment(word)
                    total_syllables += len(sylls)
                except Exception as e:
                    errors += 1
                    if len(error_details) < 10:
                        error_details.append({"word": word, "error": str(e)})

            batch_time = time.perf_counter() - batch_start
            if batch_time > 0:
                current, peak = tracemalloc.get_traced_memory()
                memory_samples.append(peak / 1024 / 1024)

        total_time = time.perf_counter() - start_time
        current_mem, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        avg_mem = sum(memory_samples) / len(memory_samples) if memory_samples else peak_mem / 1024 / 1024

        return StressTestResult(
            total_words=len(words),
            total_syllables=total_syllables,
            total_time_sec=total_time,
            words_per_sec=len(words) / total_time if total_time > 0 else 0,
            syllables_per_sec=total_syllables / total_time if total_time > 0 else 0,
            peak_memory_mb=peak_mem / 1024 / 1024,
            avg_memory_mb=avg_mem,
            errors=errors,
            error_details=error_details,
        )

    def _load_corpus_words(
        self, corpus_path: str, max_words: int, sample_mode: str
    ) -> list[str]:
        """Load words from corpus file."""
        words = []
        path = Path(corpus_path)

        if not path.exists():
            return words

        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    # Try multiple field names for Zolai text
                    text = (
                        data.get("text")
                        or data.get("word")
                        or data.get("zo")
                        or data.get("zo_tdb77")
                        or data.get("zo_tedim2010")
                        or ""
                    )
                    if text:
                        # Split into words
                        for word in text.split():
                            clean = word.strip(".,!?;:()[]{}\"'").lower()
                            if clean and clean.isalpha():
                                words.append(clean)
                                if len(words) >= max_words:
                                    break
                except json.JSONDecodeError:
                    continue
                if len(words) >= max_words:
                    break

        if sample_mode == "random" and len(words) > max_words:
            import random
            random.shuffle(words)
            words = words[:max_words]

        return words

    # --- Performance Benchmarking --------------------------------------------

    def benchmark_performance(self, num_iterations: int = 1000) -> BenchmarkResult:
        """Benchmark segmenter, tokenizer, POS tagger, and MT.

        Args:
            num_iterations: Number of iterations for each benchmark.

        Returns:
            BenchmarkResult with timing metrics.
        """
        test_texts = [
            "Pasian in vantung leh leitung a piangsak hi",
            "Ka nungzuiah a om hi",
            "Tapa in laisiangtho a zoh hi",
            "Vantung ah Pasian a om hi",
            "Leitung a bawl hi",
            "Ka mu khin hi",
            "Amaute pai kei hi",
            "Bang hang na pai hiam",
        ]

        # Benchmark segmenter
        segmenter_times = []
        for _ in range(num_iterations):
            for text in test_texts:
                for word in text.split():
                    start = time.perf_counter()
                    self.segmenter.segment(word)
                    segmenter_times.append(time.perf_counter() - start)

        # Benchmark tokenizer
        tokenizer_times = []
        for _ in range(num_iterations):
            for text in test_texts:
                start = time.perf_counter()
                self.tokenizer.tokenize(text)
                tokenizer_times.append(time.perf_counter() - start)

        # Benchmark POS tagger
        pos_times = []
        for _ in range(num_iterations):
            for text in test_texts:
                start = time.perf_counter()
                self.pos_tagger.tag(text)
                pos_times.append(time.perf_counter() - start)

        # Benchmark MT (dictionary only, no async)
        mt_times = []
        for _ in range(num_iterations // 10):  # Fewer iterations for MT
            for text in test_texts[:3]:
                start = time.perf_counter()
                self.mt.translate_dict(text, direction="zo_en")
                mt_times.append(time.perf_counter() - start)

        def stats(times: list[float]) -> dict[str, float]:
            if not times:
                return {"mean": 0, "median": 0, "min": 0, "max": 0, "p95": 0, "p99": 0}
            sorted_times = sorted(times)
            n = len(sorted_times)
            return {
                "mean": sum(times) / n,
                "median": sorted_times[n // 2],
                "min": sorted_times[0],
                "max": sorted_times[-1],
                "p95": sorted_times[int(n * 0.95)],
                "p99": sorted_times[int(n * 0.99)],
            }

        return BenchmarkResult(
            segmenter=stats(segmenter_times),
            tokenizer=stats(tokenizer_times),
            pos_tagger=stats(pos_times),
            mt=stats(mt_times),
            overall={
                "total_operations": len(segmenter_times) + len(tokenizer_times) + len(pos_times) + len(mt_times),
                "total_time_sec": sum(segmenter_times) + sum(tokenizer_times) + sum(pos_times) + sum(mt_times),
            },
        )

    # --- ZVS Compliance Verification -----------------------------------------

    def verify_zvs_compliance(
        self,
        corpus_path: str,
        max_words: int = 10000,
        sample_mode: str = "sequential",
    ) -> ZVSComplianceResult:
        """Verify ZVS 2018 compliance on corpus.

        Args:
            corpus_path: Path to corpus JSONL file.
            max_words: Maximum words to test.
            sample_mode: "sequential" or "random".

        Returns:
            ZVSComplianceResult with violation details.
        """
        words = self._load_corpus_words(corpus_path, max_words, sample_mode)

        violations_found = 0
        violation_details = []

        for word in words:
            report = zvs_validate(word)
            if not report.is_valid:
                violations_found += 1
                for v in report.violations:
                    violation_details.append({
                        "word": word,
                        "forbidden": v.forbidden,
                        "preferred": v.preferred,
                        "rule_id": v.rule_id,
                        "context": v.context,
                    })

        compliance_rate = (len(words) - violations_found) / len(words) if words else 1.0

        return ZVSComplianceResult(
            total_words_tested=len(words),
            violations_found=violations_found,
            violation_details=violation_details,
            compliance_rate=compliance_rate,
        )

    # --- Report Generation ---------------------------------------------------

    def generate_report(
        self,
        results: dict[str, Any],
        output_path: str,
        include_details: bool = True,
    ) -> None:
        """Generate comprehensive test report.

        Args:
            results: Results dict from any test method.
            output_path: Output file path (JSON).
            include_details: Whether to include detailed per-test results.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Convert dataclasses to serializable dicts
        def serialize(obj: Any) -> Any:
            if hasattr(obj, "__dict__"):
                return {k: serialize(v) for k, v in obj.__dict__.items()}
            elif isinstance(obj, list):
                return [serialize(item) for item in obj]
            elif isinstance(obj, dict):
                return {k: serialize(v) for k, v in obj.items()}
            elif hasattr(obj, "__dataclass_fields__"):
                return {f: serialize(getattr(obj, f)) for f in obj.__dataclass_fields__}
            else:
                return obj

        serializable = serialize(results)

        with output_path.open("w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)

        print(f"Report written to {output_path}")

    def generate_html_report(
        self,
        results: dict[str, Any],
        output_path: str,
        title: str = "SylBreak4All E2E Test Report",
    ) -> None:
        """Generate HTML report with visualizations."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        html = self._build_html_report(results, title)
        output_path.write_text(html, encoding="utf-8")
        print(f"HTML report written to {output_path}")

    def _build_html_report(self, results: dict[str, Any], title: str) -> str:
        """Build HTML report from results."""
        # This is a simplified HTML generator
        # In practice, you'd use a template engine
        lines = [
            "<!DOCTYPE html>",
            f"<html><head><title>{title}</title>",
            "<style>",
            "body { font-family: sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }",
            "h1 { color: #1a1a2e; border-bottom: 2px solid #16213e; padding-bottom: 10px; }",
            "h2 { color: #16213e; margin-top: 30px; }",
            "table { border-collapse: collapse; width: 100%; margin: 15px 0; }",
            "th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }",
            "th { background: #16213e; color: white; }",
            "tr:nth-child(even) { background: #f2f2f2; }",
            ".metric { display: inline-block; background: #f8f9fa; border: 1px solid #dee2e6;",
            "  border-radius: 8px; padding: 15px; margin: 10px; min-width: 150px; }",
            ".metric-value { font-size: 1.5em; font-weight: bold; color: #16213e; }",
            ".metric-label { font-size: 0.85em; color: #6c757d; }",
            ".pass { color: #28a745; } .fail { color: #dc3545; }",
            ".warn { color: #ffc107; }",
            "</style></head><body>",
            f"<h1>{title}</h1>",
        ]

        # Summary metrics
        if "total" in results:
            lines.append('<div class="metrics">')
            total_tests = results["total"]
            passed_tests = results.get("passed", 0)
            failed_tests = results.get("failed", 0)
            success_rate = results.get("success_rate", 0)
            lines.append(
                f'<div class="metric"><div class="metric-label">Total Tests</div>'
                f'<div class="metric-value">{total_tests}</div></div>'
            )
            lines.append(
                f'<div class="metric"><div class="metric-label">Passed</div>'
                f'<div class="metric-value pass">{passed_tests}</div></div>'
            )
            lines.append(
                f'<div class="metric"><div class="metric-label">Failed</div>'
                f'<div class="metric-value fail">{failed_tests}</div></div>'
            )
            lines.append(
                f'<div class="metric"><div class="metric-label">Success Rate</div>'
                f'<div class="metric-value">{success_rate:.1%}</div></div>'
            )
            lines.append("</div>")

        # Detailed results table
        if "results" in results and isinstance(results["results"], list):
            lines.append("<h2>Detailed Results</h2>")
            lines.append("<table>")
            lines.append("<tr><th>Test</th><th>Status</th><th>Details</th></tr>")
            for i, r in enumerate(results["results"]):
                status_class = "pass" if getattr(r, "success", getattr(r, "passed", False)) else "fail"
                status_text = "PASS" if status_class == "pass" else "FAIL"
                details = getattr(r, "error", getattr(r, "diff", "")) or "OK"
                row = f"<tr><td>Test {i}</td><td class='{status_class}'>{status_text}</td><td>{details}</td></tr>"
                lines.append(row)
            lines.append("</table>")

        lines.append("</body></html>")
        return "\n".join(lines)


# --- Default Test Cases ------------------------------------------------------

DEFAULT_TEST_CASES = [
    {
        "input": "Pasian in vantung leh leitung a piangsak hi",
        "english": "God created the heaven and earth",
        "expected_syllables": ["pa", "sian", "in", "van", "tung", "leh", "lei", "tung", "a", "piang", "sak", "hi"],
        "expected_pos": ["N.PROPER", "PART.ERG", "N.PROPER", "PART", "N.PROPER", "PRON", "VERB", "PART"],
    },
    {
        "input": "Ka nungzuiah a om hi",
        "english": "My mind is peaceful",
        "expected_syllables": ["ka", "nung", "zui", "ah", "a", "om", "hi"],
        "expected_pos": ["PRON", "VERB", "PRON", "NOUN", "PART"],
    },
    {
        "input": "Tapa in laisiangtho a zoh hi",
        "english": "The son reads the Bible",
        "expected_syllables": ["ta", "pa", "in", "lai", "siang", "tho", "a", "zoh", "hi"],
        "expected_pos": ["N.PROPER", "PART.ERG", "NOUN", "PRON", "NOUN", "PART"],
    },
    {
        "input": "Vantung ah Pasian a om hi",
        "english": "In heaven God exists",
        "expected_syllables": ["van", "tung", "ah", "pa", "sian", "a", "om", "hi"],
    },
    {
        "input": "Leitung a bawl hi",
        "english": "The earth is made",
        "expected_syllables": ["lei", "tung", "a", "bawl", "hi"],
    },
    {
        "input": "Ka mu khin hi",
        "english": "I have seen",
        "expected_syllables": ["ka", "mu", "khin", "hi"],
    },
    {
        "input": "Amaute pai kei hi",
        "english": "They do not go",
        "expected_syllables": ["a", "ma", "u", "te", "pai", "kei", "hi"],
    },
    {
        "input": "Bang hang na pai hiam",
        "english": "Why do you go?",
        "expected_syllables": ["bang", "hang", "na", "pai", "hiam"],
    },
    {
        "input": "Na dam hiam",
        "english": "Are you well?",
        "expected_syllables": ["na", "dam", "hiam"],
    },
    {
        "input": "Kumpipa in a suah hi",
        "english": "The Savior saves",
        "expected_syllables": ["kum", "pi", "pa", "in", "a", "suah", "hi"],
        "skip_zvs": True,  # 'suah' as verb 'to save' is valid; ZVS flags noun form
    },
    # Edge cases
    {
        "input": "A",
        "expected_syllables": ["a"],
    },
    {
        "input": "Hi",
        "expected_syllables": ["hi"],
    },
    {
        "input": "Pasian",
        "expected_syllables": ["pa", "sian"],
    },
    {
        "input": "Vantung",
        "expected_syllables": ["van", "tung"],
    },
    {
        "input": "Laisiangtho",
        "expected_syllables": ["lai", "siang", "tho"],
    },
]


# --- CLI ---------------------------------------------------------------------


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m zolai.syllable.e2e_test",
        description="End-to-end testing and validation for SylBreak4All (M9)",
    )
    parser.add_argument(
        "--pipeline",
        "-p",
        action="store_true",
        help="Run full pipeline tests",
    )
    parser.add_argument(
        "--regression",
        "-r",
        type=str,
        help="Run regression tests against baseline JSON file",
    )
    parser.add_argument(
        "--stress",
        "-s",
        type=str,
        help="Run stress test with corpus JSONL file",
    )
    parser.add_argument(
        "--max-words",
        type=int,
        default=100000,
        help="Max words for stress test",
    )
    parser.add_argument(
        "--benchmark",
        "-b",
        action="store_true",
        help="Run performance benchmarks",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=1000,
        help="Number of iterations for benchmark",
    )
    parser.add_argument(
        "--zvs",
        type=str,
        help="Run ZVS compliance check on corpus JSONL file",
    )
    parser.add_argument(
        "--max-zvs-words",
        type=int,
        default=10000,
        help="Max words for ZVS compliance check",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all tests (pipeline, benchmark, stress, zvs)",
    )
    parser.add_argument(
        "--mode",
        choices=["rule", "crf"],
        default="rule",
        help="Segmenter mode",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        help="Path to zolai.db for MT",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="report/e2e/",
        help="Output directory for reports",
    )
    parser.add_argument(
        "--save-baseline",
        type=str,
        help="Save baseline results to JSON file",
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="Generate HTML report",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = _build_cli()
    args = parser.parse_args(argv)

    # Initialize tester
    tester = E2ETester(
        segmenter_mode=args.mode,
        db_path=args.db_path,
        voter=None,  # No voter for testing
    )

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_results = {}

    # Save baseline if requested
    if args.save_baseline:
        print(f"Saving baseline to {args.save_baseline}...")
        tester.save_baseline(DEFAULT_TEST_CASES, args.save_baseline)
        return 0

    # Run pipeline tests
    if args.pipeline or args.all:
        print("\n" + "=" * 60)
        print("RUNNING PIPELINE TESTS")
        print("=" * 60)
        pipeline_results = tester.test_pipeline(DEFAULT_TEST_CASES)
        all_results["pipeline"] = pipeline_results

        print(f"\nTotal: {pipeline_results['total']}")
        print(f"Passed: {pipeline_results['passed']}")
        print(f"Failed: {pipeline_results['failed']}")
        print(f"Success Rate: {pipeline_results['success_rate']:.1%}")

        for i, result in enumerate(pipeline_results["results"]):
            status = "✓" if result.success else "✗"
            print(f"  {status} Test {i}: {result.input_text[:50]}...")
            if not result.success:
                print(f"    Error: {result.error}")

    # Run regression tests
    if args.regression:
        print("\n" + "=" * 60)
        print("RUNNING REGRESSION TESTS")
        print("=" * 60)
        regression_results = tester.test_regression(args.regression)
        all_results["regression"] = regression_results

        print(f"\nTotal: {regression_results['total']}")
        print(f"Passed: {regression_results['passed']}")
        print(f"Failed: {regression_results['failed']}")
        print(f"Success Rate: {regression_results['success_rate']:.1%}")

        for result in regression_results["results"]:
            status = "✓" if result.passed else "✗"
            print(f"  {status} {result.test_name}")
            if not result.passed and result.diff:
                print(f"    Diff: {result.diff}")

    # Run stress test
    if args.stress or args.all:
        print("\n" + "=" * 60)
        print("RUNNING STRESS TEST")
        print("=" * 60)
        stress_results = tester.stress_test(
            args.stress or "/home/peter/Documents/Projects/zolai-ai/data/bible/parallel_corpus_v1.jsonl",
            max_words=args.max_words,
        )
        all_results["stress"] = stress_results

        print(f"\nTotal words processed: {stress_results.total_words:,}")
        print(f"Total syllables: {stress_results.total_syllables:,}")
        print(f"Total time: {stress_results.total_time_sec:.2f}s")
        print(f"Words/sec: {stress_results.words_per_sec:,.0f}")
        print(f"Syllables/sec: {stress_results.syllables_per_sec:,.0f}")
        print(f"Peak memory: {stress_results.peak_memory_mb:.1f} MB")
        print(f"Avg memory: {stress_results.avg_memory_mb:.1f} MB")
        print(f"Errors: {stress_results.errors}")

    # Run benchmarks
    if args.benchmark or args.all:
        print("\n" + "=" * 60)
        print("RUNNING PERFORMANCE BENCHMARKS")
        print("=" * 60)
        benchmark_results = tester.benchmark_performance(num_iterations=args.iterations)
        all_results["benchmark"] = benchmark_results

        def print_stats(name: str, stats: dict[str, float]) -> None:
            print(f"\n{name}:")
            print(f"  Mean: {stats['mean']*1000:.3f}ms")
            print(f"  Median: {stats['median']*1000:.3f}ms")
            print(f"  P95: {stats['p95']*1000:.3f}ms")
            print(f"  P99: {stats['p99']*1000:.3f}ms")
            print(f"  Min: {stats['min']*1000:.3f}ms")
            print(f"  Max: {stats['max']*1000:.3f}ms")

        print_stats("Segmenter", benchmark_results.segmenter)
        print_stats("Tokenizer", benchmark_results.tokenizer)
        print_stats("POS Tagger", benchmark_results.pos_tagger)
        print_stats("MT (dict)", benchmark_results.mt)
        total_ops = benchmark_results.overall["total_operations"]
        total_time = benchmark_results.overall["total_time_sec"]
        print(f"\nOverall: {total_ops} ops in {total_time:.2f}s")

    # Run ZVS compliance
    if args.zvs or args.all:
        print("\n" + "=" * 60)
        print("RUNNING ZVS 2018 COMPLIANCE CHECK")
        print("=" * 60)
        zvs_results = tester.verify_zvs_compliance(
            args.zvs or "/home/peter/Documents/Projects/zolai-ai/data/bible/parallel_corpus_v1.jsonl",
            max_words=args.max_zvs_words,
        )
        all_results["zvs"] = zvs_results

        print(f"\nTotal words tested: {zvs_results.total_words_tested:,}")
        print(f"Violations found: {zvs_results.violations_found:,}")
        print(f"Compliance rate: {zvs_results.compliance_rate:.2%}")

        if zvs_results.violation_details:
            print("\nTop violations:")
            for v in zvs_results.violation_details[:10]:
                print(f"  {v['word']}: '{v['forbidden']}' → '{v['preferred']}' ({v['rule_id']})")

    # Generate reports
    if all_results:
        json_path = output_dir / "e2e_results.json"
        tester.generate_report(all_results, str(json_path))
        print(f"\nJSON report: {json_path}")

        if args.html:
            html_path = output_dir / "e2e_report.html"
            tester.generate_html_report(all_results, str(html_path))

    # Determine exit code based on test results that have pass/fail
    exit_code = 0
    for key, result in all_results.items():
        if isinstance(result, dict) and "failed" in result:
            if result.get("failed", 0) > 0:
                exit_code = 1
                break
        elif hasattr(result, "failed"):
            # For dataclass results with failed attribute
            if getattr(result, "failed", 0) > 0:
                exit_code = 1
                break

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
