"""
Advanced Consensus System for Agent Decisions.

Weighted voting based on agent confidence, expertise, and track record.
"""

from .confidence_scoring import AgentTrackRecord, ConfidenceScorer
from .discussion_analyzer import DiscussionAnalyzer, DiscussionInsight
from .weighted_voting import (
    ConsensusBuilder,
    ConsensusResult,
    Vote,
    VoteType,
    WeightedVotingConsensus,
)

__all__ = [
    "WeightedVotingConsensus",
    "ConsensusBuilder",
    "ConsensusResult",
    "Vote",
    "VoteType",
    "ConfidenceScorer",
    "AgentTrackRecord",
    "DiscussionAnalyzer",
    "DiscussionInsight",
]

