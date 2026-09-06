"""
Conversation Memory for Zolai AI.

Tracks turns and vocabulary learned per session.
"""
from collections import defaultdict
from typing import Optional


class ConversationMemory:
    """Track conversation history and vocabulary."""

    def __init__(self, max_turns: int = 5):
        self.max_turns = max_turns
        self.sessions: dict[str, list[dict]] = defaultdict(list)
        self.vocabulary: dict[str, set[str]] = defaultdict(set)

    def add_turn(self, session_id: str, role: str, text: str):
        """Add a conversation turn."""
        self.sessions[session_id].append({
            'role': role,
            'text': text
        })
        # Keep only last N turns
        if len(self.sessions[session_id]) > self.max_turns:
            self.sessions[session_id] = self.sessions[session_id][-self.max_turns:]

    def get_history(self, session_id: str) -> list[dict]:
        """Get conversation history."""
        return self.sessions.get(session_id, [])

    def add_vocabulary(self, session_id: str, words: list[str]):
        """Track vocabulary user has learned."""
        self.vocabulary[session_id].update(words)

    def get_vocabulary(self, session_id: str) -> set[str]:
        """Get vocabulary user has learned."""
        return self.vocabulary.get(session_id, set())

    def get_context_summary(self, session_id: str) -> str:
        """Get summary of conversation context."""
        history = self.get_history(session_id)
        vocab = self.get_vocabulary(session_id)

        if not history and not vocab:
            return "New conversation."

        summary = []
        if vocab:
            summary.append(f"Vocabulary learned: {', '.join(sorted(vocab)[:10])}")
        if history:
            last_turn = history[-1]
            summary.append(f"Last topic: {last_turn['text'][:100]}")

        return "; ".join(summary)

# Singleton instance
_memory_instance: Optional[ConversationMemory] = None

def get_conversation_memory() -> ConversationMemory:
    global _memory_instance
    if _memory_instance is None:
        _memory_instance = ConversationMemory()
    return _memory_instance
