"""
Teaching Agent — teaches Zolai vocabulary and grammar.
"""
from __future__ import annotations

import random

from ..api.rag_context import get_rag_context
from ..rules.zolai_rules_reference import ZolaiRules
from .base import AgentResult, ZolaiAgent


class TeachingAgent(ZolaiAgent):
    """Teaches Zolai with progressive difficulty and examples."""

    def __init__(self) -> None:
        super().__init__("teaching_agent")
        self.description = "Teaches Zolai vocabulary, grammar, and conversation"

    def process(self, input_data: dict) -> AgentResult:
        action = input_data.get("action", "teach_word")
        word = input_data.get("word", "")
        level = input_data.get("level", "beginner")

        if action == "teach_word":
            return self._teach_word(word, level)
        elif action == "teach_phrase":
            return self._teach_phrase(
                input_data.get("phrase", ""), input_data.get("meaning", "")
            )
        elif action == "teach_grammar":
            return self._teach_grammar(input_data.get("rule", ""))
        elif action == "quiz":
            return self._generate_quiz(input_data.get("topic", "vocabulary"))
        else:
            return AgentResult(
                success=False,
                errors=[f"Unknown action: {action}"],
                agent_name=self.name,
            )

    def _teach_word(self, word: str, level: str) -> AgentResult:
        """Teach a single word with examples."""
        rag = get_rag_context()

        # Get dictionary entry
        dict_results = rag.lookup_dictionary(word, limit=1)

        # Get Bible examples
        bible_examples = rag.find_bible_examples(word, limit=2)

        # Build lesson
        lesson = {
            "word": word,
            "level": level,
            "definition": dict_results[0] if dict_results else None,
            "examples": bible_examples,
            "usage_notes": self._get_usage_notes(word, level),
        }

        return AgentResult(
            success=True,
            data=lesson,
            agent_name=self.name,
        )

    def _teach_phrase(self, phrase: str, meaning: str) -> AgentResult:
        """Teach a phrase with context."""
        return AgentResult(
            success=True,
            data={
                "phrase": phrase,
                "meaning": meaning,
                "type": "phrase",
            },
            agent_name=self.name,
        )

    def _teach_grammar(self, rule: str) -> AgentResult:
        """Teach a grammar rule."""
        rules = ZolaiRules()

        # Get relevant rule info
        rule_info = {
            "forbidden_forms": rules.FORBIDDEN_FORMS,
            "tense_markers": rules.TENSE_MARKERS,
            "negation": rules.NEGATION,
            "particles": rules.PARTICLES,
            "pronouns": rules.PRONOUNS,
        }

        return AgentResult(
            success=True,
            data={
                "rule": rule,
                "info": rule_info.get(rule, {}),
                "summary": rules.get_token_efficient_summary(),
            },
            agent_name=self.name,
        )

    def _generate_quiz(self, topic: str) -> AgentResult:
        """Generate a quiz question."""
        if topic == "vocabulary":
            rag = get_rag_context()
            # Pick a random word from dictionary
            if rag.dict_zo_en:
                entry = random.choice(rag.dict_zo_en)
                return AgentResult(
                    success=True,
                    data={
                        "type": "vocabulary",
                        "question": f"What does '{entry.get('zolai', '')}' mean in English?",
                        "answer": entry.get("english", ""),
                        "hint": f"Part of speech: {entry.get('pos', '')}",
                    },
                    agent_name=self.name,
                )

        return AgentResult(
            success=True,
            data={"type": topic, "question": "Quiz not available for this topic"},
            agent_name=self.name,
        )

    def _get_usage_notes(self, word: str, level: str) -> str:
        """Get usage notes based on level."""
        if level == "beginner":
            return "Common word. Use in simple sentences."
        elif level == "intermediate":
            return "Can be used in compound expressions."
        else:
            return "Advanced usage with nuance."
