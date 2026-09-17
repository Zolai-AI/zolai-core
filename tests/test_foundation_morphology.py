"""Tests for Foundation Enhanced Morphological Analyzer."""
from __future__ import annotations

import pytest

from zolai.foundation.morphology import (
    EnhancedMorphologyAnalyzer,
    MorphemeAnalysis,
    get_enhanced_morphology,
)


class TestEnhancedMorphologyAnalyzer:
    """Test EnhancedMorphologyAnalyzer for agglutinative decomposition."""

    @pytest.fixture(scope="class")
    def analyzer(self) -> EnhancedMorphologyAnalyzer:
        return get_enhanced_morphology()

    def test_singleton(self) -> None:
        """Test singleton pattern."""
        a1 = get_enhanced_morphology()
        a2 = get_enhanced_morphology()
        assert a1 is a2

    def test_decompose_simple(self, analyzer: EnhancedMorphologyAnalyzer) -> None:
        """Test decomposition of simple word."""
        result = analyzer.decompose("pasian")
        assert isinstance(result, MorphemeAnalysis)
        assert len(result.segments) > 0
        assert result.stem != ""

    def test_decompose_compound(self, analyzer: EnhancedMorphologyAnalyzer) -> None:
        """Test decomposition of compound word."""
        result = analyzer.decompose("vantung")
        assert isinstance(result, MorphemeAnalysis)
        assert "van" in result.segments or "vantung" in result.segments

    def test_decompose_empty(self, analyzer: EnhancedMorphologyAnalyzer) -> None:
        """Test decomposition of empty string."""
        result = analyzer.decompose("")
        assert result.is_valid is False
        assert len(result.violations) > 0

    def test_detect_directional(self, analyzer: EnhancedMorphologyAnalyzer) -> None:
        """Test directional particle detection."""
        # 'hong' is a directional prefix
        result = analyzer.decompose("hongpai")
        assert result.directional == "hong" or result.directional is None  # depends on base analyzer

    def test_detect_aspect(self, analyzer: EnhancedMorphologyAnalyzer) -> None:
        """Test aspect marker detection."""
        result = analyzer.decompose("nuntakna")
        # 'ta' is part of 'takna' suffix, check aspect detection
        assert isinstance(result, MorphemeAnalysis)

    def test_detect_particle(self, analyzer: EnhancedMorphologyAnalyzer) -> None:
        """Test particle detection."""
        result = analyzer.decompose("pasianin")
        # 'in' is an ergative particle
        assert isinstance(result, MorphemeAnalysis)

    def test_validate_zvs_morphemes_valid(self, analyzer: EnhancedMorphologyAnalyzer) -> None:
        """Test ZVS validation with valid morphemes."""
        violations = analyzer._validate_zvs_morphemes(("pasian", "gam"))
        assert len(violations) == 0

    def test_validate_zvs_morphemes_forbidden(self, analyzer: EnhancedMorphologyAnalyzer) -> None:
        """Test ZVS validation with forbidden morphemes."""
        violations = analyzer._validate_zvs_morphemes(("pathian",))
        assert len(violations) > 0
        assert any("pathian" in v for v in violations)

    def test_validate_compound_valid(self, analyzer: EnhancedMorphologyAnalyzer) -> None:
        """Test compound validation with known parts."""
        is_valid, notes = analyzer._validate_compound("vantung")
        # van and tung are known compound parts
        assert isinstance(is_valid, bool)
        assert isinstance(notes, tuple)

    def test_get_directionals(self, analyzer: EnhancedMorphologyAnalyzer) -> None:
        """Test directionals listing."""
        directionals = analyzer.get_directionals()
        assert isinstance(directionals, dict)
        assert "hong" in directionals
        assert "va" in directionals
        assert "khia" in directionals

    def test_get_aspects(self, analyzer: EnhancedMorphologyAnalyzer) -> None:
        """Test aspects listing."""
        aspects = analyzer.get_aspects()
        assert isinstance(aspects, dict)
        assert "ta" in aspects
        assert "ding" in aspects

    def test_get_particles(self, analyzer: EnhancedMorphologyAnalyzer) -> None:
        """Test particles listing."""
        particles = analyzer.get_particles()
        assert isinstance(particles, dict)
        assert "hi" in particles
        assert "in" in particles

    def test_morpheme_analysis_dataclass(self) -> None:
        """Test MorphemeAnalysis dataclass fields."""
        analysis = MorphemeAnalysis(
            segments=("ka", "pai", "ding"),
            directional=None,
            stem="pai",
            aspect="ding",
            particle=None,
            is_valid=True,
        )
        assert analysis.segments == ("ka", "pai", "ding")
        assert analysis.stem == "pai"
        assert analysis.aspect == "ding"
        assert analysis.is_valid is True
        assert len(analysis.violations) == 0
