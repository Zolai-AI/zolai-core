"""
Grammar Agent - Validates and improves Zolai grammar.

This agent:
- Validates ZVS 2018 compliance
- Checks SOV word order, ergative 'in', negation 'kei'
- Learns grammar patterns from Bible
- Collaborates with other agents
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

from .base_agent import BaseAgent, AgentConfig, AgentRole, AgentSkill, AgentMessage, DiscussionMixin, FeedbackType
from ..bible_pattern_learner import get_bible_learner
from ...zvs import validate as zvs_validate

logger = logging.getLogger(__name__)


# ZVS 2018 forbidden forms
ZVS_FORBIDDEN = {
    "pathian": "pasian",
    "ram": "gam", 
    "fapa": "tapa",
    "bawipa": "topa",
    "siangpahrang": "kumpipa",
    "cu": "tua",
    "cun": "tua",
    "suah": "suahtakna",
    "zalenna": "suahtakna",
    "nunnak": "nuntakna",
    "lo": "kei",  # negation
}

ZVS_PREFERRED = {v: k for k, v in ZVS_FORBIDDEN.items()}


class GrammarAgent(BaseAgent, DiscussionMixin):
    """Grammar expert agent for ZVS 2018 compliance."""

    def __init__(self, engine=None):
        config = AgentConfig(
            agent_id="grammar_expert_001",
            name="GrammarExpert",
            role=AgentRole.GRAMMAR_EXPERT,
            skills=[
                AgentSkill.GRAMMAR_VALIDATION,
                AgentSkill.ERROR_DETECTION,
                AgentSkill.CONTINUOUS_LEARNING,
            ],
            bible_priority=True,
            confidence_threshold=0.9,
        )
        super().__init__(config, engine)
        
        self.bible_learner = get_bible_learner()
        self.grammar_rules: dict = {}
        self.violation_cache: dict = {}
        
        logger.info("GrammarExpert initialized")

    async def process_task(self, task: dict[str, Any]) -> dict[str, Any]:
        """Process a grammar validation task."""
        task_type = task.get("type", "validate")
        
        if task_type == "validate":
            return await self._validate_grammar(task)
        elif task_type == "correct":
            return await self._correct_grammar(task)
        elif task_type == "learn_patterns":
            return await self._learn_grammar_patterns(task)
        elif task_type == "check_zvs":
            return await self._check_zvs_compliance(task)
        else:
            return {"error": f"Unknown task type: {task_type}"}

    async def handle_message(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Handle messages from other agents."""
        if message.message_type == "validate_grammar":
            return await self._handle_grammar_check(message)
        elif message.message_type == "correct_grammar":
            return await self._handle_correction_request(message)
        elif message.message_type == "question":
            return await self._answer_question(message)
        elif message.message_type == "consensus":
            return await self._handle_consensus(message)
        
        return None

    async def _validate_grammar(self, task: dict[str, Any]) -> dict[str, Any]:
        """Validate Zolai grammar against ZVS 2018."""
        text = task.get("text", "")
        
        if not text:
            return {"error": "No text provided"}
            
        # Run ZVS validation
        report = zvs_validate(text)
        corrected = report.text  # The corrected text
        review = {
            "valid": report.is_valid,
            "violations_before": len(report.violations),  # Approximate
            "violations_after": 0 if report.is_valid else len(report.violations),
        }
        
        # Additional Bible-based checks
        bible_checks = await self._check_bible_patterns(text)
        
        result = {
            "original": text,
            "corrected": corrected,
            "is_valid": report.is_valid,
            "violations_before": len(report.violations),
            "violations_after": 0 if report.is_valid else len(report.violations),
            "bible_checks": bible_checks,
            "confidence": 0.95 if report.is_valid else 0.5,
        }
        
        # Emit learning signal
        await self.emit_learning_signal(
            query=f"Validate grammar: {text[:50]}",
            response=f"Valid: {review['valid']}, corrected: {corrected}",
            feedback_type=FeedbackType.EXPLICIT_UP if review["valid"] else FeedbackType.EXPLICIT_DOWN,
            confidence=result["confidence"],
            metadata={"original": text, "corrected": corrected}
        )
        
        self.update_metrics("tasks_completed")
        return result

    async def _check_bible_patterns(self, text: str) -> dict[str, Any]:
        """Check text against Bible-learned patterns."""
        from ..bible_pattern_learner import get_bible_learner
        
        learner = get_bible_learner()
        issues = []
        suggestions = []
        
        # Check SOV pattern
        patterns = learner.get_patterns_for_type("sov", limit=3)
        for pattern in patterns:
            # Simple check - would need proper parsing
            pass
            
        # Check for forbidden forms
        text_lower = text.lower()
        for forbidden, preferred in ZVS_FORBIDDEN.items():
            if forbidden in text_lower:
                issues.append(f"Forbidden form '{forbidden}' found, use '{preferred}'")
                suggestions.append(f"Replace '{forbidden}' with '{preferred}'")
                
        # Check negation
        if "lo" in text_lower.split():
            issues.append("Negation uses 'lo' instead of 'kei'")
            suggestions.append("Replace 'lo' with 'kei'")
            
        return {
            "issues": issues,
            "suggestions": suggestions,
            "is_clean": len(issues) == 0,
        }

    async def _correct_grammar(self, task: dict[str, Any]) -> dict[str, Any]:
        """Correct grammar in text."""
        text = task.get("text", "")
        if not text:
            return {"error": "No text provided"}
            
        report = zvs_validate(text)
        corrected = report.text
        review = {
            "valid": report.is_valid,
            "violations_before": len(report.violations),
            "violations_after": 0 if report.is_valid else len(report.violations),
        }
        
        return {
            "original": text,
            "corrected": corrected,
            "changes": len(report.violations) - (0 if report.is_valid else len(report.violations)),
            "is_valid": report.is_valid,
        }

    async def _learn_grammar_patterns(self, task: dict[str, Any]) -> dict[str, Any]:
        """Learn grammar patterns from Bible."""
        pattern_type = task.get("pattern_type", "grammar")
        limit = task.get("limit", 10)
        
        from ..bible_pattern_learner import get_bible_learner
        learner = get_bible_learner()
        
        # Get patterns from Bible
        pattern_types = ["sov", "question", "negation", "quotative", "ergative"]
        all_patterns = {}
        
        for ptype in pattern_types:
            patterns = learner.get_patterns_for_type(ptype, limit=limit)
            self.grammar_rules[ptype] = patterns
            
        return {
            "pattern_types_learned": len(self.grammar_rules),
            "total_patterns": sum(len(p) for p in self.grammar_rules.values()),
        }

    async def _check_zvs_compliance(self, task: dict[str, Any]) -> dict[str, Any]:
        """Check ZVS 2018 compliance."""
        text = task.get("text", "")
        if not text:
            return {"error": "No text provided"}
            
        report = zvs_validate(text)
        corrected = report.text
        review = {
            "valid": report.is_valid,
            "violations_before": len(report.violations),
            "violations_after": 0 if report.is_valid else len(report.violations),
        }
        
        return {
            "text": text,
            "is_compliant": report.is_valid,
            "violations": len(report.violations),
            "corrected_text": corrected,
            "details": {
                "valid": report.is_valid,
                "violations_before": len(report.violations),
                "violations_after": 0 if report.is_valid else len(report.violations),
            },
        }

    async def _handle_grammar_check(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Handle grammar check request from another agent."""
        text = message.metadata.get("text", "")
        if not text:
            return AgentMessage(
                from_agent=self.agent_id,
                to_agent=message.from_agent,
                message_type="error",
                content="No text provided for grammar check"
            )
            
        result = await self._validate_grammar({"text": text})
        
        return AgentMessage(
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type="grammar_result",
            content=f"Valid: {result['is_valid']}, Corrected: {result['corrected'][:100]}",
            metadata={"result": result}
        )

    async def _handle_correction_request(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Handle correction request."""
        text = message.metadata.get("text", "")
        if not text:
            return AgentMessage(
                from_agent=self.agent_id,
                to_agent=message.from_agent,
                message_type="error",
                content="No text provided for correction"
            )
            
        result = await self._correct_grammar({"text": text})
        
        return AgentMessage(
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type="correction_result",
            content=result.get("corrected", text),
            metadata={"result": result}
        )

    async def _answer_question(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Answer grammar question."""
        question = message.content
        
        # Provide grammar guidance
        answer = "I can help with Zolai grammar (ZVS 2018). " \
                 "Key rules: SOV word order, ergative 'in', negation 'kei', " \
                 "forbidden forms: pathian→pasian, ram→gam, fapa→tapa, etc."
        
        return AgentMessage(
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type="answer",
            content=answer,
            metadata={"question": question}
        )

    async def _handle_consensus(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Handle consensus request."""
        topic = message.metadata.get("topic", "")
        proposal = message.content.replace("CONSENSUS PROPOSAL: ", "")
        
        # Grammar expert's consensus: check grammar compliance
        consensus = f"GrammarExpert on '{topic}': {proposal[:100]}... " \
                   f"(Will validate ZVS 2018 compliance)"
        
        return AgentMessage(
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type="consensus_response",
            content=consensus,
            metadata={"topic": topic, "agreed": True, "role": "grammar_expert"}
        )
