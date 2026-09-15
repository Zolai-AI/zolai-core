"""Gold Evaluation Metrics — Accuracy vs ground truth.

Computes token/word/paragraph-level accuracy against gold JSONL fixtures.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

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


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Complete evaluation report."""
    word_metrics: dict[str, MetricResult]
    sentence_metrics: dict[str, MetricResult]
    paragraph_metrics: dict[str, MetricResult]
    overall_accuracy: float

    def to_dict(self) -> dict[str, Any]:
        wm = {k: {"name": v.name, "value": v.value, "total": v.total} for k, v in self.word_metrics.items()}
        sm = {k: {"name": v.name, "value": v.value, "total": v.total} for k, v in self.sentence_metrics.items()}
        pm = {k: {"name": v.name, "value": v.value, "total": v.total} for k, v in self.paragraph_metrics.items()}
        return {
            "word_metrics": wm,
            "sentence_metrics": sm,
            "paragraph_metrics": pm,
            "overall_accuracy": self.overall_accuracy,
        }

    def print_summary(self) -> None:
        print("=" * 60)
        print("GOLD EVALUATION REPORT")
        print("=" * 60)
        for section, metrics in [
            ("WORD METRICS", self.word_metrics),
            ("SENTENCE METRICS", self.sentence_metrics),
            ("PARAGRAPH METRICS", self.paragraph_metrics),
        ]:
            print(f"\n{section}:")
            print("-" * 40)
            for name, result in metrics.items():
                status = "✓" if result.value >= 0.8 else "✗"
                print(f"  {status} {name}: {result.value:.2%} ({int(result.value * result.total)}/{result.total})")
        print(f"\nOVERALL ACCURACY: {self.overall_accuracy:.2%}")
        print("=" * 60)


def load_gold_words() -> list[dict[str, Any]]:
    path = GOLD_DIR / "words.jsonl"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def load_gold_sentences() -> list[dict[str, Any]]:
    path = GOLD_DIR / "sentences.jsonl"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def load_gold_paragraphs() -> list[dict[str, Any]]:
    path = GOLD_DIR / "paragraphs.jsonl"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def evaluate_words(analyzer: FoundationAnalyzer) -> dict[str, MetricResult]:
    gold = load_gold_words()
    if not gold:
        return {}
    total = len(gold)
    syllable_correct = 0
    pos_correct = 0
    root_correct = 0
    zvs_correct = 0
    meaning_correct = 0
    for item in gold:
        result = analyzer.analyze_word(item["word"])
        tok = result.primary_token
        if tok.syllable_count == item.get("syllable_count"):
            syllable_correct += 1
        if tok.pos.tag == item.get("pos"):
            pos_correct += 1
        if tok.morphology.root == item.get("root"):
            root_correct += 1
        if result.zvs_compliant == item.get("zvs_compliant"):
            zvs_correct += 1
        if item.get("meaning") and item["meaning"] in tok.morphology.meaning:
            meaning_correct += 1
    return {
        "syllable_count_accuracy": MetricResult("syllable_count_accuracy", syllable_correct / total, total),
        "pos_accuracy": MetricResult("pos_accuracy", pos_correct / total, total),
        "morphology_root_accuracy": MetricResult("morphology_root_accuracy", root_correct / total, total),
        "zvs_compliance_accuracy": MetricResult("zvs_compliance_accuracy", zvs_correct / total, total),
        "meaning_overlap_accuracy": MetricResult("meaning_overlap_accuracy", meaning_correct / total, total),
    }


def evaluate_sentences(analyzer: FoundationAnalyzer) -> dict[str, MetricResult]:
    gold = load_gold_sentences()
    if not gold:
        return {}
    total = len(gold)
    sov_correct = 0
    erg_correct = 0
    neg_correct = 0
    ques_correct = 0
    tense_correct = 0
    zvs_correct = 0
    tok_correct = 0
    for item in gold:
        result = analyzer.analyze_sentence(item["sentence"])
        if result.sov_valid == item.get("sov_valid"):
            sov_correct += 1
        if result.ergative_present == item.get("ergative_present"):
            erg_correct += 1
        if result.negation_type == item.get("negation_type"):
            neg_correct += 1
        if result.question_type == item.get("question_type"):
            ques_correct += 1
        if result.tense == item.get("tense"):
            tense_correct += 1
        if result.zvs_compliant == item.get("zvs_compliant"):
            zvs_correct += 1
        if len(result.tokens) == item.get("token_count"):
            tok_correct += 1
    return {
        "sov_accuracy": MetricResult("sov_accuracy", sov_correct / total, total),
        "ergative_detection_accuracy": MetricResult("ergative_detection_accuracy", erg_correct / total, total),
        "negation_detection_accuracy": MetricResult("negation_detection_accuracy", neg_correct / total, total),
        "question_detection_accuracy": MetricResult("question_detection_accuracy", ques_correct / total, total),
        "tense_detection_accuracy": MetricResult("tense_detection_accuracy", tense_correct / total, total),
        "zvs_compliance_accuracy": MetricResult("zvs_compliance_accuracy", zvs_correct / total, total),
        "token_count_accuracy": MetricResult("token_count_accuracy", tok_correct / total, total),
    }


def evaluate_paragraphs(analyzer: FoundationAnalyzer) -> dict[str, MetricResult]:
    gold = load_gold_paragraphs()
    if not gold:
        return {}
    total = len(gold)
    sent_correct = 0
    reg_correct = 0
    tense_correct = 0
    for item in gold:
        result = analyzer.analyze_paragraph(item["paragraph"])
        if result.sentence_count == item.get("sentence_count"):
            sent_correct += 1
        if result.register == item.get("register"):
            reg_correct += 1
        if result.dominant_tense == item.get("dominant_tense"):
            tense_correct += 1
    return {
        "sentence_count_accuracy": MetricResult("sentence_count_accuracy", sent_correct / total, total),
        "register_detection_accuracy": MetricResult("register_detection_accuracy", reg_correct / total, total),
        "dominant_tense_accuracy": MetricResult("dominant_tense_accuracy", tense_correct / total, total),
    }


def run_gold_evaluation(
    syllable_mode: str = "rule",
    tokenizer_model: Optional[str] = None,
) -> EvaluationReport:
    """Run full gold evaluation suite."""
    analyzer = get_foundation_analyzer(syllable_mode=syllable_mode, tokenizer_model=tokenizer_model)
    word_metrics = evaluate_words(analyzer)
    sentence_metrics = evaluate_sentences(analyzer)
    paragraph_metrics = evaluate_paragraphs(analyzer)

    all_values = [m.value for d in [word_metrics, sentence_metrics, paragraph_metrics] for m in d.values()]
    overall = sum(all_values) / len(all_values) if all_values else 0.0

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
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    report = run_gold_evaluation(
        syllable_mode=args.syllable_mode,
        tokenizer_model=args.tokenizer_model,
    )

    if args.json:
        import json
        out = json.dumps(report.to_dict(), indent=2)
        if args.output:
            Path(args.output).write_text(out, encoding="utf-8")
        else:
            print(out)
    else:
        report.print_summary()

    if report.overall_accuracy < 0.8:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
