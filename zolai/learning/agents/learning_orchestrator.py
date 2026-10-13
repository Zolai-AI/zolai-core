"""
Learning Orchestrator - Coordinates all learning agents and manages the learning pipeline.

This is the main entry point for the multi-agent learning system.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .base_agent import BaseAgent, AgentConfig, AgentMessage, AgentRole
from .agent_team import AgentTeamManager, DiscussionFacilitator, create_standard_learning_team
from .bible_learner_agent import BibleLearnerAgent
from .translation_agent import TranslationAgent
from .grammar_agent import GrammarAgent
from .vocabulary_agent import VocabularyAgent
from ..feedback_store import get_feedback_store, FeedbackType
from ..bible_pattern_learner import get_bible_learner
from ...data.repositories import get_engine as get_data_engine

logger = logging.getLogger(__name__)


@dataclass
class LearningTask:
    """A learning task to be processed by agents."""
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = ""
    priority: int = 5  # 1=highest, 10=lowest
    assigned_agent: Optional[str] = None
    team_id: Optional[str] = None
    payload: dict = field(default_factory=dict)
    status: str = "pending"  # pending, assigned, in_progress, completed, failed
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[dict] = None
    error: Optional[str] = None


def _get_orchestrator_engine(engine=None):
    """Get or create an engine for the orchestrator."""
    if engine is not None:
        return engine
    # Use the data repository's engine factory
    return get_data_engine()


class LearningOrchestrator:
    """Main orchestrator for the multi-agent learning system."""
    
    def __init__(self, engine=None):
        self.engine = _get_orchestrator_engine(engine)
        self.team_manager = AgentTeamManager(self.engine)
        self.discussion_facilitator = DiscussionFacilitator(self.team_manager)
        self.feedback_store = get_feedback_store()
        
        # Create standard team with proper engine
        self.team = create_standard_learning_team(self.team_manager, self.engine)
        
        # Task queue
        self.task_queue: asyncio.Queue = asyncio.Queue()
        self.active_tasks: dict[str, LearningTask] = {}
        self.completed_tasks: list[LearningTask] = []
        
        # Continuous learning state
        self.continuous_learning_active = False
        self.learning_cycle_interval = 3600  # 1 hour default
        
        logger.info("LearningOrchestrator initialized with standard team")

    async def start(self):
        """Start the orchestrator and all agents (idempotent)."""
        if self.continuous_learning_active:
            return
            
        logger.info("Starting LearningOrchestrator")
        
        # Start all agents
        for agent in self.team_manager.agents.values():
            asyncio.create_task(agent.start())
        
        # Start task processor
        asyncio.create_task(self._process_task_queue())
        
        # Start continuous learning
        self.continuous_learning_active = True
        asyncio.create_task(self._continuous_learning_loop())
        
        logger.info("LearningOrchestrator started successfully")

    async def _process_task_queue(self):
        """Process tasks from the queue."""
        while True:
            try:
                task = await asyncio.wait_for(self.task_queue.get(), timeout=1.0)
                await self._execute_task(task)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Task processor error: {e}")

    async def _execute_task(self, task: LearningTask):
        """Execute a single task."""
        task.status = "in_progress"
        task.started_at = datetime.utcnow()
        self.active_tasks[task.task_id] = task
        
        try:
            # Determine which agent should handle this task
            agent = await self._assign_agent(task)
            if not agent:
                task.status = "failed"
                task.error = "No suitable agent found"
                return
                
            task.assigned_agent = agent.agent_id
            
            # Execute task
            result = await agent.process_task(task.payload)
            task.result = result
            task.status = "completed"
            task.completed_at = datetime.utcnow()
            
        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            logger.error(f"Task {task.task_id} failed: {e}")
        finally:
            self.active_tasks.pop(task.task_id, None)
            self.completed_tasks.append(task)

    async def _assign_agent(self, task: LearningTask) -> Optional[BaseAgent]:
        """Assign the best agent for a task."""
        task_type = task.type
        
        # Direct role mapping
        role_mapping = {
            "learn_pattern": AgentRole.BIBLE_LEARNER,
            "extract_vocabulary": AgentRole.BIBLE_LEARNER,
            "build_context": AgentRole.BIBLE_LEARNER,
            "validate_translation": AgentRole.BIBLE_LEARNER,
            "extract_patterns": AgentRole.BIBLE_LEARNER,
            "translate": AgentRole.TRANSLATOR,
            "ground_with_bible": AgentRole.TRANSLATOR,
            "validate": AgentRole.GRAMMAR_EXPERT,
            "correct": AgentRole.GRAMMAR_EXPERT,
            "learn_patterns": AgentRole.GRAMMAR_EXPERT,
            "check_zvs": AgentRole.GRAMMAR_EXPERT,
            "build_vocabulary": AgentRole.VOCABULARY_BUILDER,
            "extract_collocations": AgentRole.VOCABULARY_BUILDER,
            "find_relationships": AgentRole.VOCABULARY_BUILDER,
            "get_definition": AgentRole.VOCABULARY_BUILDER,
            "expand_vocabulary": AgentRole.VOCABULARY_BUILDER,
        }
        
        target_role = role_mapping.get(task_type)
        if not target_role:
            # Default to Bible learner for learning tasks
            target_role = AgentRole.BIBLE_LEARNER
            
        # Find agent with matching role in the team
        for agent_id in self.team.members:
            agent = self.team_manager.agents.get(agent_id)
            if agent and agent.config.role == target_role:
                return agent
                
        # Fallback: return any available agent
        for agent_id in self.team.members:
            agent = self.team_manager.agents.get(agent_id)
            if agent:
                return agent
                
        return None

    async def submit_task(self, task_type: str, payload: dict, 
                         priority: int = 5, team_id: str = None) -> str:
        """Submit a new learning task."""
        task = LearningTask(
            type=task_type,
            priority=priority,
            team_id=team_id or self.team.team_id,
            payload=payload,
        )
        
        await self.task_queue.put(task)
        return task.task_id

    async def submit_and_wait(self, task_type: str, payload: dict, 
                             timeout: float = 30.0) -> Optional[dict]:
        """Submit a task and wait for result."""
        # Ensure orchestrator is started
        if not self.continuous_learning_active:
            await self.start()
        
        task_id = await self.submit_task(task_type, payload)
        
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout:
            # Check completed tasks
            for task in self.completed_tasks:
                if task.task_id == task_id:
                    return task.result
            # Check active tasks
            if task_id in self.active_tasks:
                task = self.active_tasks[task_id]
                if task.status == "completed":
                    return task.result
                elif task.status == "failed":
                    raise Exception(f"Task failed: {task.error}")
            await asyncio.sleep(0.1)
            
        raise TimeoutError(f"Task {task_id} did not complete within {timeout}s")

    # High-level API methods
    async def translate(self, text: str, target_lang: str = "en", 
                       source_lang: str = "zo") -> dict:
        """Translate text using the translation agent."""
        return await self.submit_and_wait("translate", {
            "source_text": text,
            "target_lang": target_lang,
            "source_lang": source_lang,
        })

    async def validate_grammar(self, text: str) -> dict:
        """Validate grammar using grammar expert."""
        return await self.submit_and_wait("validate", {"text": text})

    async def correct_grammar(self, text: str) -> dict:
        """Correct grammar using grammar expert."""
        return await self.submit_and_wait("correct", {"text": text})

    async def build_vocabulary(self, limit: int = 100, min_frequency: int = 1) -> dict:
        """Build vocabulary from Bible."""
        return await self.submit_and_wait("build_vocabulary", {
            "limit": limit,
            "min_frequency": min_frequency,
        })

    async def extract_collocations(self, limit: int = 100) -> dict:
        """Extract collocations from Bible."""
        return await self.submit_and_wait("extract_collocations", {"limit": limit})

    async def learn_bible_patterns(self, pattern_type: str = "sov", limit: int = 10) -> dict:
        """Learn patterns from Bible."""
        return await self.submit_and_wait("learn_pattern", {
            "pattern_type": pattern_type,
            "limit": limit,
        })

    async def validate_translation(self, source: str, translation: str) -> dict:
        """Validate a translation."""
        return await self.submit_and_wait("validate", {
            "source_text": source,
            "translation": translation,
        })

    async def get_bible_context(self, word: str, limit: int = 3) -> dict:
        """Get Bible context for a word."""
        return await self.submit_and_wait("build_context", {"word": word, "limit": limit})

    async def start_discussion(self, topic: str, initial_message: str = "") -> DiscussionContext:
        """Start a discussion among team members."""
        # Use Bible learner as initiator (first member)
        initiator = self.team.members[0] if self.team.members else None
        if not initiator:
            raise ValueError("No team members available")
            
        return await self.discussion_facilitator.start_discussion(
            team_id=self.team.team_id,
            topic=topic,
            initiator_id=initiator,
            initial_message=initial_message
        )

    async def get_consensus(self, topic: str) -> str:
        """Get consensus from a discussion."""
        return await self.discussion_facilitator.build_consensus(topic)

    async def conclude_discussion(self, topic: str, consensus: str = "") -> DiscussionContext:
        """Conclude a discussion."""
        initiator = self.team.members[0] if self.team.members else None
        return await self.discussion_facilitator.conclude_discussion(topic, initiator, consensus)

    async def _continuous_learning_loop(self):
        """Continuous learning loop - runs periodically."""
        while self.continuous_learning_active:
            try:
                logger.info("Orchestrator: Running continuous learning cycle")
                
                # 1. Bible learner continuous learning
                bible_learner = self.team_manager.agents.get("bible_learner_001")
                if bible_learner:
                    await bible_learner.continuous_learning_loop(interval=0)  # Run once
                
                # 2. Process feedback signals
                await self._process_feedback_signals()
                
                # 3. Update team knowledge
                await self._update_team_knowledge()
                
                # 4. Check for model improvement opportunities
                await self._check_improvement_opportunities()
                
            except Exception as e:
                logger.error(f"Continuous learning error: {e}")
                
            await asyncio.sleep(self.learning_cycle_interval)

    async def _process_feedback_signals(self):
        """Process feedback signals from the feedback store."""
        feedback_store = get_feedback_store()
        
        # Get pending review signals
        pending = feedback_store.get_pending_review(limit=10)
        
        for signal_data in pending:
            # Process based on feedback type
            feedback_type = signal_data.get("feedback_type")
            
            if feedback_type == "explicit_down":
                # Create correction task
                await self.submit_task("correct", {
                    "text": signal_data.get("query", "")
                }, priority=2)
                
            elif feedback_type == "correction":
                # Learn from correction
                await self.submit_task("learn_from_correction", {
                    "original": signal_data.get("query", ""),
                    "corrected": signal_data.get("feedback_value", ""),
                }, priority=1)

    async def _update_team_knowledge(self):
        """Share knowledge across team members."""
        # Bible learner shares new patterns with team
        bible_learner = self.team_manager.agents.get("bible_learner_001")
        if bible_learner and bible_learner.extracted_patterns:
            for pattern_type, patterns in bible_learner.extracted_patterns.items():
                # Broadcast to team
                await self.team_manager.broadcast_to_team(
                    "bible_learner_001", self.team.team_id, "knowledge_update",
                    f"New {pattern_type} patterns learned",
                    metadata={"pattern_type": pattern_type, "patterns": patterns}
                )

    async def _check_improvement_opportunities(self):
        """Check for model improvement opportunities."""
        # Get feedback statistics
        # This would integrate with the feedback store to identify areas needing improvement
        pass

    def get_system_status(self) -> dict[str, Any]:
        """Get comprehensive system status."""
        return {
            "orchestrator": "running" if self.continuous_learning_active else "stopped",
            "team": {
                "team_id": self.team.team_id,
                "name": self.team.name,
                "members": self.team.members,
                "member_count": len(self.team.members),
            },
            "agents": {
                agent_id: agent.get_learning_metrics() 
                for agent_id, agent in self.team_manager.agents.items()
            },
            "tasks": {
                "queued": self.task_queue.qsize(),
                "active": len(self.active_tasks),
                "completed": len(self.completed_tasks),
            },
            "learning_cycle_interval": self.learning_cycle_interval,
            "continuous_learning": self.continuous_learning_active,
        }

    async def shutdown(self):
        """Shutdown the orchestrator and all agents."""
        logger.info("Shutting down LearningOrchestrator")
        self.continuous_learning_active = False
        
        for agent in self.team_manager.agents.values():
            await agent.shutdown()
            
        logger.info("LearningOrchestrator shutdown complete")


# Global instance
_orchestrator: Optional[LearningOrchestrator] = None


def get_learning_orchestrator(engine=None) -> LearningOrchestrator:
    """Get or create the global learning orchestrator."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = LearningOrchestrator(engine)
    return _orchestrator
