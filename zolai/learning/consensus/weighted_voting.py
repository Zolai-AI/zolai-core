"""
Weighted Voting Consensus System.

Implements weighted voting based on agent confidence, expertise, and track record.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)

#: Vote types recognised by the tally (mirrors :class:`VoteType`).
VALID_VOTE_TYPES = ("agree", "disagree", "abstain", "conditional")


class VoteType(str, Enum):
    """Types of votes."""

    AGREE = "agree"
    DISAGREE = "disagree"
    ABSTAIN = "abstain"
    CONDITIONAL = "conditional"  # Agree with conditions


@dataclass
class Vote:
    """A single vote from an agent."""

    agent_id: str
    agent_role: str
    vote_type: str  # agree, disagree, abstain, conditional
    confidence: float  # 0.0 to 1.0
    reasoning: str = ""
    conditions: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    weight: float = 1.0  # Calculated weight


@dataclass
class ConsensusResult:
    """Result of consensus building."""

    topic: str
    decision: str  # consensus, no_consensus, split
    agree_weight: float = 0.0
    disagree_weight: float = 0.0
    abstain_weight: float = 0.0
    total_weight: float = 0.0
    consensus_threshold: float = 0.66  # 66% for consensus
    votes: list = field(default_factory=list)
    consensus_reached: bool = False
    winning_position: str = ""
    conditions: list[str] = field(default_factory=list)
    minority_opinions: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.utcnow)


class WeightedVotingConsensus:
    """Weighted voting consensus system."""

    def __init__(self, consensus_threshold: float = 0.66):
        self.consensus_threshold = consensus_threshold
        self.votes: list = []
        self.agent_weights: dict[str, float] = {}  # agent_id -> base weight
        self._weights_calculated = False

    def add_vote(self, vote: dict) -> None:
        """Add a vote to the consensus."""
        vote_obj = {
            "agent_id": vote.get("agent_id", ""),
            "agent_role": vote.get("agent_role", ""),
            "vote_type": vote.get("vote_type", "agree"),
            "confidence": vote.get("confidence", 0.5),
            "reasoning": vote.get("reasoning", ""),
            "conditions": vote.get("conditions", []),
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.votes.append(vote_obj)
        # New votes need weighting before the next tally.
        self._weights_calculated = False

    def calculate_weights(self, agent_weights: dict[str, float] = None) -> None:
        """Calculate weights for each vote."""
        self.agent_weights = agent_weights or {}

        for vote in self.votes:
            agent_id = vote["agent_id"]
            role_weight = self._get_role_weight(vote.get("agent_role", ""))
            base_weight = self.agent_weights.get(agent_id, 1.0)
            confidence = vote.get("confidence", 0.5)

            # Weight = role_weight * base_weight * confidence
            vote["weight"] = role_weight * base_weight * confidence

        self._weights_calculated = True

    def _get_role_weight(self, role: str) -> float:
        """Get base weight for agent role."""
        role_weights = {
            "bible_learner": 1.2,  # Primary knowledge source
            "grammar_expert": 1.1,  # Authority on grammar
            "translation_agent": 1.0,  # Standard weight
            "vocabulary_builder": 1.0,  # Standard weight
            "translation_validator": 1.1,  # Validation authority
            "context_analyzer": 1.0,
            "discussion_facilitator": 0.9,
            "learning_orchestrator": 0.9,
        }
        return role_weights.get(role, 1.0)

    def build_consensus(self, topic: str, threshold: float = None) -> dict:
        """Build consensus from collected votes.

        ``threshold`` overrides the instance default for this tally only.
        """
        threshold = self.consensus_threshold if threshold is None else threshold

        if not self.votes:
            return self._empty_result(topic, "no_votes")

        # Calculate weights if not done
        if not self._weights_calculated:
            self.calculate_weights(self.agent_weights)

        # Calculate weighted sums
        agree_weight = sum(v.get("weight", 0) for v in self.votes if v.get("vote_type") == "agree")
        disagree_weight = sum(v.get("weight", 0) for v in self.votes if v.get("vote_type") == "disagree")
        abstain_weight = sum(v.get("weight", 0) for v in self.votes if v.get("vote_type") == "abstain")
        conditional_weight = sum(v.get("weight", 0) for v in self.votes if v.get("vote_type") == "conditional")

        total_weight = agree_weight + disagree_weight + abstain_weight + conditional_weight

        if total_weight == 0:
            return self._empty_result(topic, "no_valid_votes")

        agree_ratio = agree_weight / total_weight
        disagree_ratio = disagree_weight / total_weight

        # Collect conditions from conditional votes
        conditions = []
        for vote in self.votes:
            if vote.get("vote_type") == "conditional":
                conditions.extend(vote.get("conditions", []))

        # Determine consensus
        consensus_reached = False
        winning_position = ""
        decision = "no_consensus"

        if agree_ratio >= threshold:
            consensus_reached = True
            winning_position = "agree"
            decision = "consensus"
        elif disagree_ratio >= threshold:
            consensus_reached = True
            winning_position = "disagree"
            decision = "consensus"
        elif agree_weight > disagree_weight:
            decision = "majority_agree"
            winning_position = "agree"
        elif disagree_weight > agree_weight:
            decision = "majority_disagree"
            winning_position = "disagree"
        else:
            decision = "split"
            winning_position = "split"

        # Collect minority opinions
        minority = []
        for vote in self.votes:
            if vote.get("vote_type") in ("disagree", "abstain") and vote.get("reasoning"):
                minority.append(f"{vote.get('agent_id')}: {vote.get('reasoning')}")

        return {
            "topic": topic,
            "decision": decision,
            "agree_weight": agree_weight,
            "disagree_weight": disagree_weight,
            "abstain_weight": abstain_weight,
            "conditional_weight": conditional_weight,
            "total_weight": total_weight,
            "agree_ratio": agree_ratio,
            "disagree_ratio": disagree_ratio,
            "consensus_threshold": threshold,
            "consensus_reached": consensus_reached,
            "winning_position": winning_position,
            "conditions": sorted(set(conditions)),  # Deduplicate, stable order
            "minority_opinions": minority,
            "votes": self.votes,
        }

    def _empty_result(self, topic: str, reason: str) -> dict:
        """Return empty consensus result."""
        return {
            "topic": topic,
            "decision": "no_consensus",
            "reason": reason,
            "agree_weight": 0.0,
            "disagree_weight": 0.0,
            "abstain_weight": 0.0,
            "conditional_weight": 0.0,
            "total_weight": 0.0,
            "agree_ratio": 0.0,
            "disagree_ratio": 0.0,
            "consensus_threshold": self.consensus_threshold,
            "consensus_reached": False,
            "winning_position": "",
            "conditions": [],
            "minority_opinions": [],
            "votes": [],
        }

    def clear_votes(self):
        """Clear all votes."""
        self.votes = []
        self._weights_calculated = False


class ConsensusBuilder:
    """Helper to build consensus from agent discussions."""

    def __init__(self, consensus_threshold: float = 0.66):
        self.voting = WeightedVotingConsensus(consensus_threshold)

    def add_agent_vote(
        self,
        agent_id: str,
        agent_role: str,
        vote_type: str,
        confidence: float,
        reasoning: str = "",
        conditions: list = None,
    ) -> None:
        """Add a vote from an agent."""
        vote = {
            "agent_id": agent_id,
            "agent_role": agent_role,
            "vote_type": vote_type,
            "confidence": confidence,
            "reasoning": reasoning,
            "conditions": conditions or [],
        }
        self.voting.add_vote(vote)

    def build(self, topic: str, agent_weights: dict = None) -> dict:
        """Build final consensus, optionally weighting agents by track record."""
        if agent_weights is not None:
            self.voting.calculate_weights(agent_weights)
        return self.voting.build_consensus(topic)

    def clear(self):
        """Clear all votes."""
        self.voting.clear_votes()
