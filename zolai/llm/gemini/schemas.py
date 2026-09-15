"""Pydantic output schemas for structured Gemini verification responses."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BaseVerificationResult(BaseModel):
    """Common fields shared by all verification result schemas.

    Attributes:
        is_valid: Whether the candidate passes verification.
        confidence: Model confidence (0.0–1.0).
        evidence_assessment: Explanations of how evidence was evaluated.
        disagreements: Conflicts found between evidence sources.
        requires_human_review: Whether a human should review this result.
    """

    is_valid: bool
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_assessment: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    requires_human_review: bool = False


class WordVerificationResult(BaseVerificationResult):
    """Verification result for a single Zolai word.

    Attributes:
        canonical_form: The correct ZVS 2018 spelling.
        syllables: Syllable breakdown of the word.
        pos: Part of speech.
        morphology: Morphological analysis.
        senses: List of meaning senses.
    """

    canonical_form: str = ""
    syllables: list[str] = Field(default_factory=list)
    pos: str = ""
    morphology: str = ""
    senses: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}


class GrammarCorrection(BaseModel):
    """A single grammar correction suggestion.

    Attributes:
        rule: The grammar rule violated.
        found: What was found in the text.
        expected: What the correct form should be.
    """

    rule: str
    found: str
    expected: str

    model_config = {"frozen": True}


class SentenceVerificationResult(BaseVerificationResult):
    """Verification result for a Zolai sentence.

    Attributes:
        sov_valid: Whether SOV word order is correct.
        ergative_present: Whether ergative "in" is used correctly.
        negation_type: Type of negation detected.
        question_type: Type of question detected.
        tense: Tense/aspect identified.
        translation: English translation of the sentence.
    """

    sov_valid: bool = True
    ergative_present: bool = False
    negation_type: str = "none"
    question_type: str = "none"
    tense: str = "unknown"
    translation: str = ""

    model_config = {"frozen": True}


class GrammarVerificationResult(BaseVerificationResult):
    """Detailed grammar verification result.

    Attributes:
        sov_valid: Whether SOV word order is correct.
        ergative_present: Whether ergative "in" is used correctly.
        negation_type: Type of negation detected.
        question_type: Type of question detected.
        tense: Tense/aspect identified.
        translation: English translation.
        violations: List of specific grammar violations found.
        corrections: List of suggested corrections.
    """

    sov_valid: bool = True
    ergative_present: bool = False
    negation_type: str = "none"
    question_type: str = "none"
    tense: str = "unknown"
    translation: str = ""
    violations: list[str] = Field(default_factory=list)
    corrections: list[GrammarCorrection] = Field(default_factory=list)

    model_config = {"frozen": True}
