"""
Grammar Agent — checks Zolai grammar rules.
"""
from __future__ import annotations

import re

from ..rules.zolai_rules_reference import ZolaiRules
from .base import AgentResult, ZolaiAgent


class GrammarAgent(ZolaiAgent):
    """Checks Zolai grammar: ZVS, SOV, tense, negation, particles."""

    def __init__(self) -> None:
        super().__init__("grammar_agent")
        self.description = "Checks Zolai grammar rules and suggests corrections"
        self.rules = ZolaiRules()

    def process(self, input_data: dict) -> AgentResult:
        text = input_data.get("text", "")
        if not text:
            return AgentResult(
                success=False, errors=["No text provided"], agent_name=self.name
            )

        # Check forbidden forms
        violations = ZolaiRules.check_forbidden(text)

        # Check word order hints
        word_order_issues = self._check_word_order(text)

        # Build suggestions
        suggestions = []
        for forbidden, suggested, context in violations:
            suggestions.append(
                {
                    "type": "forbidden_form",
                    "original": forbidden,
                    "corrected": suggested,
                    "context": context,
                }
            )

        for issue in word_order_issues:
            suggestions.append(issue)

        corrected = text
        for forbidden, suggested, _ in violations:
            corrected = re.sub(
                r"\b" + re.escape(forbidden) + r"\b",
                suggested,
                corrected,
                flags=re.IGNORECASE,
            )

        return AgentResult(
            success=True,
            data={
                "text": text,
                "corrected": corrected,
                "violations": [(f, s, c) for f, s, c in violations],
                "suggestions": suggestions,
                "is_compliant": len(violations) == 0,
                "word_order_issues": word_order_issues,
            },
            agent_name=self.name,
        )

    def _check_word_order(self, text: str) -> list[dict]:
        """Basic SOV word order check (simple heuristics)."""
        issues: list[dict] = []
        # Very basic: flag if verb appears before subject in simple sentences
        # This is a placeholder for more sophisticated checking
        return issues
