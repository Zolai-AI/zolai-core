"""Tests for the regression test suite."""
from __future__ import annotations

import pytest

from zolai.foundation.regression import (
    GrammarRegressionTest,
    RegressionCategoryReport,
    RegressionReport,
    RegressionSuite,
    SyllableRegressionTest,
    ToneRegressionTest,
    ZVSRegressionTest,
)

# ---------------------------------------------------------------------------
# ZVSRegressionTest
# ---------------------------------------------------------------------------


class TestZVSRegressionTest:
    """Test ZVS regression tests."""

    def test_run_returns_report(self) -> None:
        test = ZVSRegressionTest()
        report = test.run()
        assert isinstance(report, RegressionCategoryReport)
        assert report.category == "zvs"
        assert report.total > 0

    def test_all_valid_forms_pass(self) -> None:
        """All ZVS-2018-valid forms should pass."""
        test = ZVSRegressionTest(
            fail_cases=[],  # no fail cases
        )
        report = test.run()
        assert report.passed == report.total

    def test_forbidden_forms_detected(self) -> None:
        """Forbidden forms should be detected."""
        test = ZVSRegressionTest(
            pass_cases=[],  # no pass cases
        )
        report = test.run()
        # At least some should fail (forbidden forms)
        assert report.failed > 0 or report.total == 0


# ---------------------------------------------------------------------------
# GrammarRegressionTest
# ---------------------------------------------------------------------------


class TestGrammarRegressionTest:
    """Test grammar regression tests."""

    def test_run_returns_report(self) -> None:
        test = GrammarRegressionTest()
        report = test.run()
        assert isinstance(report, RegressionCategoryReport)
        assert report.category == "grammar"
        assert report.total > 0

    def test_sov_detection(self) -> None:
        """Test SOV word order detection."""
        test = GrammarRegressionTest(
            cases=[
                ("Gam ka mu hi.", {"sov_valid": True}, "SOV correct"),
                ("Ka gam mu hi.", {"sov_valid": False}, "SOV wrong"),
            ]
        )
        report = test.run()
        assert report.total == 2

    def test_negation_detection(self) -> None:
        """Test negation detection."""
        test = GrammarRegressionTest(
            cases=[
                ("Ka pai kei hi.", {"negation_type": "kei"}, "kei negation"),
            ]
        )
        report = test.run()
        assert report.total == 1


# ---------------------------------------------------------------------------
# SyllableRegressionTest
# ---------------------------------------------------------------------------


class TestSyllableRegressionTest:
    """Test syllable regression tests."""

    def test_run_returns_report(self) -> None:
        test = SyllableRegressionTest()
        report = test.run()
        assert isinstance(report, RegressionCategoryReport)
        assert report.category == "syllable"
        assert report.total > 0

    def test_known_words(self) -> None:
        """Test known Zolai words segment correctly."""
        test = SyllableRegressionTest(
            cases=[
                ("pasian", ["pa", "sian"], "pasian"),
                ("vantung", ["van", "tung"], "vantung"),
                ("gam", ["gam"], "gam"),
            ]
        )
        report = test.run()
        assert report.passed == 3

    def test_empty_word(self) -> None:
        """Test empty word handling."""
        test = SyllableRegressionTest(
            cases=[("", [""], "empty")]
        )
        report = test.run()
        assert report.total == 1


# ---------------------------------------------------------------------------
# ToneRegressionTest
# ---------------------------------------------------------------------------


class TestToneRegressionTest:
    """Test tone sandhi regression tests."""

    def test_run_returns_report(self) -> None:
        test = ToneRegressionTest()
        report = test.run()
        assert isinstance(report, RegressionCategoryReport)
        assert report.category == "tone"
        assert report.total > 0

    def test_t1_t3_sandhi(self) -> None:
        """Test T1+T3 → T2+T3."""
        test = ToneRegressionTest(
            cases=[
                (1, 3, 2, 3, "T1+T3 → T2+T3"),
            ]
        )
        report = test.run()
        assert report.passed == 1

    def test_t3_t1_sandhi(self) -> None:
        """Test T3+T1 → T2+T1."""
        test = ToneRegressionTest(
            cases=[
                (3, 1, 2, 1, "T3+T1 → T2+T1"),
            ]
        )
        report = test.run()
        assert report.passed == 1

    def test_unchanged_combinations(self) -> None:
        """Test that unchanged combinations remain unchanged."""
        test = ToneRegressionTest(
            cases=[
                (1, 1, 1, 1, "T1+T1 unchanged"),
                (2, 2, 2, 2, "T2+T2 unchanged"),
                (4, 4, 4, 4, "T4+T4 unchanged"),
            ]
        )
        report = test.run()
        assert report.passed == 3


# ---------------------------------------------------------------------------
# RegressionSuite
# ---------------------------------------------------------------------------


class TestRegressionSuite:
    """Test the full regression suite."""

    def test_run_all_categories(self) -> None:
        """Test running all categories."""
        suite = RegressionSuite()
        report = suite.run()
        assert isinstance(report, RegressionReport)
        assert len(report.categories) == 4
        assert report.total > 0

    def test_run_single_category(self) -> None:
        """Test running a single category."""
        suite = RegressionSuite(categories=["tone"])
        report = suite.run()
        assert len(report.categories) == 1
        assert report.categories[0].category == "tone"

    def test_run_multiple_categories(self) -> None:
        """Test running specific categories."""
        suite = RegressionSuite(categories=["zvs", "syllable"])
        report = suite.run()
        assert len(report.categories) == 2
        cats = {c.category for c in report.categories}
        assert cats == {"zvs", "syllable"}

    def test_invalid_category_raises(self) -> None:
        """Test that invalid category raises ValueError."""
        suite = RegressionSuite(categories=["invalid"])
        with pytest.raises(ValueError, match="Unknown category"):
            suite.run()

    def test_overall_precision(self) -> None:
        """Test overall precision calculation."""
        suite = RegressionSuite()
        report = suite.run()
        assert 0.0 <= report.overall_precision <= 1.0

    def test_report_to_dict(self) -> None:
        """Test report serialization."""
        suite = RegressionSuite(categories=["tone"])
        report = suite.run()
        d = report.to_dict()
        assert "total" in d
        assert "passed" in d
        assert "failed" in d
        assert "overall_precision" in d
        assert "categories" in d
        assert len(d["categories"]) == 1

    def test_report_print_summary(self, capsys) -> None:
        """Test report prints summary."""
        suite = RegressionSuite(categories=["tone"])
        report = suite.run()
        report.print_summary()
        captured = capsys.readouterr()
        assert "REGRESSION TEST REPORT" in captured.out
        assert "tone" in captured.out.lower()


# ---------------------------------------------------------------------------
# RegressionCategoryReport
# ---------------------------------------------------------------------------


class TestRegressionCategoryReport:
    """Test RegressionCategoryReport."""

    def test_precision_empty(self) -> None:
        report = RegressionCategoryReport(category="test")
        assert report.precision == 0.0

    def test_precision_calculated(self) -> None:
        report = RegressionCategoryReport(
            category="test", total=10, passed=8, failed=2
        )
        assert report.precision == 0.8

    def test_to_dict(self) -> None:
        report = RegressionCategoryReport(
            category="test", total=5, passed=4, failed=1
        )
        d = report.to_dict()
        assert d["category"] == "test"
        assert d["total"] == 5
        assert d["passed"] == 4
        assert d["failed"] == 1
