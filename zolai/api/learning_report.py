"""
Zolai AI Learning Report — progress logs and statistics.
"""
import json
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data" / "zolai_memory"


class LearningReport:
    """Generate learning progress reports."""

    def __init__(self, memory, learning_engine):
        self.memory = memory
        self.engine = learning_engine

    def generate_session_report(self) -> dict:
        """Generate report for current session."""
        stats = self.memory.get_stats()
        corrections = self.engine.get_correction_stats()
        velocity = self.engine.get_learning_velocity()

        return {
            'timestamp': datetime.now().isoformat(),
            'session': {
                'l1_turns': stats['l1_turns'],
                'l2_turns': stats['l2_turns'],
            },
            'vocabulary': stats['l3_vocabulary'],
            'patterns': stats['l4_patterns'],
            'corrections': corrections,
            'velocity': velocity,
        }

    def generate_text_report(self) -> str:
        """Generate human-readable progress report."""
        report = self.generate_session_report()

        lines = [
            "# Zolai AI Learning Report",
            f"Generated: {report['timestamp'][:16]}",
            "",
            "## Session",
            f"- Working memory: {report['session']['l1_turns']} turns",
            f"- Session memory: {report['session']['l2_turns']} turns",
            "",
            "## Vocabulary",
            f"- Total words tracked: {report['vocabulary']['total']}",
            f"- Average confidence: {report['vocabulary']['avg_confidence']:.1%}",
        ]

        for level, count in report['vocabulary']['levels'].items():
            lines.append(f"- {level}: {count}")

        lines.extend([
            "",
            "## Learning",
            f"- Total corrections: {report['corrections']['total']}",
            f"- Learning trend: {report['velocity']['trend']}",
            f"- Corrections this session: {report['velocity']['corrections_per_session']}",
        ])

        if report['corrections']['by_rule']:
            lines.append("\n### Correction Rules")
            for rule, count in report['corrections']['by_rule'].items():
                lines.append(f"- {rule}: {count}")

        return "\n".join(lines)

    def save_report(self):
        """Save report to file."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        report = self.generate_session_report()
        path = DATA_DIR / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return path


def get_learning_report(memory=None, engine=None):
    """Get or create learning report instance."""
    from .learning_engine import get_learning_engine
    from .memory_layers import MemoryLayers

    if memory is None:
        memory = MemoryLayers()
    if engine is None:
        engine = get_learning_engine(memory)
    return LearningReport(memory, engine)
