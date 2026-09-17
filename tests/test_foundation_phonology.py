"""Tests for Foundation Phonological Analyzer."""
from __future__ import annotations

import pytest

from zolai.foundation.phonology import (
    PhonologicalAnalysis,
    PhonologicalAnalyzer,
    SyllableStructure,
    T1, T2, T3, T4,
    get_phonological_analyzer,
)


class TestPhonologicalAnalyzer:
    """Test PhonologicalAnalyzer for syllable validation, tone sandhi, phonotactics."""

    @pytest.fixture(scope="class")
    def analyzer(self) -> PhonologicalAnalyzer:
        return get_phonological_analyzer()

    def test_singleton(self) -> None:
        """Test singleton pattern."""
        a1 = get_phonological_analyzer()
        a2 = get_phonological_analyzer()
        assert a1 is a2

    def test_validate_syllable_structure(self, analyzer: PhonologicalAnalyzer) -> None:
        """Test syllable structure validation."""
        structures = analyzer.validate_syllable_structure("pasian")
        assert isinstance(structures, tuple)
        assert len(structures) > 0
        for s in structures:
            assert isinstance(s, SyllableStructure)
            assert hasattr(s, "syllable")
            assert hasattr(s, "onset")
            assert hasattr(s, "nucleus")
            assert hasattr(s, "coda")

    def test_validate_syllable_single(self, analyzer: PhonologicalAnalyzer) -> None:
        """Test single syllable word."""
        structures = analyzer.validate_syllable_structure("mi")
        assert len(structures) >= 1

    def test_parse_syllable(self, analyzer: PhonologicalAnalyzer) -> None:
        """Test syllable parsing."""
        onset, nucleus, coda = analyzer._parse_syllable("pan")
        assert onset == "p"
        assert nucleus == "a"
        assert coda == "n"

    def test_parse_syllable_vowel_initial(self, analyzer: PhonologicalAnalyzer) -> None:
        """Test vowel-initial syllable."""
        onset, nucleus, coda = analyzer._parse_syllable("om")
        assert onset == ""
        assert nucleus == "o"
        assert coda == "m"

    def test_parse_syllable_no_coda(self, analyzer: PhonologicalAnalyzer) -> None:
        """Test syllable without coda."""
        onset, nucleus, coda = analyzer._parse_syllable("pa")
        assert onset == "p"
        assert nucleus == "a"
        assert coda == ""

    def test_apply_tone_sandhi_t1_t3(self, analyzer: PhonologicalAnalyzer) -> None:
        """Test T1 + T3 → T2 + T3 sandhi rule."""
        result = analyzer.apply_tone_sandhi([T1, T3])
        assert result == [T2, T3]

    def test_apply_tone_sandhi_t3_t1(self, analyzer: PhonologicalAnalyzer) -> None:
        """Test T3 + T1 → T2 + T1 sandhi rule."""
        result = analyzer.apply_tone_sandhi([T3, T1])
        assert result == [T2, T1]

    def test_apply_tone_sandhi_t3_t3(self, analyzer: PhonologicalAnalyzer) -> None:
        """Test T3 + T3 → T2 + T3 sandhi rule."""
        result = analyzer.apply_tone_sandhi([T3, T3])
        assert result == [T2, T3]

    def test_apply_tone_sandhi_t3_t4(self, analyzer: PhonologicalAnalyzer) -> None:
        """Test T3 + T4 → T3 + T2 sandhi rule."""
        result = analyzer.apply_tone_sandhi([T3, T4])
        assert result == [T3, T2]

    def test_apply_tone_sandhi_no_change(self, analyzer: PhonologicalAnalyzer) -> None:
        """Test T1 + T1 → unchanged."""
        result = analyzer.apply_tone_sandhi([T1, T1])
        assert result == [T1, T1]

    def test_apply_tone_sandhi_empty(self, analyzer: PhonologicalAnalyzer) -> None:
        """Test empty tone list."""
        result = analyzer.apply_tone_sandhi([])
        assert result == []

    def test_apply_tone_sandhi_single(self, analyzer: PhonologicalAnalyzer) -> None:
        """Test single tone (no sandhi possible)."""
        result = analyzer.apply_tone_sandhi([T1])
        assert result == [T1]

    def test_get_tone_rules(self, analyzer: PhonologicalAnalyzer) -> None:
        """Test tone rules dictionary."""
        rules = analyzer.get_tone_rules()
        assert isinstance(rules, dict)
        assert (T1, T3) in rules
        assert rules[(T1, T3)] == (T2, T3)

    def test_check_phonotactics_valid(self, analyzer: PhonologicalAnalyzer) -> None:
        """Test phonotactic check on valid word."""
        valid, violations = analyzer.check_phonotactics("pasian")
        assert isinstance(valid, bool)
        assert isinstance(violations, tuple)

    def test_analyze_stress(self, analyzer: PhonologicalAnalyzer) -> None:
        """Test stress pattern analysis."""
        stress = analyzer.analyze_stress("pasian")
        assert isinstance(stress, tuple)
        assert len(stress) > 0
        assert stress[0] == "primary"  # Initial stress

    def test_analyze_stress_single_syllable(self, analyzer: PhonologicalAnalyzer) -> None:
        """Test stress on single syllable."""
        stress = analyzer.analyze_stress("mi")
        assert stress == ("primary",)

    def test_analyze_full(self, analyzer: PhonologicalAnalyzer) -> None:
        """Test full phonological analysis."""
        result = analyzer.analyze("vantung")
        assert isinstance(result, PhonologicalAnalysis)
        assert isinstance(result.syllable_structures, tuple)
        assert isinstance(result.tone_patterns, tuple)
        assert isinstance(result.sandhi_applied, tuple)
        assert isinstance(result.phonotactic_valid, bool)
        assert isinstance(result.stress_pattern, tuple)

    def test_analyze_empty(self, analyzer: PhonologicalAnalyzer) -> None:
        """Test analysis on empty word."""
        result = analyzer.analyze("")
        assert isinstance(result, PhonologicalAnalysis)
        # Empty word has no valid syllable structures
        assert result.phonotactic_valid is False

    def test_syllable_structure_dataclass(self) -> None:
        """Test SyllableStructure dataclass."""
        s = SyllableStructure(
            syllable="pan",
            onset="p",
            nucleus="a",
            coda="n",
            tone="T1",
            valid=True,
        )
        assert s.syllable == "pan"
        assert s.onset == "p"
        assert s.nucleus == "a"
        assert s.coda == "n"
        assert s.valid is True
