"""
Multi-Agent Learning System for Zolai Language.

Bible-first, database-driven, multi-agent system that:
- Learns Zolai from Bible parallel corpus (primary source)
- Uses multi-agent teams with specialized skills
- Continuous learning from database interactions
- Agent discussions and collaborative learning
- Auto-improvement without external code
"""

from .base_agent import (
    BaseAgent, 
    AgentSkill, 
    AgentRole, 
    AgentMessage,
    DiscussionContext,
    AgentConfig,
)

from .bible_learner_agent import BibleLearnerAgent
from .translation_agent import TranslationAgent
from .grammar_agent import GrammarAgent
from .vocabulary_agent import VocabularyAgent
from .agent_team import (
    AgentTeamManager, 
    DiscussionFacilitator, 
    AgentTeam,
    create_standard_learning_team,
)
from .learning_orchestrator import (
    LearningOrchestrator,
    LearningTask,
    get_learning_orchestrator,
)

__all__ = [
    # Base
    "BaseAgent",
    "AgentSkill", 
    "AgentRole",
    "AgentMessage",
    "DiscussionContext",
    "AgentConfig",
    
    # Agents
    "BibleLearnerAgent",
    "TranslationAgent", 
    "GrammarAgent",
    "VocabularyAgent",
    
    # Team & Discussion
    "AgentTeamManager",
    "DiscussionFacilitator",
    "AgentTeam",
    "create_standard_learning_team",
    
    # Orchestrator
    "LearningOrchestrator",
    "LearningTask",
    "get_learning_orchestrator",
]

