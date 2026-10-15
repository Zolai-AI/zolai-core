"""
Discussion Analyzer - Analyzes agent discussions for insights.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

#: Connectives that mark a sentence as a reasoned argument.
_CONNECTIVES = (
    "because", "since", "therefore", "thus", "however", "but", "although",
)


@dataclass
class DiscussionInsight:
    """Insight extracted from a discussion."""
    topic: str
    key_arguments: dict[str, list[str]] = field(default_factory=dict)  # agent -> arguments
    agreements: list[str] = field(default_factory=list)
    disagreements: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    proposed_solutions: list[str] = field(default_factory=list)
    sentiment: str = "neutral"  # positive, negative, neutral, mixed
    participation: dict[str, int] = field(default_factory=dict)  # agent -> message count
    key_terms: list[str] = field(default_factory=list)
    summary: str = ""


class DiscussionAnalyzer:
    """Analyzes agent discussions for insights and patterns."""

    def __init__(self):
        self.stop_words = {
            "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
            "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
            "been", "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "must", "can", "this",
            "that", "these", "those", "i", "you", "he", "she", "it", "we", "they",
            "my", "your", "his", "her", "its", "our", "their", "what", "which",
            "who", "whom", "whose", "where", "when", "why", "how", "not", "no",
            "yes", "so", "if", "then", "else", "than", "very", "just",
        }

    def analyze_discussion(self, messages: list[dict]) -> dict:
        """Analyze a full discussion and return insights."""
        if not messages:
            return {}

        # Extract participant info
        participants = set(m.get("from_agent", "") for m in messages)
        participant_count = len(participants)

        # Message counts per agent
        participation = Counter(m.get("from_agent", "") for m in messages)

        # Extract key terms from all messages
        all_text = " ".join(m.get("content", "") for m in messages)
        key_terms = self._extract_key_terms(all_text)

        # Classify messages
        agreements = []
        disagreements = []
        questions = []
        proposals = []

        for msg in messages:
            content = msg.get("content", "").lower()
            agent = msg.get("from_agent", "")

            if any(word in content for word in ["agree", "concur", "yes", "correct", "right"]):
                agreements.append({"agent": agent, "text": msg.get("content", "")[:200]})
            elif any(word in content for word in ["disagree", "dispute", "incorrect", "wrong", "no"]):
                disagreements.append({"agent": agent, "text": msg.get("content", "")[:200]})
            elif "?" in msg.get("content", ""):
                questions.append({"agent": agent, "text": msg.get("content", "")[:200]})
            elif any(word in content for word in ["propose", "suggest", "recommend"]):
                proposals.append({"agent": agent, "text": msg.get("content", "")[:200]})

        # Determine sentiment
        sentiment = self._analyze_sentiment(messages)

        # Extract key arguments per agent
        key_arguments = self._extract_key_arguments(messages)

        return {
            "topic": self._extract_topic(messages),
            "participants": list(participants),
            "participant_count": participant_count,
            "participation": dict(participation),
            "total_messages": len(messages),
            "agreements": agreements,
            "disagreements": disagreements,
            "questions": questions,
            "proposals": proposals,
            "sentiment": sentiment,
            "key_terms": key_terms,
            "key_arguments": key_arguments,
            "duration_minutes": self._estimate_duration(messages),
        }

    def _extract_topic(self, messages: list[dict]) -> str:
        """Extract main topic from discussion."""
        if not messages:
            return ""

        # Use first substantial message
        for msg in messages:
            content = msg.get("content", "")
            if len(content) > 20:
                # Extract first sentence or first 100 chars
                sentences = re.split(r'[.!?]', content)
                if sentences:
                    return sentences[0][:100]
        return "Discussion"

    def _extract_key_terms(self, text: str) -> list[str]:
        """Extract key terms from text."""
        words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
        filtered = [w for w in words if w not in self.stop_words and len(w) > 3]
        freq = Counter(filtered)
        return [word for word, _ in freq.most_common(20)]

    def _analyze_sentiment(self, messages: list[dict]) -> str:
        """Analyze overall sentiment of discussion."""
        positive_words = {"agree", "good", "great", "excellent", "correct", "right",
                         "support", "agree", "concur", "yes", "valid", "accurate"}
        negative_words = {"disagree", "wrong", "incorrect", "bad", "poor", "invalid",
                         "dispute", "reject", "oppose", "no", "wrong"}

        pos_count = 0
        neg_count = 0

        for msg in messages:
            content = msg.get("content", "").lower()
            pos_count += sum(1 for w in positive_words if w in content)
            neg_count += sum(1 for w in negative_words if w in content)

        if pos_count > neg_count * 1.5:
            return "positive"
        elif neg_count > pos_count * 1.5:
            return "negative"
        elif pos_count == 0 and neg_count == 0:
            return "neutral"
        return "mixed"

    def _extract_key_arguments(self, messages: list[dict]) -> dict[str, list[str]]:
        """Extract key arguments per agent."""
        arguments = {}

        for msg in messages:
            agent = msg.get("from_agent", "")
            content = msg.get("content", "")

            if agent not in arguments:
                arguments[agent] = []

            # Extract substantive points (longer messages with reasoning)
            if len(content) > 30:
                # Extract key phrases
                sentences = re.split(r'[.!?]', content)
                for sent in sentences:
                    sent = sent.strip()
                    if len(sent) > 20 and any(w in sent.lower() for w in _CONNECTIVES):
                        if sent not in arguments[agent]:
                            arguments[agent].append(sent[:200])

        # Limit arguments per agent
        for agent in arguments:
            arguments[agent] = arguments[agent][:5]

        return arguments

    def _estimate_duration(self, messages: list[dict]) -> float:
        """Estimate discussion duration in minutes."""
        if not messages or len(messages) < 2:
            return 0.0

        try:
            first = datetime.fromisoformat(messages[0].get("timestamp", ""))
            last = datetime.fromisoformat(messages[-1].get("timestamp", ""))
            return (last - first).total_seconds() / 60
        except (TypeError, ValueError):
            return len(messages) * 0.5  # Rough estimate

    def compare_discussions(self, discussion1: dict, discussion2: dict) -> dict:
        """Compare two discussions for similarity."""
        # Compare key terms
        terms1 = set(discussion1.get("key_terms", []))
        terms2 = set(discussion2.get("key_terms", []))

        intersection = terms1 & terms2
        union = terms1 | terms2

        jaccard = len(intersection) / len(union) if union else 0

        # Compare participants
        participants1 = set(discussion1.get("participants", []))
        participants2 = set(discussion2.get("participants", []))

        participant_overlap = len(participants1 & participants2) / max(len(participants1 | participants2), 1)

        return {
            "topic_similarity": jaccard,
            "participant_overlap": participant_overlap,
            "shared_terms": list(intersection)[:10],
        }

    def extract_action_items(self, messages: list[dict]) -> list[dict]:
        """Extract action items from discussion."""
        action_items = []

        action_patterns = [
            r"(?:should|need to|must|have to|will)\s+(.+?)(?:\.|$)",
            r"(?:action item|todo|task):\s*(.+?)(?:\.|$)",
            r"(?:i will|we will|let's)\s+(.+?)(?:\.|$)",
        ]

        for msg in messages:
            content = msg.get("content", "")
            agent = msg.get("from_agent", "")

            for pattern in action_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                for match in matches:
                    action_items.append({
                        "agent": agent,
                        "action": match.strip(),
                        "context": content[:200],
                        "timestamp": msg.get("timestamp", ""),
                    })

        return action_items
