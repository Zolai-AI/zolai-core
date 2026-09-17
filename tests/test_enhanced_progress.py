"""Tests for Enhanced Progress Tracker with CEFR-aligned adaptive difficulty."""
from __future__ import annotations

import pytest

from zolai.learning.progress import ProgressTracker, _CEFR_THRESHOLDS, _FREQ_TIERS


class TestProgressTracker:
    """Test ProgressTracker with morphology-aware difficulty and adaptive quiz generation."""

    @pytest.fixture(scope="class")
    def tracker(self) -> ProgressTracker:
        return ProgressTracker(user_id="test_user")

    def test_compute_morphology_complexity(self, tracker: ProgressTracker) -> None:
        """Test morphology complexity computation."""
        complexity = tracker._compute_morphology_complexity("pasian")
        assert isinstance(complexity, float)
        assert 0.0 <= complexity <= 1.0

    def test_compute_morphology_complexity_compound(self, tracker: ProgressTracker) -> None:
        """Test complexity of compound word."""
        complexity = tracker._compute_morphology_complexity("vantung")
        assert isinstance(complexity, float)
        assert 0.0 <= complexity <= 1.0

    def test_apply_sm2_tuning_base(self, tracker: ProgressTracker) -> None:
        """Test SM-2 tuning with no adjustments."""
        ease = tracker._apply_sm2_tuning(2.5, morphology_score=0.1, tone_sensitive=False)
        assert ease == 2.5

    def test_apply_sm2_tuning_tone_sensitive(self, tracker: ProgressTracker) -> None:
        """Test SM-2 tuning with tone sensitivity."""
        ease = tracker._apply_sm2_tuning(2.5, morphology_score=0.1, tone_sensitive=True)
        assert ease == 2.6  # +0.1

    def test_apply_sm2_tuning_complex_morphology(self, tracker: ProgressTracker) -> None:
        """Test SM-2 tuning with complex morphology."""
        ease = tracker._apply_sm2_tuning(2.5, morphology_score=0.6, tone_sensitive=False)
        assert ease == 2.65  # +0.15

    def test_apply_sm2_tuning_moderate_morphology(self, tracker: ProgressTracker) -> None:
        """Test SM-2 tuning with moderate morphology."""
        ease = tracker._apply_sm2_tuning(2.5, morphology_score=0.35, tone_sensitive=False)
        assert ease == 2.58  # +0.08

    def test_apply_sm2_tuning_clamp_min(self, tracker: ProgressTracker) -> None:
        """Test SM-2 tuning clamps to minimum."""
        ease = tracker._apply_sm2_tuning(1.2, morphology_score=0.0, tone_sensitive=False)
        assert ease >= 1.3

    def test_apply_sm2_tuning_clamp_max(self, tracker: ProgressTracker) -> None:
        """Test SM-2 tuning clamps to maximum."""
        ease = tracker._apply_sm2_tuning(3.0, morphology_score=0.8, tone_sensitive=True)
        assert ease <= 3.0

    def test_get_cefr_level_morphology_simple(self, tracker: ProgressTracker) -> None:
        """Test CEFR level for simple word."""
        level = tracker._get_cefr_level_morphology("pasian")
        assert level in ("A1", "A2", "B1", "B2", "C1", "C2")

    def test_get_cefr_level_morphology_compound(self, tracker: ProgressTracker) -> None:
        """Test CEFR level for compound word."""
        level = tracker._get_cefr_level_morphology("vantung")
        assert level in ("A1", "A2", "B1", "B2", "C1", "C2")

    def test_get_adaptive_difficulty(self, tracker: ProgressTracker) -> None:
        """Test adaptive difficulty computation."""
        result = tracker.get_adaptive_difficulty(user_id="test_user")
        assert isinstance(result, dict)
        assert "difficulty" in result
        assert "frequency_tier" in result
        assert "morphology_threshold" in result
        assert "include_tone_questions" in result
        assert "recommended_count" in result
        assert "reason" in result

    def test_get_adaptive_difficulty_defaults(self, tracker: ProgressTracker) -> None:
        """Test adaptive difficulty with no history returns beginner."""
        result = tracker.get_adaptive_difficulty(user_id="nonexistent_user_xyz")
        assert result["difficulty"] == "beginner"
        assert result["frequency_tier"] == "high"

    def test_cefr_thresholds_coverage(self) -> None:
        """Test CEFR thresholds cover all levels."""
        expected_levels = {"A1", "A2", "B1", "B2", "C1", "C2"}
        assert expected_levels == set(_CEFR_THRESHOLDS.keys())

    def test_freq_tiers_coverage(self) -> None:
        """Test frequency tiers are defined."""
        expected_tiers = {"high", "medium", "low"}
        assert expected_tiers == set(_FREQ_TIERS.keys())

    def test_get_cefr_level(self, tracker: ProgressTracker) -> None:
        """Test CEFR level computation."""
        result = tracker.get_cefr_level()
        assert isinstance(result, dict)
        assert "level" in result
        assert "words_known" in result
        assert "words_total" in result
        assert "progress" in result
        assert result["level"] in ("A1", "A2", "B1", "B2", "C1", "C2")

    def test_get_statistics(self, tracker: ProgressTracker) -> None:
        """Test learning statistics."""
        stats = tracker.get_statistics()
        assert isinstance(stats, dict)
        assert "total_vocab" in stats
        assert "total_grammar" in stats
        assert "total_exercises" in stats
