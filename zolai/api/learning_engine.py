"""
Zolai AI Learning Engine — feedback loop and learning tracking.

Processes corrections, updates mastery, extracts patterns.
"""
import json
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data" / "zolai_memory"


class LearningEngine:
    """Feedback loop for Zolai AI learning."""

    def __init__(self, memory):
        self.memory = memory
        self.corrections: list[dict] = []
        self._load_corrections()

    def _load_corrections(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / "corrections.jsonl"
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self.corrections.append(json.loads(line))

    def _save_correction(self, correction: dict):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / "corrections.jsonl"
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(correction, ensure_ascii=False) + "\n")
        self.corrections.append(correction)

    def process_feedback(self, original: str, corrected: str, rule: str,
                         user_feedback: str = "corrected"):
        """Process user feedback on AI response."""
        correction = {
            'original': original,
            'corrected': corrected,
            'rule': rule,
            'feedback': user_feedback,
            'timestamp': datetime.now().isoformat(),
        }

        # Save correction
        self._save_correction(correction)

        # Update memory layers
        self.memory.add_correction(original, corrected, rule)

        return correction

    def learn_from_conversation(self, user_input: str, ai_response: str,
                                 user_correction: str | None = None):
        """Learn from a conversation turn."""
        if user_correction:
            # User corrected the AI
            self.process_feedback(
                original=ai_response,
                corrected=user_correction,
                rule="user_correction",
                user_feedback="corrected"
            )

        # Track vocabulary usage
        import re
        words = re.findall(r'\b[a-z][a-z]*\b', user_input.lower())
        for word in words:
            if len(word) >= 3:  # Skip very short words
                self.memory.update_vocabulary(word, True, user_input[:100])

    def get_correction_stats(self) -> dict:
        if not self.corrections:
            return {'total': 0, 'by_rule': {}}

        by_rule = {}
        for c in self.corrections:
            rule = c.get('rule', 'unknown')
            by_rule[rule] = by_rule.get(rule, 0) + 1

        return {
            'total': len(self.corrections),
            'by_rule': by_rule,
            'recent': self.corrections[-5:] if self.corrections else []
        }

    def get_learning_velocity(self) -> dict:
        """Calculate learning velocity from corrections."""
        if not self.corrections:
            return {'corrections_per_session': 0, 'trend': 'stable'}

        # Group by date
        by_date = {}
        for c in self.corrections:
            date = c['timestamp'][:10]
            by_date[date] = by_date.get(date, 0) + 1

        dates = sorted(by_date.keys())
        if len(dates) < 2:
            return {'corrections_per_session': len(self.corrections), 'trend': 'new'}

        recent = by_date[dates[-1]]
        previous = by_date[dates[-2]]

        if recent > previous:
            trend = 'improving'
        elif recent < previous:
            trend = 'declining'
        else:
            trend = 'stable'

        return {
            'corrections_per_session': recent,
            'trend': trend,
            'total_sessions': len(dates),
        }


def get_learning_engine(memory=None):
    """Get or create learning engine instance."""
    from .memory_layers import MemoryLayers
    if memory is None:
        memory = MemoryLayers()
    return LearningEngine(memory)
