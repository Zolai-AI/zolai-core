"""Foundation Analysis — Word, Sentence, Paragraph.

Orchestrates existing Zolai engines:
- zolai.tokenizer (SentencePiece)
- zolai.syllable.SyllableSegmenter
- zolai.pos_tagger.ZolaiPOSTagger
- zolai.morphology.ZolaiMorphology

Produces structured analyses for Foundation pipeline.
"""
from __future__ import annotations
import re

import logging
from dataclasses import dataclass, field
from typing import Optional

from zolai.morphology import ZolaiMorphology, get_morphology, _KNOWN_ROOTS as MORPH_KNOWN_ROOTS
from zolai.pos_tagger import ZolaiPOSTagger, get_pos_tagger
from zolai.syllable import ZolaiSyllabifier, segment, segment_with_boundaries, Boundary
from zolai.tokenizer.zolai_tokenizer import ZolaiTokenizer

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SyllableInfo:
    """Syllable with boundary metadata."""
    syllable: str
    start: int
    end: int

    @classmethod
    def from_boundary(cls, b: Boundary) -> SyllableInfo:
        return cls(syllable=b.syllable, start=b.start, end=b.end)


@dataclass(frozen=True, slots=True)
class MorphologyInfo:
    """Morphological analysis result."""
    stem: str
    prefix: str
    suffix: str
    root: str
    pos: str
    morphemes: tuple[str, ...]
    meaning: str
    particle: str
    is_compound: bool = False
    compound_parts: tuple[str, ...] = field(default_factory=tuple)
    compound_meanings: tuple[str, ...] = field(default_factory=tuple)
    is_reduplication: bool = False


@dataclass(frozen=True, slots=True)
class POSInfo:
    """POS tag with confidence."""
    tag: str
    confidence: float


@dataclass(frozen=True, slots=True)
class TokenAnalysis:
    """Single token analysis combining all engines."""
    form: str
    syllables: tuple[SyllableInfo, ...]
    syllable_count: int
    morphology: MorphologyInfo
    pos: POSInfo
    tokenizer_piece_ids: tuple[int, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class WordAnalysis:
    """Complete word-level analysis (ref section 25)."""
    word: str
    tokens: tuple[TokenAnalysis, ...]  # Usually 1, but compounds may split
    primary_token: TokenAnalysis
    is_known_word: bool
    dictionary_senses: tuple[str, ...] = field(default_factory=tuple)
    bible_attestations: tuple[str, ...] = field(default_factory=tuple)
    zvs_compliant: bool = True
    zvs_notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class SentenceAnalysis:
    """Complete sentence-level analysis (ref section 33)."""
    sentence: str
    tokens: tuple[TokenAnalysis, ...]
    pos_tags: tuple[POSInfo, ...]
    sov_valid: bool
    ergative_present: bool
    negation_type: Optional[str]  # 'kei' | 'lo' | None
    question_type: Optional[str]  # 'hiam' | 'diam' | 'bang_hang' | None
    tense: Optional[str]  # 'present' | 'past' | 'future' | 'completive' | ...
    english_translation: Optional[str] = None
    bible_reference: Optional[str] = None
    grammar_patterns_matched: tuple[str, ...] = field(default_factory=tuple)
    zvs_compliant: bool = True
    zvs_violations: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ParagraphAnalysis:
    """Complete paragraph-level analysis (ref section 43)."""
    paragraph: str
    sentences: tuple[SentenceAnalysis, ...]
    sentence_count: int
    total_tokens: int
    style_profile: dict[str, float]  # e.g. {'narrative': 0.7, 'dialogue': 0.2, 'poetic': 0.1}
    dominant_tense: Optional[str]
    register: str  # 'formal' | 'informal' | 'biblical' | 'conversational'
    paraphrases: tuple[str, ...] = field(default_factory=tuple)  # Multi-style generation
    cohesion_score: float = 0.0  # 0.0-1.0


class FoundationAnalyzer:
    """Orchestrates existing Zolai engines for Foundation analysis.

    Single entry point for word/sentence/paragraph analysis.
    All engines are lazily initialized on first use.
    """

    def __init__(
        self,
        syllable_mode: str = "rule",
        tokenizer_model: Optional[str] = None,
    ) -> None:
        self._syllable_mode = syllable_mode
        self._tokenizer_model = tokenizer_model

        # Lazy-initialized engines
        self._syllabifier: Optional[ZolaiSyllabifier] = None
        self._tokenizer: Optional[ZolaiTokenizer] = None
        self._pos_tagger: Optional[ZolaiPOSTagger] = None
        self._morphology: Optional[ZolaiMorphology] = None

    # ── Engine accessors (lazy) ──────────────────────────────────────────────
    @property
    def syllabifier(self) -> ZolaiSyllabifier:
        if self._syllabifier is None:
            self._syllabifier = ZolaiSyllabifier(mode=self._syllable_mode)
        return self._syllabifier

    @property
    def tokenizer(self) -> ZolaiTokenizer:
        if self._tokenizer is None:
            self._tokenizer = ZolaiTokenizer()
            if self._tokenizer_model:
                self._tokenizer.load(self._tokenizer_model)
        return self._tokenizer

    @property
    def pos_tagger(self) -> ZolaiPOSTagger:
        if self._pos_tagger is None:
            self._pos_tagger = get_pos_tagger()
        return self._pos_tagger

    @property
    def morphology(self) -> ZolaiMorphology:
        if self._morphology is None:
            self._morphology = get_morphology()
        return self._morphology

    # ── Core analysis methods ────────────────────────────────────────────────
    def analyze_word(self, word: str) -> WordAnalysis:
        """Analyze a single Zolai word (ref section 25)."""
        clean_word = word.strip()
        if not clean_word:
            raise ValueError("Empty word")

        # Syllable segmentation
        syllables = segment_with_boundaries(clean_word)
        syllable_infos = tuple(SyllableInfo.from_boundary(b) for b in syllables)

        # Morphology
        morph_result = self.morphology.analyze(clean_word)
        morph_info = MorphologyInfo(
            stem=morph_result.get("stem", ""),
            prefix=morph_result.get("prefix", ""),
            suffix=morph_result.get("suffix", ""),
            root=morph_result.get("root", ""),
            pos=morph_result.get("POS", "X"),
            morphemes=tuple(morph_result.get("morphemes", [])),
            meaning=morph_result.get("meaning", ""),
            particle=morph_result.get("particle", ""),
            is_compound=bool(morph_result.get("morphemes")) and "+" in morph_result.get("root", ""),
            compound_parts=tuple(),
            compound_meanings=tuple(),
            is_reduplication=False,
        )

        # Detect compound from morphemes
        if morph_info.is_compound and "root" in morph_result:
            root_parts = morph_result["root"].split("+")
            if len(root_parts) > 1:
                morph_info = MorphologyInfo(
                    stem=morph_info.stem,
                    prefix=morph_info.prefix,
                    suffix=morph_info.suffix,
                    root=morph_info.root,
                    pos=morph_info.pos,
                    morphemes=morph_info.morphemes,
                    meaning=morph_info.meaning,
                    particle=morph_info.particle,
                    is_compound=True,
                    compound_parts=tuple(root_parts),
                    compound_meanings=tuple(),
                )

        # Detect reduplication
        if len(clean_word) >= 4:
            half = len(clean_word) // 2
            if len(clean_word) % 2 == 0 and clean_word[:half] == clean_word[half:]:
                morph_info = MorphologyInfo(
                    stem=morph_info.stem,
                    prefix=morph_info.prefix,
                    suffix=morph_info.suffix,
                    root=morph_info.root,
                    pos=morph_info.pos,
                    morphemes=morph_info.morphemes,
                    meaning=morph_info.meaning,
                    particle=morph_info.particle,
                    is_compound=morph_info.is_compound,
                    compound_parts=morph_info.compound_parts,
                    compound_meanings=morph_info.compound_meanings,
                    is_reduplication=True,
                )

        # POS tagging
        pos_tagged = self.pos_tagger.tag_with_confidence(clean_word)
        pos_info = POSInfo(tag=pos_tagged[0][1], confidence=pos_tagged[0][2])

        # Tokenizer
        tokenizer_ids = tuple(self.tokenizer.encode(clean_word)) if self._tokenizer_model else tuple()

        # Token analysis
        token = TokenAnalysis(
            form=clean_word,
            syllables=syllable_infos,
            syllable_count=len(syllable_infos),
            morphology=morph_info,
            pos=pos_info,
            tokenizer_piece_ids=tokenizer_ids,
        )

        # Dictionary senses (from morphology known roots)
        dict_senses = ()
        if morph_info.root in MORPH_KNOWN_ROOTS:
            root_info = MORPH_KNOWN_ROOTS[morph_info.root]
            dict_senses = (root_info.get("meaning", ""),)

        # Bible attestations (from POS tagger's bible words)
        bible_attestations = ()
        if clean_word.lower() in self.pos_tagger._bible_words:
            bible_attestations = ("Bible corpus",)

        # ZVS compliance check (basic)
        zvs_compliant, zvs_notes = self._check_zvs_word(clean_word, morph_info)

        return WordAnalysis(
            word=clean_word,
            tokens=(token,),
            primary_token=token,
            is_known_word=clean_word.lower() in self.morphology._dict_words
            or clean_word.lower() in self.pos_tagger._dict_pos,
            dictionary_senses=dict_senses,
            bible_attestations=bible_attestations,
            zvs_compliant=zvs_compliant,
            zvs_notes=zvs_notes,
        )

    def analyze_sentence(self, sentence: str) -> SentenceAnalysis:
        """Analyze a Zolai sentence (ref section 33)."""
        clean_sentence = sentence.strip()
        if not clean_sentence:
            raise ValueError("Empty sentence")

        # Tokenize by whitespace (Zolai uses space-separated words)
        words = clean_sentence.split()

        # Analyze each word
        tokens: list[TokenAnalysis] = []
        pos_infos: list[POSInfo] = []

        for word in words:
            word_analysis = self.analyze_word(word)
            tokens.append(word_analysis.primary_token)
            pos_infos.append(word_analysis.primary_token.pos)

        # POS tags from tagger (more accurate for sentence context)
        pos_tagged = self.pos_tagger.tag_with_confidence(clean_sentence)
        pos_infos = tuple(POSInfo(tag=p[1], confidence=p[2]) for p in pos_tagged)

        # Grammar checks
        sov_valid = self._check_sov(words, pos_tagged)
        ergative_present = any(p[1] == "PART.ERG" for p in pos_tagged)
        negation_type = self._detect_negation(pos_tagged)
        question_type = self._detect_question(clean_sentence, pos_tagged)
        tense = self._detect_tense(pos_tagged)
        grammar_patterns = self._match_grammar_patterns(clean_sentence)

        # ZVS compliance
        zvs_compliant, zvs_violations = self._check_zvs_sentence(clean_sentence, pos_tagged)

        # Bible reference lookup
        bible_ref = self._lookup_bible_reference(clean_sentence)

        return SentenceAnalysis(
            sentence=clean_sentence,
            tokens=tuple(tokens),
            pos_tags=pos_infos,
            sov_valid=sov_valid,
            ergative_present=ergative_present,
            negation_type=negation_type,
            question_type=question_type,
            tense=tense,
            bible_reference=bible_ref,
            grammar_patterns_matched=tuple(grammar_patterns),
            zvs_compliant=zvs_compliant,
            zvs_violations=tuple(zvs_violations),
        )

    def analyze_paragraph(self, paragraph: str) -> ParagraphAnalysis:
        """Analyze a Zolai paragraph (ref section 43)."""
        clean_paragraph = paragraph.strip()
        if not clean_paragraph:
            raise ValueError("Empty paragraph")

        # Simple sentence splitting (Zolai uses . ? ! as sentence terminators)
        import re
        sentence_texts = re.split(r"[.!?]+\s*", clean_paragraph)
        sentence_texts = [s.strip() for s in sentence_texts if s.strip()]

        sentences: list[SentenceAnalysis] = []
        for sent_text in sentence_texts:
            try:
                sent_analysis = self.analyze_sentence(sent_text)
                sentences.append(sent_analysis)
            except Exception as e:
                log.warning("Failed to analyze sentence '%s': %s", sent_text, e)

        # Style profiling (simple heuristic)
        style_profile = self._compute_style_profile(sentences)
        dominant_tense = self._dominant_tense(sentences)
        register = self._detect_register(sentences, clean_paragraph)
        cohesion_score = self._compute_cohesion(sentences)

        return ParagraphAnalysis(
            paragraph=clean_paragraph,
            sentences=tuple(sentences),
            sentence_count=len(sentences),
            total_tokens=sum(len(s.tokens) for s in sentences),
            style_profile=style_profile,
            dominant_tense=dominant_tense,
            register=register,
            cohesion_score=cohesion_score,
        )

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _check_zvs_word(self, word: str, morph: MorphologyInfo) -> tuple[bool, tuple[str, ...]]:
        """Basic ZVS 2018 compliance check for a word."""
        forbidden = {
            "pathian": "pasian",
            "ram": "gam",
            "fapa": "tapa",
            "bawipa": "topa",
            "siangpahrang": "kumpipa",
            "cu": "tua",
            "cun": "tua",
            "suah": "suahtakna",
            "nunnak": "nuntakna",
        }
        lower = word.lower()
        notes = []
        compliant = True
        for forbidden_form, correct in forbidden.items():
            if forbidden_form in lower:
                compliant = False
                notes.append(f"Forbidden form '{forbidden_form}' → use '{correct}'")
        return compliant, tuple(notes)

    def _check_zvs_sentence(
        self, sentence: str, pos_tagged: list[tuple[str, str, float]]
    ) -> tuple[bool, tuple[str, ...]]:
        """Basic ZVS 2018 compliance check for a sentence."""
        violations = []
        words = [p[0] for p in pos_tagged]

        # Check for forbidden forms in sentence
        forbidden = {
            "pathian": "pasian",
            "ram": "gam",
            "fapa": "tapa",
            "bawipa": "topa",
            "siangpahrang": "kumpipa",
            "cu": "tua",
            "cun": "tua",
        }
        for w in words:
            lower = w.lower()
            for forbidden_form, correct in forbidden.items():
                if forbidden_form == lower:
                    violations.append(f"Forbidden form '{forbidden_form}' → use '{correct}'")

        # Check 'lo' negation without agreement
        for i, (word, pos, _) in enumerate(pos_tagged):
            if word.lower() == "lo" and pos != "PART.NEG":
                violations.append("'lo' negation must not take agreement marker")

        # Check content question word order: bang hang V S hiam
        if "bang" in [w.lower() for w in words] and "hang" in [w.lower() for w in words]:
            bang_idx = next(i for i, w in enumerate(words) if w.lower() == "bang")
            hang_idx = next(i for i, w in enumerate(words) if w.lower() == "hang")
            if hang_idx != bang_idx + 1:
                violations.append("Content question requires 'bang hang' sequence")

        return len(violations) == 0, tuple(violations)

    def _check_sov(self, words: list[str], pos_tagged: list[tuple[str, str, float]]) -> bool:
        """Check if sentence follows SOV word order (heuristic)."""
        # Find verb position (last VERB or V.* tag)
        verb_positions = [
            i for i, (_, pos, _) in enumerate(pos_tagged)
            if pos.startswith("VERB")
        ]
        if not verb_positions:
            return True  # No verb found, assume valid
        # Verb should be last or near-last (before final particles)
        last_verb = max(verb_positions)
        # Allow particles after verb (hi, hen, un, etc.)
        non_particle_after = any(
            not pos.startswith("PART") for _, pos, _ in pos_tagged[last_verb + 1 :]
        )
        return not non_particle_after

    def _detect_negation(self, pos_tagged: list[tuple[str, str, float]]) -> Optional[str]:
        for word, pos, _ in pos_tagged:
            if word.lower() == "kei":
                return "kei"
            if word.lower() == "lo" and pos == "PART.NEG":
                return "lo"
        return None

    def _detect_question(
        self, sentence: str, pos_tagged: list[tuple[str, str, float]]
    ) -> Optional[str]:
        import re
        words = [re.sub(r"[.!?]+$", "", p[0]).lower() for p in pos_tagged]
        if words and words[-1] == "hiam":
            if "bang" in words and "hang" in words:
                return "bang_hang"
            return "hiam"
        if words and words[-1] == "diam":
            return "diam"
        return None

    def _detect_tense(self, pos_tagged: list[tuple[str, str, float]]) -> Optional[str]:
        tense_markers = {
            "ding": "future",
            "ta": "past",
            "zo": "completive",
            "lai": "progressive",
            "hen": "experiential",
            "khin": "experiential",
            "tak": "completive",
        }
        for word, pos, _ in pos_tagged:
            if word.lower() in tense_markers:
                return tense_markers[word.lower()]
        return "present"  # default

    def _match_grammar_patterns(self, sentence: str) -> list[str]:
        """Match against known grammar patterns (simplified)."""
        patterns = []
        lower = sentence.lower()
        # Split and strip punctuation for token matching
        tokens = [re.sub(r'[^\w\-]+$', '', t) for t in lower.split()]
        if " in " in f" {lower} ":
            patterns.append("ergative_construction")
        if "kei" in tokens:
            patterns.append("negation_kei")
        if "lo" in tokens:
            patterns.append("negation_lo")
        if "hiam" in tokens:
            patterns.append("question_hiam")
        if "bang hang" in lower:
            patterns.append("question_bang_hang")
        if "ding" in tokens:
            patterns.append("future_ding")
        return patterns

    def _lookup_bible_reference(self, sentence: str) -> Optional[str]:
        """Look up sentence in Bible verses (simplified)."""
        # This would query the database in production
        # For now, return None
        return None

    def _compute_style_profile(
        self, sentences: list[SentenceAnalysis]
    ) -> dict[str, float]:
        """Simple style profiling heuristic."""
        if not sentences:
            return {"narrative": 1.0}

        total = len(sentences)
        narrative = sum(1 for s in sentences if s.tense in ("past", "present"))
        dialogue = sum(1 for s in sentences if s.question_type)
        poetic = sum(1 for s in sentences if "parallelism" in s.grammar_patterns_matched)

        return {
            "narrative": narrative / total if total else 0.0,
            "dialogue": dialogue / total if total else 0.0,
            "poetic": poetic / total if total else 0.0,
        }

    def _dominant_tense(self, sentences: list[SentenceAnalysis]) -> Optional[str]:
        if not sentences:
            return None
        tenses = [s.tense for s in sentences if s.tense]
        if not tenses:
            return None
        return max(set(tenses), key=tenses.count)

    def _detect_register(
        self, sentences: list[SentenceAnalysis], paragraph: str
    ) -> str:
        """Detect register: formal, informal, biblical, conversational."""
        if any(s.bible_reference for s in sentences):
            return "biblical"
        if any(s.question_type for s in sentences):
            return "conversational"
        if any("lo" in s.sentence.lower().split() for s in sentences):
            return "formal"  # literary negation
        return "informal"

    def _compute_cohesion(self, sentences: list[SentenceAnalysis]) -> float:
        """Simple cohesion score based on shared entities/tense."""
        if len(sentences) < 2:
            return 1.0
        # Heuristic: same tense across sentences = higher cohesion
        tenses = [s.tense for s in sentences if s.tense]
        if not tenses:
            return 0.5
        dominant = max(set(tenses), key=tenses.count)
        return tenses.count(dominant) / len(tenses)


# ── Module-level singleton ──────────────────────────────────────────────────
_analyzer: Optional[FoundationAnalyzer] = None


def get_foundation_analyzer(
    syllable_mode: str = "rule", tokenizer_model: Optional[str] = None
) -> FoundationAnalyzer:
    """Get or create the singleton FoundationAnalyzer."""
    global _analyzer  # noqa: PLW0603
    if _analyzer is None:
        _analyzer = FoundationAnalyzer(
            syllable_mode=syllable_mode, tokenizer_model=tokenizer_model
        )
    return _analyzer