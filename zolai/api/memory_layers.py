"""
Zolai AI Memory Layers — L1 to L(N).

L1: Working memory (5 turns, in-memory)
L2: Session memory (50 turns, in-memory + JSONL append)
L3: Vocabulary mastery (persistent JSON)
L4: Cross-session patterns (aggregated JSONL)
"""
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data" / "zolai_memory"


class L1WorkingMemory:
    """L1: Current conversation working memory (5 turns)."""

    def __init__(self, max_turns: int = 5):
        self.max_turns = max_turns
        self.turns: list[dict] = []

    def add(self, role: str, text: str, metadata: dict | None = None):
        self.turns.append({
            'role': role,
            'text': text,
            'timestamp': datetime.now().isoformat(),
            'metadata': metadata or {}
        })
        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns:]

    def get_context(self, max_tokens: int = 200) -> str:
        if not self.turns:
            return ""
        lines = ["## Current Conversation"]
        for t in self.turns[-3:]:
            lines.append(f"{t['role']}: {t['text'][:80]}")
        return "\n".join(lines)

    def clear(self):
        self.turns = []


class L2SessionMemory:
    """L2: Session memory (50 turns, persisted to JSONL)."""

    def __init__(self, session_id: str = "default", max_turns: int = 50):
        self.session_id = session_id
        self.max_turns = max_turns
        self.turns: list[dict] = []
        self._load()

    def _load(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / f"session_{self.session_id}.jsonl"
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self.turns.append(json.loads(line))

    def _save(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / f"session_{self.session_id}.jsonl"
        with open(path, 'w', encoding='utf-8') as f:
            for t in self.turns[-self.max_turns:]:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")

    def add(self, role: str, text: str, metadata: dict | None = None):
        self.turns.append({
            'role': role,
            'text': text,
            'timestamp': datetime.now().isoformat(),
            'metadata': metadata or {}
        })
        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns:]
        self._save()

    def get_context(self, max_tokens: int = 300) -> str:
        if not self.turns:
            return ""
        lines = ["## Session History"]
        for t in self.turns[-5:]:
            lines.append(f"[{t['timestamp'][:16]}] {t['role']}: {t['text'][:100]}")
        return "\n".join(lines)

    def clear(self):
        self.turns = []
        self._save()


class L3VocabularyMastery:
    """L3: Persistent vocabulary mastery tracking."""

    def __init__(self):
        self.mastery: dict[str, dict] = {}
        self._load()

    def _load(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / "vocabulary_mastery.json"
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                self.mastery = json.load(f)

    def _save(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / "vocabulary_mastery.json"
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.mastery, f, ensure_ascii=False, indent=2)

    def update(self, word: str, correct: bool, context: str = ""):
        if word not in self.mastery:
            self.mastery[word] = {
                'word': word,
                'attempts': 0,
                'correct': 0,
                'confidence': 0.0,
                'level': 'unknown',
                'first_seen': datetime.now().isoformat(),
                'last_seen': datetime.now().isoformat(),
                'contexts': []
            }
        entry = self.mastery[word]
        entry['attempts'] += 1
        if correct:
            entry['correct'] += 1
        entry['confidence'] = entry['correct'] / entry['attempts'] if entry['attempts'] > 0 else 0.0
        entry['last_seen'] = datetime.now().isoformat()

        # Update level based on confidence
        if entry['confidence'] >= 0.9 and entry['attempts'] >= 5:
            entry['level'] = 'mastered'
        elif entry['confidence'] >= 0.7:
            entry['level'] = 'learning'
        elif entry['confidence'] >= 0.4:
            entry['level'] = 'familiar'
        else:
            entry['level'] = 'new'

        if context and len(entry['contexts']) < 5:
            entry['contexts'].append(context[:200])

        self._save()

    def get_word(self, word: str) -> dict | None:
        return self.mastery.get(word)

    def get_context(self, max_tokens: int = 300) -> str:
        if not self.mastery:
            return ""
        # Top 10 words by confidence that aren't mastered yet
        learning = [v for v in self.mastery.values() if v['level'] != 'mastered']
        learning.sort(key=lambda x: x['confidence'], reverse=True)
        lines = ["## Vocabulary Progress"]
        for entry in learning[:10]:
            lines.append(f"- {entry['word']}: {entry['level']} ({entry['confidence']:.0%})")
        mastered = sum(1 for v in self.mastery.values() if v['level'] == 'mastered')
        lines.append(f"\nTotal: {len(self.mastery)} words, {mastered} mastered")
        return "\n".join(lines)

    def get_stats(self) -> dict:
        levels = defaultdict(int)
        for entry in self.mastery.values():
            levels[entry['level']] += 1
        return {
            'total': len(self.mastery),
            'levels': dict(levels),
            'avg_confidence': sum(v['confidence'] for v in self.mastery.values()) / max(len(self.mastery), 1)
        }


class L4CrossSessionPatterns:
    """L4: Cross-session pattern extraction and learning."""

    def __init__(self, max_entries: int = 10000):
        self.max_entries = max_entries
        self.patterns: list[dict] = []
        self._load()

    def _load(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / "cross_session_patterns.jsonl"
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self.patterns.append(json.loads(line))

    def _save(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / "cross_session_patterns.jsonl"
        # LRU eviction if over max
        if len(self.patterns) > self.max_entries:
            self.patterns = self.patterns[-self.max_entries:]
        with open(path, 'w', encoding='utf-8') as f:
            for p in self.patterns:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")

    def add_pattern(self, pattern_type: str, pattern: str, confidence: float = 0.5):
        self.patterns.append({
            'type': pattern_type,
            'pattern': pattern,
            'confidence': confidence,
            'timestamp': datetime.now().isoformat(),
            'seen_count': 1
        })
        self._save()

    def add_correction(self, original: str, corrected: str, rule: str):
        self.patterns.append({
            'type': 'correction',
            'original': original,
            'corrected': corrected,
            'rule': rule,
            'timestamp': datetime.now().isoformat(),
            'seen_count': 1
        })
        self._save()

    def get_context(self, max_tokens: int = 300) -> str:
        if not self.patterns:
            return ""
        # Recent corrections
        corrections = [p for p in self.patterns if p['type'] == 'correction']
        corrections.sort(key=lambda x: x['timestamp'], reverse=True)
        lines = ["## Learned Patterns"]
        for c in corrections[:5]:
            lines.append(f"- {c['original']} → {c['corrected']} ({c['rule']})")
        return "\n".join(lines)


class MemoryLayers:
    """Unified memory interface combining L1-L4."""

    def __init__(self, session_id: str = "default"):
        self.l1 = L1WorkingMemory()
        self.l2 = L2SessionMemory(session_id)
        self.l3 = L3VocabularyMastery()
        self.l4 = L4CrossSessionPatterns()

    def add_turn(self, role: str, text: str, metadata: dict | None = None):
        self.l1.add(role, text, metadata)
        self.l2.add(role, text, metadata)

    def get_full_context(self, max_tokens: int = 500) -> str:
        parts = []
        for layer in [self.l1, self.l2, self.l3, self.l4]:
            ctx = layer.get_context()
            if ctx:
                parts.append(ctx)
        # Truncate to token budget (rough: 1 token ≈ 4 chars)
        full = "\n\n".join(parts)
        char_budget = max_tokens * 4
        if len(full) > char_budget:
            full = full[:char_budget] + "\n... [truncated]"
        return full

    def update_vocabulary(self, word: str, correct: bool, context: str = ""):
        self.l3.update(word, correct, context)

    def add_correction(self, original: str, corrected: str, rule: str):
        self.l4.add_correction(original, corrected, rule)
        # Also update vocabulary mastery
        self.l3.update(original, False, f"Corrected to: {corrected}")
        self.l3.update(corrected, True, f"Correct form of: {original}")

    def get_stats(self) -> dict:
        return {
            'l1_turns': len(self.l1.turns),
            'l2_turns': len(self.l2.turns),
            'l3_vocabulary': self.l3.get_stats(),
            'l4_patterns': len(self.l4.patterns),
        }

    def clear_session(self):
        self.l1.clear()
        self.l2.clear()
