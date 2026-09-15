"""Tests for Foundation Analysis module."""
from __future__ import annotations

import pytest

from zolai.foundation import (
    FoundationAnalyzer,
    ParagraphAnalysis,
    SentenceAnalysis,
    WordAnalysis,
    get_foundation_analyzer,
)


class TestFoundationAnalyzer:
    """Test FoundationAnalyzer orchestrates existing engines correctly."""

    @pytest.fixture(scope="class")
    def analyzer(self) -> FoundationAnalyzer:
        return get_foundation_analyzer(syllable_mode="rule")

    def test_analyze_word_basic(self, analyzer: FoundationAnalyzer) -> None:
        """Test word analysis for known word 'pasian'."""
        result = analyzer.analyze_word("pasian")

        assert isinstance(result, WordAnalysis)
        assert result.word == "pasian"
        assert result.primary_token.form == "pasian"
        assert result.primary_token.syllable_count == 2
        assert "pa" in [s.syllable for s in result.primary_token.syllables]
        assert "sian" in [s.syllable for s in result.primary_token.syllables]
        assert result.is_known_word is True
        assert result.zvs_compliant is True

    def test_analyze_word_compound(self, analyzer: FoundationAnalyzer) -> None:
        """Test word analysis for compound 'vantung'."""
        result = analyzer.analyze_word("vantung")

        assert result.word == "vantung"
        assert result.primary_token.syllable_count == 2
        assert result.primary_token.morphology.is_compound is True
        assert "van" in result.primary_token.morphology.compound_parts
        assert "tung" in result.primary_token.morphology.compound_parts

    def test_analyze_word_negation_particle(self, analyzer: FoundationAnalyzer) -> None:
        """Test word analysis for negation particle 'kei'."""
        result = analyzer.analyze_word("kei")

        assert result.word == "kei"
        assert result.primary_token.syllable_count == 1
        assert result.primary_token.pos.tag == "PART.NEG"
        assert result.primary_token.pos.confidence == 1.0

    def test_analyze_word_question_particle(self, analyzer: FoundationAnalyzer) -> None:
        """Test word analysis for question particle 'hiam'."""
        result = analyzer.analyze_word("hiam")

        assert result.word == "hiam"
        assert result.primary_token.pos.tag == "PART.QUESTION"
        assert result.primary_token.pos.confidence == 1.0

    def test_analyze_sentence_basic(self, analyzer: FoundationAnalyzer) -> None:
        """Test sentence analysis for Gen 1:1."""
        sentence = "Pasian in vantung leh leitung a piangsak hi."
        result = analyzer.analyze_sentence(sentence)

        assert isinstance(result, SentenceAnalysis)
        assert result.sentence == sentence
        assert result.sov_valid is True
        assert result.ergative_present is True
        assert result.negation_type is None
        assert result.question_type is None
        assert result.zvs_compliant is True
        assert "ergative_construction" in result.grammar_patterns_matched

    def test_analyze_sentence_negation_kei(self, analyzer: FoundationAnalyzer) -> None:
        """Test sentence with 'kei' negation."""
        sentence = "A pai kei hi."
        result = analyzer.analyze_sentence(sentence)

        assert result.negation_type == "kei"
        assert "negation_kei" in result.grammar_patterns_matched

    def test_analyze_sentence_question_hiam(self, analyzer: FoundationAnalyzer) -> None:
        """Test sentence with 'hiam' question."""
        sentence = "Na pai hiam?"
        result = analyzer.analyze_sentence(sentence)

        assert result.question_type == "hiam"
        assert "question_hiam" in result.grammar_patterns_matched

    def test_analyze_sentence_question_bang_hang(self, analyzer: FoundationAnalyzer) -> None:
        """Test sentence with 'bang hang' content question."""
        sentence = "Bang hang pai na hiam?"
        result = analyzer.analyze_sentence(sentence)

        assert result.question_type == "bang_hang"
        assert "question_bang_hang" in result.grammar_patterns_matched

    def test_analyze_sentence_future_ding(self, analyzer: FoundationAnalyzer) -> None:
        """Test sentence with future marker 'ding'."""
        sentence = "Ka pai ding hi."
        result = analyzer.analyze_sentence(sentence)

        assert result.tense == "future"
        assert "future_ding" in result.grammar_patterns_matched

    def test_analyze_paragraph_basic(self, analyzer: FoundationAnalyzer) -> None:
        """Test paragraph analysis."""
        paragraph = "Pasian in vantung leh leitung a piangsak hi. A mu hi cih a gen ding hi."
        result = analyzer.analyze_paragraph(paragraph)

        assert isinstance(result, ParagraphAnalysis)
        assert result.paragraph == paragraph
        assert result.sentence_count == 2
        assert result.total_tokens > 0
        assert result.register in ("biblical", "formal", "informal", "conversational")
        assert 0.0 <= result.cohesion_score <= 1.0

    def test_zvs_compliance_forbidden_forms(self, analyzer: FoundationAnalyzer) -> None:
        """Test ZVS compliance detects forbidden forms."""
        # 'pathian' is forbidden, should use 'pasian'
        result = analyzer.analyze_word("pathian")
        assert result.zvs_compliant is False
        assert any("pathian" in note for note in result.zvs_notes)

    def test_sov_detection(self, analyzer: FoundationAnalyzer) -> None:
        """Test SOV word order detection."""
        # Valid SOV
        sov_sentence = "Pasian in gam a mu hi."
        result = analyzer.analyze_sentence(sov_sentence)
        assert result.sov_valid is True

    def test_ergative_detection(self, analyzer: FoundationAnalyzer) -> None:
        """Test ergative 'in' detection."""
        ergative_sentence = "Mi in ne hi."
        result = analyzer.analyze_sentence(ergative_sentence)
        assert result.ergative_present is True

        non_ergative = "A pai hi."
        result2 = analyzer.analyze_sentence(non_ergative)
        assert result2.ergative_present is False


class TestFoundationDataclasses:
    """Test Foundation dataclass structures."""

    def test_word_analysis_structure(self) -> None:
        """Verify WordAnalysis has all required fields."""
        from zolai.foundation import MorphologyInfo, POSInfo, SyllableInfo, TokenAnalysis

        # Just verify imports work and dataclasses are constructible
        syllable = SyllableInfo(syllable="pa", start=0, end=2)
        morph = MorphologyInfo(
            stem="pasian", prefix="", suffix="", root="pasian",
            pos="NOUN", morphemes=("pasian",), meaning="God", particle="",
        )
        pos = POSInfo(tag="N.PROPER", confidence=1.0)
        token = TokenAnalysis(
            form="pasian", syllables=(syllable,), syllable_count=1,
            morphology=morph, pos=pos,
        )
        analysis = WordAnalysis(
            word="pasian", tokens=(token,), primary_token=token,
            is_known_word=True,
        )
        assert analysis.word == "pasian"

    def test_sentence_analysis_structure(self) -> None:
        """Verify SentenceAnalysis has all required fields."""
        from zolai.foundation import MorphologyInfo, POSInfo, SyllableInfo, TokenAnalysis

        syllable = SyllableInfo(syllable="pasian", start=0, end=6)
        morph = MorphologyInfo(
            stem="pasian", prefix="", suffix="", root="pasian",
            pos="NOUN", morphemes=("pasian",), meaning="God", particle="",
        )
        pos = POSInfo(tag="N.PROPER", confidence=1.0)
        token = TokenAnalysis(
            form="pasian", syllables=(syllable,), syllable_count=1,
            morphology=morph, pos=pos,
        )
        pos_info = POSInfo(tag="N.PROPER", confidence=1.0)
        analysis = SentenceAnalysis(
            sentence="Pasian om hi.", tokens=(token,), pos_tags=(pos_info,),
            sov_valid=True, ergative_present=False, negation_type=None,
            question_type=None, tense="present",
        )
        assert analysis.sentence == "Pasian om hi."

    def test_paragraph_analysis_structure(self) -> None:
        """Verify ParagraphAnalysis has all required fields."""
        from zolai.foundation import MorphologyInfo, POSInfo, SyllableInfo, TokenAnalysis

        syllable = SyllableInfo(syllable="pasian", start=0, end=6)
        morph = MorphologyInfo(
            stem="pasian", prefix="", suffix="", root="pasian",
            pos="NOUN", morphemes=("pasian",), meaning="God", particle="",
        )
        pos = POSInfo(tag="N.PROPER", confidence=1.0)
        token = TokenAnalysis(
            form="pasian", syllables=(syllable,), syllable_count=1,
            morphology=morph, pos=pos,
        )
        pos_info = POSInfo(tag="N.PROPER", confidence=1.0)
        sent = SentenceAnalysis(
            sentence="Pasian om hi.", tokens=(token,), pos_tags=(pos_info,),
            sov_valid=True, ergative_present=False, negation_type=None,
            question_type=None, tense="present",
        )
        analysis = ParagraphAnalysis(
            paragraph="Pasian om hi.", sentences=(sent,), sentence_count=1,
            total_tokens=1, style_profile={"narrative": 1.0},
            dominant_tense="present", register="biblical", cohesion_score=1.0,
        )
        assert analysis.paragraph == "Pasian om hi."
