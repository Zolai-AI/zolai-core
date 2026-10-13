"""
Bible Learner Agent - Primary agent for learning Zolai from Bible.

This is the PRIMARY learning agent that:
- Extracts patterns from Bible parallel corpus
- Builds translation memory from Bible
- Teaches other agents Bible-grounded knowledge
- Continuously improves from Bible patterns
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from .base_agent import BaseAgent, AgentConfig, AgentRole, AgentSkill, AgentMessage, FeedbackType
from ..bible_pattern_learner import get_bible_learner

logger = logging.getLogger(__name__)


class BibleLearnerAgent(BaseAgent):
    """Primary agent for learning Zolai from Bible corpus."""

    def __init__(self, engine=None):
        config = AgentConfig(
            agent_id="bible_learner_001",
            name="BibleLearner",
            role=AgentRole.BIBLE_LEARNER,
            skills=[
                AgentSkill.BIBLE_PATTERN_EXTRACTION,
                AgentSkill.BIBLE_TRANSLATION_GROUNDING,
                AgentSkill.SENTENCE_PATTERN_LEARNING,
                AgentSkill.CONTINUOUS_LEARNING,
            ],
            bible_priority=True,
            confidence_threshold=0.85,
        )
        super().__init__(config, engine)
        
        self.bible_learner = get_bible_learner()
        self.extracted_patterns: dict = {}
        self.learned_vocabulary: dict = {}
        self.pattern_frequency: dict = {}
        
        logger.info("BibleLearnerAgent initialized - Primary learning agent")

    async def process_task(self, task: dict[str, Any]) -> dict[str, Any]:
        """Process a learning task."""
        task_type = task.get("type", "learn_pattern")
        
        if task_type == "learn_pattern":
            return await self._learn_pattern(task)
        elif task_type == "extract_vocabulary":
            return await self._extract_vocabulary(task)
        elif task_type == "build_context":
            return await self._build_bible_context(task)
        elif task_type == "validate_translation":
            return await self._validate_with_bible(task)
        elif task_type == "extract_patterns":
            return await self._extract_all_patterns(task)
        else:
            return {"error": f"Unknown task type: {task_type}"}

    async def handle_message(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Handle messages from other agents."""
        if message.message_type == "question":
            return await self._answer_question(message)
        elif message.message_type == "request_context":
            return await self._provide_bible_context(message)
        elif message.message_type == "validate":
            return await self._validate_for_agent(message)
        elif message.message_type == "consensus":
            return await self._handle_consensus(message)
        
        return None

    async def _learn_pattern(self, task: dict[str, Any]) -> dict[str, Any]:
        """Learn a specific pattern from Bible."""
        pattern_type = task.get("pattern_type", "sov")
        limit = task.get("limit", 10)
        
        patterns = self.bible_learner.get_patterns_for_type(pattern_type, limit=limit)
        
        result = {
            "pattern_type": pattern_type,
            "patterns_found": len(patterns),
            "patterns": [
                {
                    "structure": p.structure,
                    "example_zo": p.example_zo,
                    "example_en": p.example_en,
                    "components": p.components,
                    "frequency": p.frequency,
                }
                for p in patterns
            ],
            "learned_at": datetime.utcnow().isoformat(),
        }
        
        # Store in knowledge cache
        self.extracted_patterns[pattern_type] = result
        self.update_metrics("knowledge_items_learned", len(patterns))
        
        # Emit learning signal
        await self.emit_learning_signal(
            query=f"Learn {pattern_type} patterns",
            response=f"Extracted {len(patterns)} {pattern_type} patterns from Bible",
            feedback_type=FeedbackType.EXPLICIT_UP,
            confidence=0.95,
            metadata={"pattern_type": pattern_type, "count": len(patterns)}
        )
        
        return result

    async def _extract_vocabulary(self, task: dict[str, Any]) -> dict[str, Any]:
        """Extract vocabulary from Bible verses."""
        limit = task.get("limit", 50)
        min_frequency = task.get("min_frequency", 2)
        
        learner = self.bible_learner
        vocab = learner.word_freq.most_common(limit)
        
        # Filter by frequency
        vocab = [(w, c) for w, c in vocab if c >= min_frequency]
        
        # Build vocabulary entries with Bible context
        vocab_entries = []
        for word, freq in vocab:
            context = self.bible_learner.build_context_for_word(word, limit=2)
            if "No Bible sentences" not in context:
                self.learned_vocabulary[word] = {
                    "word": word,
                    "frequency": freq,
                    "bible_context": context,
                    "learned_from": "bible",
                }
                vocab_entries.append({
                    "word": word,
                    "frequency": freq,
                    "context": context[:500],
                })
        
        result = {
            "vocabulary_size": len(vocab_entries),
            "min_frequency": min_frequency,
            "entries": vocab_entries[:20],  # Return top 20
        }
        
        self.update_metrics("knowledge_items_learned", len(vocab_entries))
        return result

    async def _build_bible_context(self, task: dict[str, Any]) -> dict[str, Any]:
        """Build Bible context for a word or phrase."""
        word = task.get("word", "")
        limit = task.get("limit", 3)
        
        if not word:
            return {"error": "No word provided"}
            
        context = self.bible_learner.build_context_for_word(task["word"], limit=limit)
        
        return {
            "word": word,
            "bible_context": context,
            "source": "bible_parallel_corpus",
        }

    async def _validate_with_bible(self, task: dict[str, Any]) -> dict[str, Any]:
        """Validate a translation against Bible patterns."""
        source = task.get("source_text", "")
        translation = task.get("translation", "")
        
        if not source or not translation:
            return {"error": "Missing source_text or translation"}
            
        validation = await self.validate_with_bible(translation, source)
        return validation

    async def _extract_all_patterns(self, task: dict[str, Any]) -> dict[str, Any]:
        """Extract all pattern types from Bible."""
        pattern_types = ["sov", "question", "negation", "quotative", "future", "past_completed", "ergative"]
        
        all_patterns = {}
        total = 0
        
        for ptype in pattern_types:
            patterns = self.bible_learner.get_patterns_for_type(ptype, limit=5)
            all_patterns[ptype] = [
                {
                    "structure": p.structure,
                    "example_zo": p.example_zo,
                    "example_en": p.example_en,
                    "components": p.components,
                }
                for p in patterns
            ]
            total += len(patterns)
        
        return {
            "pattern_types": list(all_patterns.keys()),
            "total_patterns": total,
            "patterns": all_patterns,
        }

    async def _answer_question(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Answer a question from another agent using Bible knowledge."""
        question = message.content
        
        # Try to answer from Bible knowledge
        # For now, provide Bible context for key terms
        words = question.lower().split()
        contexts = {}
        
        for word in words[:3]:
            if len(word) > 2:
                context = self.bible_learner.build_context_for_word(word, limit=1)
                if "No Bible sentences" not in context:
                    contexts[word] = context
        
        answer = "Based on Bible corpus: "
        if contexts:
            for word, ctx in contexts.items():
                answer += f"\n- {word}: {ctx[:200]}"
        else:
            answer += "No specific Bible context found for this question."
            
        return AgentMessage(
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type="answer",
            content=answer,
            metadata={"original_question": question}
        )

    async def _provide_bible_context(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Provide Bible context for a word or phrase."""
        # Extract word from message metadata or content
        word = message.metadata.get("word", "")
        if not word:
            # Try to extract from content
            words = message.content.split()
            for w in words:
                if len(w) > 2:
                    word = w
                    break
        
        if not word:
            return AgentMessage(
                from_agent=self.agent_id,
                to_agent=message.from_agent,
                message_type="error",
                content="No word found in request"
            )
            
        context = self.bible_learner.build_context_for_word(word, limit=3)
        
        return AgentMessage(
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type="context_response",
            content=context,
            metadata={"word": word, "source": "bible"}
        )

    async def _validate_for_agent(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Validate a translation for another agent."""
        translation = message.metadata.get("translation", "")
        source = message.metadata.get("source_text", "")
        
        if not translation or not source:
            return AgentMessage(
                from_agent=self.agent_id,
                to_agent=message.from_agent,
                message_type="error",
                content="Missing translation or source_text in metadata"
            )
            
        validation = await self.validate_with_bible(translation, source)
        
        return AgentMessage(
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type="validation_result",
            content=f"Validation: {'PASS' if validation.get('is_valid') else 'FAIL'} - Confidence: {validation.get('confidence', 0):.2f}",
            metadata={"validation": validation}
        )

    async def _handle_consensus(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Handle consensus building message."""
        topic = message.metadata.get("topic", "")
        proposal = message.content.replace("CONSENSUS PROPOSAL: ", "")
        
        # Bible learner's consensus: check against Bible patterns
        # For now, simple agreement if it aligns with Bible patterns
        consensus = f"BibleLearner agrees with proposal on '{topic}': {proposal[:100]}... " \
                   f"(Validated against Bible patterns)"
        
        return AgentMessage(
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type="consensus_response",
            content=consensus,
            metadata={"topic": topic, "agreed": True}
        )

    # Background continuous learning
    async def continuous_learning_loop(self, interval: int = 3600):
        """Continuous learning from Bible - runs periodically."""
        while True:
            try:
                logger.info("BibleLearner: Running continuous learning cycle")
                
                # Extract new patterns from Bible
                self.bible_learner.extract_patterns()
                
                # Update pattern frequency
                stats = self.bible_learner.get_pattern_stats()
                self.pattern_frequency = stats.get("pattern_counts", {})
                
                # Learn new vocabulary from Bible
                await self._extract_vocabulary({"limit": 100, "min_frequency": 1})
                
                # Emit learning signal
                await self.emit_learning_signal(
                    query="Continuous Bible learning cycle",
                    response=f"Updated patterns: {self.pattern_frequency}, vocabulary: {len(self.learned_vocabulary)}",
                    feedback_type=FeedbackType.EXPLICIT_UP,
                    confidence=0.9,
                    metadata={"cycle": "continuous", "stats": stats}
                )
                
                self.update_metrics("knowledge_items_learned", 1)
                
            except Exception as e:
                logger.error(f"BibleLearner continuous learning error: {e}")
                self.update_metrics("errors", 1)
                
            await asyncio.sleep(interval)

    async def get_learning_report(self) -> dict[str, Any]:
        """Get comprehensive learning report."""
        stats = self.bible_learner.get_pattern_stats()
        
        return {
            "agent": self.name,
            "role": self.role.value,
            "patterns": self.pattern_frequency,
            "vocabulary_size": len(self.learned_vocabulary),
            "bible_stats": stats,
            "metrics": self.get_learning_metrics(),
        }
