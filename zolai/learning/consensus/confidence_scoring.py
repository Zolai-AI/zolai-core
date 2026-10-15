"""
Confidence Scoring for Agents.

Tracks agent track records and computes dynamic confidence scores.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AgentTrackRecord:
    """Track record for an agent."""
    agent_id: str
    agent_role: str
    total_decisions: int = 0
    correct_decisions: int = 0
    incorrect_decisions: int = 0
    abstentions: int = 0
    avg_confidence: float = 0.5
    avg_confidence_when_correct: float = 0.5
    avg_confidence_when_wrong: float = 0.5
    calibration_error: float = 0.0  # How well confidence matches accuracy
    last_updated: datetime = field(default_factory=datetime.utcnow)
    recent_accuracy: float = 0.5  # Rolling window accuracy
    specialization_scores: dict[str, float] = field(default_factory=dict)  # topic -> accuracy

    @property
    def accuracy(self) -> float:
        if self.total_decisions == 0:
            return 0.5
        return self.correct_decisions / max(self.total_decisions, 1)

    @property
    def accuracy_without_abstain(self) -> float:
        total = self.correct_decisions + self.incorrect_decisions
        if total == 0:
            return 0.5
        return self.correct_decisions / total


class ConfidenceScorer:
    """Computes and maintains agent confidence scores."""

    def __init__(self, window_size: int = 100, decay_factor: float = 0.95):
        self.window_size = window_size
        self.decay_factor = decay_factor
        self.track_records: dict[str, AgentTrackRecord] = {}
        self.decision_history: list[dict] = []
        self.min_decisions_for_calibration = 10

    def record_decision(self, agent_id: str, agent_role: str,
                       was_correct: bool, confidence: float,
                       topic: str = "general") -> None:
        """Record a decision outcome for an agent."""
        if agent_id not in self.track_records:
            self.track_records[agent_id] = AgentTrackRecord(
                agent_id=agent_id,
                agent_role=agent_role,
            )

        record = self.track_records[agent_id]
        record.total_decisions += 1

        if was_correct:
            record.correct_decisions += 1
            # Update average confidence when correct
            n = record.correct_decisions
            record.avg_confidence_when_correct = (
                (record.avg_confidence_when_correct * (n - 1) + confidence) / n
            )
        else:
            record.incorrect_decisions += 1
            # Update average confidence when wrong
            n = record.incorrect_decisions
            record.avg_confidence_when_wrong = (
                (record.avg_confidence_when_wrong * (n - 1) + confidence) / n
            )

        # Update overall average confidence
        n = record.total_decisions
        record.avg_confidence = (
            (record.avg_confidence * (n - 1) + confidence) / n
        )

        # Update specialization score
        if topic not in record.specialization_scores:
            record.specialization_scores[topic] = 0.5
        topic_acc = record.specialization_scores[topic]
        if was_correct:
            record.specialization_scores[topic] = topic_acc * 0.9 + 0.1
        else:
            record.specialization_scores[topic] = topic_acc * 0.9

        # Update calibration error (Brier score component)
        record.calibration_error = abs(record.avg_confidence - record.accuracy)

        # Store in history for rolling window
        self.decision_history.append({
            "agent_id": agent_id,
            "correct": was_correct,
            "confidence": confidence,
            "topic": topic,
            "timestamp": datetime.utcnow(),
        })

        # Trim history
        if len(self.decision_history) > self.window_size * 10:
            self.decision_history = self.decision_history[-self.window_size:]

        # Update rolling accuracy
        self._update_rolling_metrics(agent_id)

        record.last_updated = datetime.utcnow()

    def _update_rolling_metrics(self, agent_id: str):
        """Update rolling window metrics (exponentially decayed accuracy)."""
        record = self.track_records[agent_id]

        # Weight the most recent decisions highest, older ones fading by
        # ``decay_factor`` per step so the window self-updates without a re-scan.
        recent = [
            d for d in self.decision_history[-self.window_size:]
            if d["agent_id"] == agent_id
        ]

        if not recent:
            record.recent_accuracy = 0.5
            return

        total_weight = 0.0
        correct_weight = 0.0
        steps = len(recent)
        for offset, decision in enumerate(recent):
            weight = self.decay_factor ** (steps - 1 - offset)
            total_weight += weight
            if decision["correct"]:
                correct_weight += weight

        record.recent_accuracy = correct_weight / total_weight if total_weight else 0.5

    def get_confidence_score(self, agent_id: str, topic: str = "general") -> float:
        """Get calibrated confidence score for an agent on a topic."""
        if agent_id not in self.track_records:
            return 0.5  # Default for unknown agents

        record = self.track_records[agent_id]

        # Base score from accuracy
        base_score = record.accuracy

        # Adjust for specialization
        topic_score = record.specialization_scores.get(topic, 0.5)

        # Adjust for calibration (well-calibrated agents get boost)
        calibration_penalty = record.calibration_error * 0.5

        # Recent performance weight — a third of the score, scaled by how
        # accurate the agent has actually been recently.
        recent_component = record.recent_accuracy * 0.3

        # Combine scores
        score = (
            base_score * 0.4 +
            topic_score * 0.3 +
            (1 - calibration_penalty) * 0.2 +
            recent_component
        )

        return max(0.1, min(0.95, score))  # Clamp to reasonable range

    def get_agent_weight(self, agent_id: str, role: str = "") -> float:
        """Get weight for an agent in weighted voting."""
        if agent_id not in self.track_records:
            return 1.0  # Default weight for new agents

        record = self.track_records[agent_id]

        # Base weight from accuracy
        accuracy_weight = record.accuracy

        # Role multiplier
        role_weights = {
            "bible_learner": 1.2,
            "grammar_expert": 1.1,
            "translation_agent": 1.0,
            "vocabulary_builder": 1.0,
            "translation_validator": 1.1,
            "context_analyzer": 1.0,
            "discussion_facilitator": 0.9,
            "learning_orchestrator": 0.9,
        }
        role_mult = role_weights.get(role, 1.0)

        # Confidence calibration factor
        calibration = 1.0 - record.calibration_error

        # Volume factor (more decisions = more reliable)
        volume_factor = min(1.0, record.total_decisions / 50.0)

        # Combine
        weight = accuracy_weight * role_mult * calibration * volume_factor

        # Normalize to reasonable range
        return max(0.1, min(2.0, weight))

    def get_agent_stats(self, agent_id: str) -> Optional[dict]:
        """Get agent statistics."""
        if agent_id not in self.track_records:
            return None

        record = self.track_records[agent_id]
        return {
            "agent_id": agent_id,
            "role": record.agent_role,
            "total_decisions": record.total_decisions,
            "accuracy": record.accuracy,
            "accuracy_no_abstain": record.accuracy_without_abstain,
            "recent_accuracy": record.recent_accuracy,
            "avg_confidence": record.avg_confidence,
            "calibration_error": record.calibration_error,
            "specialization": record.specialization_scores,
            "weight": self.get_agent_weight(agent_id, record.agent_role),
        }

    def get_all_agent_weights(self) -> dict[str, float]:
        """Get weights for all agents."""
        return {
            agent_id: self.get_agent_weight(agent_id, record.agent_role)
            for agent_id, record in self.track_records.items()
        }

    def calibrate_confidence(self, agent_id: str, target_accuracy: float = None) -> float:
        """Get calibration adjustment for an agent.

        Returns a damping factor in ``(0, 1]``: over-confident agents (large gap
        between stated confidence and measured accuracy) get scaled down. When
        ``target_accuracy`` is supplied the factor is additionally clamped so an
        agent never claims more accuracy than the target allows.
        """
        if agent_id not in self.track_records:
            return 1.0

        record = self.track_records[agent_id]
        if record.total_decisions < self.min_decisions_for_calibration:
            return 1.0

        factor = 1.0 - record.calibration_error
        if target_accuracy is not None:
            factor = min(factor, target_accuracy / max(record.accuracy, 1e-6))
        return max(0.1, min(1.0, factor))
