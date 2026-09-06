"""
Data Quality Agent — validates and improves Zolai data quality.
"""
from __future__ import annotations

from ..api.accuracy_scorer import get_accuracy_scorer
from ..rules.zolai_rules_reference import ZolaiRules
from .base import AgentResult, ZolaiAgent


class DataQualityAgent(ZolaiAgent):
    """Validates Zolai data quality across dictionary, Bible, wiki."""

    def __init__(self) -> None:
        super().__init__("data_quality_agent")
        self.description = "Validates and improves Zolai data quality"

    def process(self, input_data: dict) -> AgentResult:
        action = input_data.get("action", "validate_entry")

        if action == "validate_entry":
            return self._validate_entry(input_data)
        elif action == "score_accuracy":
            return self._score_accuracy(input_data.get("word", ""))
        elif action == "check_consistency":
            return self._check_consistency(input_data.get("text", ""))
        else:
            return AgentResult(
                success=False,
                errors=[f"Unknown action: {action}"],
                agent_name=self.name,
            )

    def _validate_entry(self, input_data: dict) -> AgentResult:
        """Validate a dictionary entry."""
        zolai = input_data.get("zolai", "")
        english = input_data.get("english", "")
        pos = input_data.get("pos", "")

        issues: list[str] = []

        # Check for forbidden forms
        violations = ZolaiRules.check_forbidden(zolai)
        for forbidden, suggested, _ in violations:
            issues.append(
                f"Zolai contains forbidden form: {forbidden} → should be {suggested}"
            )

        # Check for empty fields
        if not zolai:
            issues.append("Zolai headword is empty")
        if not english:
            issues.append("English translation is empty")

        return AgentResult(
            success=len(issues) == 0,
            data={
                "zolai": zolai,
                "english": english,
                "pos": pos,
                "issues": issues,
                "valid": len(issues) == 0,
            },
            agent_name=self.name,
        )

    def _score_accuracy(self, word: str) -> AgentResult:
        """Score accuracy of a word across sources."""
        scorer = get_accuracy_scorer()
        score = scorer.score_word(word)

        return AgentResult(
            success=True,
            data=score,
            agent_name=self.name,
        )

    def _check_consistency(self, text: str) -> AgentResult:
        """Check text for consistency issues."""
        violations = ZolaiRules.check_forbidden(text)

        issues: list[dict] = []
        for forbidden, suggested, context in violations:
            issues.append(
                {
                    "type": "forbidden_form",
                    "found": forbidden,
                    "should_be": suggested,
                    "context": context,
                }
            )

        return AgentResult(
            success=len(issues) == 0,
            data={
                "text": text,
                "issues": issues,
                "consistent": len(issues) == 0,
            },
            agent_name=self.name,
        )
