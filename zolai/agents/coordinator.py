"""
Agent Coordinator — orchestrates multiple Zolai agents.
"""
from __future__ import annotations

from .base import AgentResult, ZolaiAgent
from .data_quality import DataQualityAgent
from .grammar import GrammarAgent
from .teacher import TeachingAgent
from .translator import TranslationAgent


class AgentCoordinator(ZolaiAgent):
    """Coordinates multiple Zolai agents for complex tasks."""

    def __init__(self) -> None:
        super().__init__("coordinator")
        self.description = "Orchestrates multiple agents for complex Zolai tasks"
        self.agents: dict[str, ZolaiAgent] = {
            "grammar": GrammarAgent(),
            "translator": TranslationAgent(),
            "teacher": TeachingAgent(),
            "data_quality": DataQualityAgent(),
        }

    def process(self, input_data: dict) -> AgentResult:
        task = input_data.get("task", "translate_and_check")

        if task == "translate_and_check":
            return self._translate_and_check(input_data)
        elif task == "teach_with_context":
            return self._teach_with_context(input_data)
        elif task == "validate_and_fix":
            return self._validate_and_fix(input_data)
        elif task == "full_analysis":
            return self._full_analysis(input_data)
        else:
            return AgentResult(
                success=False,
                errors=[f"Unknown task: {task}"],
                agent_name=self.name,
            )

    def _translate_and_check(self, input_data: dict) -> AgentResult:
        """Translate text and check grammar."""
        text = input_data.get("text", "")

        # Step 1: Translate
        trans_result = self.agents["translator"].run(
            {
                "text": text,
                "direction": input_data.get("direction", "auto"),
            }
        )

        # Step 2: Check grammar on original
        grammar_result = self.agents["grammar"].run({"text": text})

        # Step 3: Score accuracy
        words = text.split()
        accuracy_result = self.agents["data_quality"].run(
            {
                "action": "score_accuracy",
                "word": words[0] if words else "",
            }
        )

        return AgentResult(
            success=trans_result.success and grammar_result.success,
            data={
                "translation": trans_result.data,
                "grammar": grammar_result.data,
                "accuracy": accuracy_result.data,
            },
            agent_name=self.name,
        )

    def _teach_with_context(self, input_data: dict) -> AgentResult:
        """Teach a word/phrase with full context."""
        word = input_data.get("word", "")

        # Step 1: Get teaching
        teach_result = self.agents["teacher"].run(
            {
                "action": "teach_word",
                "word": word,
                "level": input_data.get("level", "beginner"),
            }
        )

        # Step 2: Check grammar
        grammar_result = self.agents["grammar"].run({"text": word})

        # Step 3: Score accuracy
        accuracy_result = self.agents["data_quality"].run(
            {
                "action": "score_accuracy",
                "word": word,
            }
        )

        return AgentResult(
            success=True,
            data={
                "lesson": teach_result.data,
                "grammar": grammar_result.data,
                "accuracy": accuracy_result.data,
            },
            agent_name=self.name,
        )

    def _validate_and_fix(self, input_data: dict) -> AgentResult:
        """Validate data and suggest fixes."""
        text = input_data.get("text", "")

        # Step 1: Validate
        validate_result = self.agents["data_quality"].run(
            {
                "action": "check_consistency",
                "text": text,
            }
        )

        # Step 2: Fix grammar if needed
        grammar_result = self.agents["grammar"].run({"text": text})

        return AgentResult(
            success=True,
            data={
                "validation": validate_result.data,
                "grammar_fix": grammar_result.data,
                "corrected": grammar_result.data.get("corrected", text),
            },
            agent_name=self.name,
        )

    def _full_analysis(self, input_data: dict) -> AgentResult:
        """Full analysis of text: translate, check grammar, score accuracy."""
        text = input_data.get("text", "")

        results: dict = {}
        for name, agent in self.agents.items():
            if name == "data_quality":
                result = agent.run({"text": text, "action": "check_consistency"})
            else:
                result = agent.run({"text": text})
            results[name] = result.data

        return AgentResult(
            success=True,
            data=results,
            agent_name=self.name,
        )

    def get_agent(self, name: str) -> ZolaiAgent | None:
        return self.agents.get(name)

    def list_agents(self) -> list[dict]:
        return [
            {"name": name, "description": agent.description}
            for name, agent in self.agents.items()
        ]
