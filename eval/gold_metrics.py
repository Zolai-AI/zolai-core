"""Gold Evaluation Metrics — Accuracy vs ground truth.

Computes token/word/paragraph-level accuracy against gold JSONL fixtures.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zolai.foundation import (
    FoundationAnalyzer,
    get_foundation_analyzer,
)

log = logging.getLogger(__name__)

GOLD_DIR = Path(__file__).parent.parent / "data" / "gold"


@dataclass(frozen=True, slots=True)
class MetricResult:
    """Single metric result."""
    name: str
    value: float
    total: int
    correct: int
    details: dict[str, Any] = None  # type: ignore


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Complete evaluation report."""
    word_metrics: list[MetricResult]
    sentence_metrics: list[MetricResult]
    paragraph_metrics: list[MetricResult]
    overall_accuracy: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "word_metrics": [
                {"name": m.name, "value": m.value, "total": m.total, "correct": m.correct, "details": m.details}
                for m in self.word_metrics
            ],
            "sentence_metrics": [
                {"name": m.name, "value": m.value, "total": m.total, "correct": m.correct, "details": m.details}
                for m in self.sentence_metrics
            ],
            "paragraph_metrics": [
                {"name": m.name, "value": m.value, "total": m.total, "correct": m.correct, "details": m.details}
                for m in self.paragraph_metrics
            ],
            "overall_accuracy": self.overall_accuracy,
        }

    def print_summary(self) -> None:
        print("\n" + "=" * 60)
        print("GOLD EVALUATION REPORT")
        print("=" * 60)

        for category, metrics in [
            ("WORD", self.word_metrics),
            ("SENTENCE", self.sentence_metrics),
            ("PARAGRAPH", self.paragraph_metrics),
        ]:
            print(f"\n{category} METRICS:")
            print("-" * 40)
            for m in metrics:
                status = "✓" if m.value >= 0.8 else "✗"
                print(f"  {status} {m.name}: {m.value:.2%} ({m.correct}/{m.total})")
                if m.details:
                    for k, v in m.details.items():
                        print(f"      {k}: {v}")

        print(f"\nOVERALL ACCURACY: {self.overall_accuracy:.2%}")
        print("=" * 60)


def load_gold_words() -> list[dict[str, Any]]:
    """Load gold word analyses."""
    path = GOLD_DIR / "words.jsonl"
    if not path.exists():
        return []
    results = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                results.append(json.loads(line))
    return results


def load_gold_sentences() -> list[dict[str, Any]]:
    """Load gold sentence analyses."""
    path = GOLD_DIR / "sentences.jsonl"
    if not path.exists():
        return []
    results = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                results.append(json.loads(line))
    return results


def load_gold_paragraphs() -> list[dict[str, Any]]:
    """Load gold paragraph analyses."""
    path = GOLD_DIR / "paragraphs.jsonl"
    if not path.exists():
        return []
    results = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                results.append(json.loads(line))
    return results


def evaluate_words(analyzer: FoundationAnalyzer) -> list[MetricResult]:
    """Evaluate word analysis against gold."""
    gold = load_gold_words()
    if not gold:
        return [MetricResult("words", 0.0, 0, 0, {"error": "No gold data"})]

    total = len(gold)
    correct_syllables = 0
    correct_pos = 0
    correct_morphology_root = 0
    correct_zvs = 0
    correct_meanings = 0

    for item in gold:
        word = item["word"]
        try:
            analysis = analyzer.analyze_word(word)

            # Syllable count match
            if analysis.primary_token.syllable_count == item["syllable_count"]:
                correct_syllables += 1

            # POS match
            if analysis.primary_token.pos.tag == item["pos"]:
                correct_pos += 1

            # Morphology root match
            if analysis.primary_token.morphology.root == item["morphology"]["root"]:
                correct_morphology_root += 1

            # ZVS compliance match
            if analysis.zvs_compliant == item["zvs_compliant"]:
                correct_zvs += 1

            # Meanings overlap
            gold_meanings = set(item["meanings"])
            pred_meanings = set(analysis.dictionary_senses)
            if gold_meanings & pred_meanings:
                correct_meanings += 1

        except Exception as e:
            log.warning("Failed to analyze gold word '%s': %s", word, e)

    return [
        MetricResult("syllable_count_accuracy", correct_syllables / total, total, correct_syllables),
        MetricResult("pos_accuracy", correct_pos / total, total, correct_pos),
        MetricResult("morphology_root_accuracy", correct_morphology_root / total, total, correct_morphology_root),
        MetricResult("zvs_compliance_accuracy", correct_zvs / total, total, correct_zvs),
        MetricResult("meaning_overlap_accuracy", correct_meanings / total, total, correct_meanings),
    ]


def evaluate_sentences(analyzer: FoundationAnalyzer) -> list[MetricResult]:
    """Evaluate sentence analysis against gold."""
    gold = load_gold_sentences()
    if not gold:
        return [MetricResult("sentences", 0.0, 0, 0, {"error": "No gold data"})]

    total = len(gold)
    correct_sov = 0
    correct_ergative = 0
    correct_negation = 0
    correct_question = 0
    correct_tense = 0
    correct_zvs = 0
    correct_token_count = 0

    for item in gold:
        sentence = item["sentence"]
        try:
            analysis = analyzer.analyze_sentence(sentence)

            if analysis.sov_valid == item["sov_valid"]:
                correct_sov += 1
            if analysis.ergative_present == item["ergative_present"]:
                correct_ergative += 1
            if analysis.negation_type == item["negation_type"]:
                correct_negation += 1
            if analysis.question_type == item["question_type"]:
                correct_question += 1
            if analysis.tense == item["tense"]:
                correct_tense += 1
            if analysis.zvs_compliant == item["zvs_compliant"]:
                correct_zvs += 1
            if len(analysis.tokens) == len(item["tokens"]):
                correct_token_count += 1

        except Exception as e:
            log.warning("Failed to analyze gold sentence '%s': %s", sentence, e)

    return [
        MetricResult("sov_accuracy", correct_sov / total, total, correct_sov),
        MetricResult("ergative_detection_accuracy", correct_ergative / total, total, correct_ergative),
        MetricResult("negation_detection_accuracy", correct_negation / total, total, correct_negation),
        MetricResult("question_detection_accuracy", correct_question / total, total, correct_question),
        MetricResult("tense_detection_accuracy", correct_tense / total, total, correct_tense),
        MetricResult("zvs_compliance_accuracy", correct_zvs / total, total, correct_zvs),
        MetricResult("token_count_accuracy", correct_token_count / total, total, correct_token_count),
    ]


def evaluate_paragraphs(analyzer: FoundationAnalyzer) -> list[MetricResult]:
    """Evaluate paragraph analysis against gold."""
    gold = load_gold_paragraphs()
    if not gold:
        return [MetricResult("paragraphs", 0.0, 0, 0, {"error": "No gold data"})]

    total = len(gold)
    correct_sentence_count = 0
    correct_register = 0
    correct_dominant_tense = 0

    for item in gold:
        paragraph = item["paragraph"]
        try:
            analysis = analyzer.analyze_paragraph(paragraph)

            if analysis.sentence_count == item["sentence_count"]:
                correct_sentence_count += 1
            if analysis.register == item["register"]:
                correct_register += 1
            if analysis.dominant_tense == item["dominant_tense"]:
                correct_dominant_tense += 1

        except Exception as e:
            log.warning("Failed to analyze gold paragraph '%s': %s", paragraph, e)

    return [
        MetricResult("sentence_count_accuracy", correct_sentence_count / total, total, correct_sentence_count),
        MetricResult("register_detection_accuracy", correct_register / total, total, correct_register),
        MetricResult("dominant_tense_accuracy", correct_dominant_tense / total, total, correct_dominant_tense),
    ]


def run_gold_evaluation(
    syllable_mode: str = "rule",
    tokenizer_model: Optional[str] = None,
) -> EvaluationReport:
    """Run full gold evaluation suite."""
    analyzer = get_foundation_analyzer(syllable_mode=syllable_mode, tokenizer_model=tokenizer_model)

    word_metrics = evaluate_words(analyzer)
    sentence_metrics = evaluate_sentences(analyzer)
    paragraph_metrics = evaluate_paragraphs(analyzer)

    # Compute overall accuracy (macro average of all metrics)
    all_metrics = word_metrics + sentence_metrics + paragraph_metrics
    valid_metrics = [m for m in all_metrics if m.total > 0]
    overall = sum(m.value for m in valid_metrics) / len(valid_metrics) if valid_metrics else 0.0

    return EvaluationReport(
        word_metrics=word_metrics,
        sentence_metrics=sentence_metrics,
        paragraph_metrics=paragraph_metrics,
        overall_accuracy=overall,
    )


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point for gold evaluation."""
    import argparse

    parser = argparse.ArgumentParser(description="Run gold evaluation")
    parser.add_argument("--syllable-mode", default="rule", choices=["rule", "crf"])
    parser.add_argument("--tokenizer-model", default=None)
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--output", default=None, help="Output file path")

    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)

    report = run_gold_evaluation(
        syllable_mode=args.syllable_mode,
        tokenizer_model=args.tokenizer_model,
    )

    if args.json:
        output = json.dumps(report.to_dict(), indent=2)
        if args.output:
            Path(args.output).write_text(output, encoding="utf-8")
        else:
            print(output)
    else:
        report.print_summary()

    # Exit with non-zero if overall accuracy < 0.8
    return 0 if report.overall_accuracy >= 0.8 else 1


if __name__ == "__main__":
    raise SystemExit(main())
