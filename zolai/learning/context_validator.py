"""
Context Validator — checks if sentences make sense together.

CRITICAL: Grammar correctness ≠ Context correctness.
The AI must learn from real context (Bible), not isolated sentences.
"""
import json
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"


class ContextValidator:
    """Validate if sentences make sense together in context."""

    def __init__(self):
        self.bible_verses: list[dict] = []
        self.conversation_history: list[dict] = []
        self._load_bible_contexts()

    def _load_bible_contexts(self):
        """Load Bible verses as context."""
        bible_path = DATA_DIR / "bible" / "parallel_corpus_v1.jsonl"
        if not bible_path.exists():
            return

        try:
            with open(bible_path, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        zo = (entry.get('zo_tdb77') or '').strip()
                        en = (entry.get('en_kJV') or '').strip()
                        ref = entry.get('ref', '')
                        if zo and ref:
                            self.bible_verses.append({
                                'ref': ref,
                                'zolai': zo,
                                'english': en,
                            })
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass

    def add_to_conversation(self, role: str, text: str):
        """Add a turn to conversation history."""
        self.conversation_history.append({
            'role': role,
            'text': text,
        })
        # Keep last 10 turns
        if len(self.conversation_history) > 10:
            self.conversation_history = self.conversation_history[-10:]

    def validate_context(self, text: str) -> dict:
        """Validate if text makes sense in current context."""
        if not self.conversation_history:
            return {
                'valid': True,
                'confidence': 'UNKNOWN',
                'reason': 'No conversation history',
            }

        # Check if text relates to previous turns
        prev_texts = [t['text'] for t in self.conversation_history[-3:]]

        # Simple context check: do they share words?
        text_words = set(text.lower().split())
        prev_words = set(' '.join(prev_texts).lower().split())

        overlap = text_words & prev_words
        overlap_ratio = len(overlap) / max(len(text_words), 1)

        if overlap_ratio > 0.3:
            confidence = 'HIGH'
            valid = True
        elif overlap_ratio > 0.1:
            confidence = 'MEDIUM'
            valid = True
        else:
            confidence = 'LOW'
            valid = False

        return {
            'valid': valid,
            'confidence': confidence,
            'overlap_ratio': overlap_ratio,
            'shared_words': list(overlap)[:5],
            'reason': f'Context overlap: {overlap_ratio:.0%}',
        }

    def get_relevant_passage(self, text: str) -> Optional[dict]:
        """Find a Bible verse relevant to the text."""
        text_lower = text.lower()

        # Search for verse with matching words
        for verse in self.bible_verses:
            if any(
                word in verse['zolai'].lower()
                for word in text_lower.split()
                if len(word) > 3
            ):
                return {
                    'reference': verse['ref'],
                    'verses': [verse],
                    'relevance': 'high',
                }

        return None

    def build_context_response(self, user_input: str) -> str:
        """Build a context-aware response template."""
        # Add user input to history
        self.add_to_conversation('user', user_input)

        # Validate context
        validation = self.validate_context(user_input)

        # Find relevant verse
        passage = self.get_relevant_passage(user_input)

        # Build response
        if passage:
            context_note = f"\n[Context: {passage['reference']}]"
        else:
            context_note = ""

        if validation['valid']:
            return f"Context valid ({validation['confidence']}){context_note}"
        else:
            return f"Context unclear ({validation['confidence']}){context_note}"

    def get_stats(self) -> dict:
        """Get context validation statistics."""
        return {
            'bible_verses': len(self.bible_verses),
            'conversation_turns': len(self.conversation_history),
        }


# Singleton instance
_validator: Optional[ContextValidator] = None


def get_context_validator() -> ContextValidator:
    """Get context validator instance."""
    global _validator
    if _validator is None:
        _validator = ContextValidator()
    return _validator
