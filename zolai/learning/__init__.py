"""
Learning Package for Zolai Language.

Multi-agent learning system with Bible-first approach.
"""

from .bible_pattern_learner import get_bible_learner, BiblePatternLearner, SentencePattern
from .feedback_store import (
    get_feedback_store,
    FeedbackStore,
    FeedbackType,
    FeedbackStatus,
    LearningSignal,
)
# Restored: the multi-agent work below rewrote this module and dropped 12 of
# the 14 names the package had always exported, which broke
# tests/test_bible_learning.py and any consumer importing them from here.
# Adding them back is additive — nothing below is removed.
from .context_validator import ContextValidator, get_context_validator
from .data_manager import DataManager
from .dictionary_manager import DictionaryManager
from .grammar_editor import GrammarEditor
from .online_search import OnlineSearch, get_online_search
from .progress import ProgressTracker
from .sentence_builder import SentenceBuilder, get_sentence_builder
from .trainer import CorrectionTrainer
from .translation import TranslationEngine

from .agents import (
    BaseAgent,
    AgentSkill,
    AgentRole,
    AgentTeam,
    AgentMessage,
    DiscussionContext,
    AgentConfig,
    BibleLearnerAgent,
    TranslationAgent,
    GrammarAgent,
    VocabularyAgent,
    AgentTeamManager,
    DiscussionFacilitator,
    AgentTeam,
    create_standard_learning_team,
    LearningOrchestrator,
    LearningTask,
    get_learning_orchestrator,
)

__all__ = [
    # Bible patterns
    "get_bible_learner",
    "BiblePatternLearner",
    "SentencePattern",
    
    # Feedback
    "get_feedback_store",
    "FeedbackStore",
    "FeedbackType",
    "FeedbackStatus",
    "LearningSignal",
    
    # Agents
    "BaseAgent",
    "AgentSkill",
    "AgentRole",
    "AgentTeam",
    "AgentMessage",
    "DiscussionContext",
    "AgentConfig",
    "BibleLearnerAgent",
    "TranslationAgent",
    "GrammarAgent",
    "VocabularyAgent",
    
    # Teams & Discussion
    "AgentTeamManager",
    "DiscussionFacilitator",
    "AgentTeam",
    "create_standard_learning_team",
    
    # Orchestrator
    "LearningOrchestrator",
    "LearningTask",
    "get_learning_orchestrator",
  "ContextValidator",
  "get_context_validator",
  "DataManager",
  "DictionaryManager",
  "GrammarEditor",
  "OnlineSearch",
  "get_online_search",
  "ProgressTracker",
  "SentenceBuilder",
  "get_sentence_builder",
  "CorrectionTrainer",
  "TranslationEngine",
]

