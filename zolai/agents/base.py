"""
Base agent class for Zolai AI agents.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class AgentResult:
    """Standard result from any Zolai agent."""
    success: bool
    data: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    agent_name: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "data": self.data,
            "errors": self.errors,
            "agent_name": self.agent_name,
            "timestamp": self.timestamp,
        }


class ZolaiAgent(ABC):
    """Base class for all Zolai AI agents."""

    def __init__(self, name: str):
        self.name = name
        self.description = ""

    @abstractmethod
    def process(self, input_data: dict) -> AgentResult:
        """Process input and return result."""
        pass

    def validate_input(self, input_data: dict) -> tuple[bool, list[str]]:
        """Validate input data. Override for custom validation."""
        errors = []
        if not input_data:
            errors.append("Input data is empty")
        return len(errors) == 0, errors

    def run(self, input_data: dict) -> AgentResult:
        """Run agent with validation."""
        valid, errors = self.validate_input(input_data)
        if not valid:
            return AgentResult(
                success=False,
                errors=errors,
                agent_name=self.name,
            )

        try:
            result = self.process(input_data)
            result.agent_name = self.name
            return result
        except Exception as e:
            return AgentResult(
                success=False,
                errors=[f"Agent error: {e}"],
                agent_name=self.name,
            )
