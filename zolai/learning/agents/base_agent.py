"""
Base Agent Class for Zolai Multi-Agent Learning System.

All agents inherit from this base class which provides:
- Database connection and learning signal emission
- Skill management
- Team communication
- Continuous learning from Bible and database
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from sqlalchemy.engine import Engine

from ...data.repositories import get_engine, get_repositories
from ...rag.retrieve import UnifiedRetriever
from ..feedback_store import FeedbackType, LearningSignal, get_feedback_store
from ..bible_pattern_learner import get_bible_learner

logger = logging.getLogger(__name__)


class AgentRole(str, Enum):
    """Roles agents can have in the system."""
    BIBLE_LEARNER = "bible_learner"      # Learns from Bible patterns
    TRANSLATOR = "translator"             # Translates with Bible grounding
    GRAMMAR_EXPERT = "grammar_expert"     # Validates/improves grammar
    VOCABULARY_BUILDER = "vocabulary_builder"  # Builds vocabulary
    TRANSLATION_VALIDATOR = "translation_validator"  # Validates translations
    CONTEXT_ANALYZER = "context_analyzer"  # Analyzes context
    DISCUSSION_FACILITATOR = "discussion_facilitator"  # Facilitates agent discussions
    LEARNING_ORCHESTRATOR = "learning_orchestrator"    # Coordinates learning


class AgentSkill(str, Enum):
    """Skills agents can have."""
    BIBLE_PATTERN_EXTRACTION = "bible_pattern_extraction"
    BIBLE_TRANSLATION_GROUNDING = "bible_translation_grounding"
    POS_TAGGING = "pos_tagging"
    MORPHOLOGY_ANALYSIS = "morphology_analysis"
    SENTENCE_PATTERN_LEARNING = "sentence_pattern_learning"
    TRANSLATION_VALIDATION = "translation_validation"
    GRAMMAR_VALIDATION = "grammar_validation"
    VOCABULARY_EXPANSION = "vocabulary_expansion"
    CONTEXT_ANALYSIS = "context_analysis"
    DISCUSSION_FACILITATION = "discussion_facilitation"
    CONSENSUS_BUILDING = "consensus_building"
    KNOWLEDGE_SYNTHESIS = "knowledge_synthesis"
    ERROR_DETECTION = "error_detection"
    FEEDBACK_INTEGRATION = "feedback_integration"
    CONTINUOUS_LEARNING = "continuous_learning"


@dataclass
class AgentConfig:
    """Configuration for an agent."""
    agent_id: str
    name: str
    role: AgentRole
    skills: list[AgentSkill] = field(default_factory=list)
    team_id: Optional[str] = None
    learning_rate: float = 0.1
    confidence_threshold: float = 0.7
    max_concurrent_tasks: int = 5
    bible_priority: bool = True  # Use Bible as primary source


@dataclass
class AgentMessage:
    """Message between agents."""
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    from_agent: str = ""
    to_agent: Optional[str] = None  # None = broadcast to team
    team_id: Optional[str] = None
    message_type: str = "discussion"  # discussion, task, result, question, consensus
    content: str = ""
    metadata: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    reply_to: Optional[str] = None


@dataclass
class DiscussionContext:
    """Context for agent discussions."""
    topic: str = ""
    participants: list[str] = field(default_factory=list)
    messages: list[AgentMessage] = field(default_factory=list)
    consensus: Optional[str] = None
    status: str = "active"  # active, concluded, archived
    created_at: datetime = field(default_factory=datetime.utcnow)


class BaseAgent(ABC):
    """Base class for all learning agents."""

    def __init__(self, config: AgentConfig, engine: Optional[Engine] = None):
        self.engine = engine or get_engine()
        self.repos = get_repositories(engine=self.engine)
        self.retriever = UnifiedRetriever(self.engine)
        self.bible_learner = get_bible_learner()
        self.feedback_store = get_feedback_store()

        self._bind_config(config)
        
        # State
        self.active_tasks: dict[str, asyncio.Task] = {}
        self.message_queue: asyncio.Queue = asyncio.Queue()
        self.discussion_contexts: dict[str, DiscussionContext] = {}
        self.knowledge_cache: dict[str, Any] = {}
        self.learning_metrics = {
            "tasks_completed": 0,
            "discussions_participated": 0,
            "feedback_given": 0,
            "feedback_received": 0,
            "knowledge_items_learned": 0,
            "errors": 0,
        }
        
        # Learning state
        self.learning_signals_buffer: list = []
        self.discussion_history: list[DiscussionContext] = []

    def _bind_config(self, config: AgentConfig) -> None:
        """Bind a (database-resolved) config onto this agent.

        Called from ``__init__`` and again whenever the registry hands out a
        freshly tuned config for a runtime-created agent.
        """
        self.config = config
        self.agent_id = config.agent_id
        self.name = config.name
        self.role = config.role
        self.skills = config.skills
        self.team_id = config.team_id
        self.learning_rate = config.learning_rate
        self.confidence_threshold = config.confidence_threshold
        self.max_concurrent_tasks = config.max_concurrent_tasks
        self.bible_priority = config.bible_priority

        logger.info(f"Agent {self.name} ({self.role}) initialized with skills: {[s.value for s in self.skills]}")

    @abstractmethod
    async def process_task(self, task: dict[str, Any]) -> dict[str, Any]:
        """Process a task assigned to this agent."""
        pass

    @abstractmethod
    async def handle_message(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Handle incoming message from another agent."""
        pass

    async def start(self):
        """Start the agent's message processing loop."""
        logger.info(f"Agent {self.name} starting")
        while True:
            try:
                message = await asyncio.wait_for(self.message_queue.get(), timeout=1.0)
                await self._process_message(message)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Agent {self.name} error: {e}")
                self.learning_metrics["errors"] += 1

    async def _process_message(self, message: AgentMessage):
        """Process incoming message."""
        response = await self.handle_message(message)
        if response:
            await self.send_message(response)

    async def send_message(self, message: AgentMessage):
        """Send message to another agent or team."""
        # In a real system, this would go through a message bus
        # For now, we'll use a simple callback mechanism
        if hasattr(self, '_message_callback') and self._message_callback:
            await self._message_callback(message)

    def set_message_callback(self, callback):
        """Set callback for message delivery."""
        self._message_callback = callback

    async def emit_learning_signal(
        self,
        query: str,
        response: str,
        feedback_type: FeedbackType,
        confidence: float = 0.5,
        metadata: Optional[dict] = None
    ):
        """Emit a learning signal for the feedback system."""
        signal = LearningSignal(
            session_id=str(uuid.uuid4()),
            user_id=self.agent_id,
            query=query,
            response=response,
            citations=[],  # Would be populated from actual citations
            retrieval_only=False,
            provider="",
            model="",
            engine_mode="hybrid",
            feedback_type=feedback_type,
            confidence=confidence,
            metadata=metadata or {}
        )
        
        self.feedback_store.store_signal(signal)
        self.learning_signals_buffer.append(signal)
        self.learning_metrics["feedback_given"] += 1

    async def learn_from_bible(self, topic: str, limit: int = 10) -> dict[str, Any]:
        """Learn from Bible patterns for a given topic."""
        if not self.bible_priority:
            return {}
            
        # Get Bible context for topic
        bible_context = self.bible_learner.build_context_for_word(topic, limit=5)
        pattern_context = self.bible_learner.build_pattern_context("sov", limit=3)
        
        # Store as knowledge
        knowledge = {
            "topic": topic,
            "bible_context": bible_context,
            "pattern_context": pattern_context,
            "learned_at": datetime.utcnow().isoformat(),
            "source": "bible",
        }
        
        self.knowledge_cache[topic] = knowledge
        self.learning_metrics["knowledge_items_learned"] += 1
        
        return knowledge

    async def get_bible_context_for_translation(self, text: str) -> dict[str, Any]:
        """Get relevant Bible context for translation task."""
        # Extract key words from text
        words = text.lower().split()
        contexts = {}
        
        for word in words[:5]:  # Limit to 5 words
            context = self.bible_learner.build_context_for_word(word, limit=2)
            if "No Bible sentences" not in context:
                contexts[word] = context
                
        return {
            "source_text": text,
            "bible_contexts": contexts,
            "timestamp": datetime.utcnow().isoformat()
        }

    async def validate_with_bible(self, translation: str, source_text: str) -> dict[str, Any]:
        """Validate a translation against Bible patterns."""
        # Get Bible context for key terms
        bible_context = await self.get_bible_context_for_translation(source_text)
        
        # Check against learned patterns
        patterns = self.bible_learner.get_patterns_for_type("sov", limit=5)
        
        validation = {
            "translation": translation,
            "source": source_text,
            "bible_context": bible_context,
            "patterns_checked": len(patterns),
            "is_valid": True,  # Would do actual validation
            "confidence": 0.8,
            "issues": [],
            "suggestions": [],
        }
        
        return validation

    def get_learning_metrics(self) -> dict[str, Any]:
        """Get agent's learning metrics."""
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role.value,
            "skills": [s.value for s in self.skills],
            "metrics": self.learning_metrics,
            "knowledge_cache_size": len(self.knowledge_cache),
            "discussion_history_size": len(self.discussion_history),
        }

    def update_metrics(self, metric: str, value: int = 1):
        """Update a learning metric."""
        if metric in self.learning_metrics:
            self.learning_metrics[metric] += value
        else:
            self.learning_metrics[metric] = value

    async def shutdown(self):
        """Shutdown agent gracefully."""
        logger.info(f"Agent {self.name} shutting down")
        for task in self.active_tasks.values():
            task.cancel()
        await asyncio.gather(*self.active_tasks.values(), return_exceptions=True)


class DiscussionMixin:
    """Mixin for agents that participate in discussions."""

    async def start_discussion(self, topic: str, participants: list[str]) -> DiscussionContext:
        """Start a new discussion."""
        context = DiscussionContext(
            topic=topic,
            participants=participants,
        )
        self.discussion_contexts[topic] = context
        self.discussion_history.append(context)
        self.update_metrics("discussions_participated")
        return context

    async def add_to_discussion(self, topic: str, message: AgentMessage):
        """Add a message to a discussion."""
        if topic in self.discussion_contexts:
            self.discussion_contexts[topic].messages.append(message)

    async def conclude_discussion(self, topic: str, consensus: str) -> DiscussionContext:
        """Conclude a discussion with consensus."""
        if topic in self.discussion_contexts:
            ctx = self.discussion_contexts[topic]
            ctx.consensus = consensus
            ctx.status = "concluded"
            return ctx
        return None

    async def build_consensus(self, topic: str) -> str:
        """Build consensus from discussion messages."""
        if topic not in self.discussion_contexts:
            return ""
            
        ctx = self.discussion_contexts[topic]
        # Simple consensus building - in reality would be more sophisticated
        messages = [m.content for m in ctx.messages if m.from_agent != self.agent_id]
        
        if not messages:
            return "No consensus reached - no other participant messages"
            
        # For now, return summary
        return f"Consensus on '{topic}': {len(messages)} participant messages reviewed. " \
               f"Key points: {'; '.join(m[:100] for m in messages[:3])}"

    async def propose_consensus(self, topic: str, proposal: str) -> AgentMessage:
        """Propose a consensus to the team."""
        return AgentMessage(
            from_agent=self.agent_id,
            to_agent=None,  # Broadcast to team
            team_id=self.team_id,
            message_type="consensus",
            content=f"CONSENSUS PROPOSAL: {proposal}",
            metadata={"topic": topic, "proposer": self.agent_id}
        )
