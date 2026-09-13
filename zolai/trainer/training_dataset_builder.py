"""
Training Dataset Builder — creates EN↔ZO training pairs from Bible + parallel.

Exports to HuggingFace/Kaggle/Alpaca format. Source pairs come from the
canonical DB tables (bible_verses + translations), not raw JSONL.
"""
import json
import logging
import random
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"

log = logging.getLogger(__name__)


class TrainingDatasetBuilder:
    """Build training datasets from Bible + parallel corpus."""

    def __init__(self):
        self.bible_pairs: list[dict] = []
        self.parallel_pairs: list[dict] = []
        self._load_data()

    def _load_data(self):
        """Load Bible and parallel corpus from the canonical DB."""
        from ..data.repositories import get_repositories

        repos = get_repositories()

        # Bible: bible_verses (zo_tdb77 / en_kJV / ref)
        for r in repos["bible"].all_records(["zo_tdb77", "en_kJV", "ref"]):
            zo = r.get("zo_tdb77") or ""
            en = r.get("en_kJV") or ""
            if zo.strip() and en.strip():
                self.bible_pairs.append({
                    'zolai': zo.strip(),
                    'english': en.strip(),
                    'source': 'bible',
                    'reference': r.get('ref', ''),
                })

        # Parallel corpus: translations rows where the target holds Zolai.
        for r in repos["translation"].all_records(["source", "target", "direction", "reference"]):
            zo = r.get("target") or ""
            en = r.get("source") or ""
            if zo.strip() and en.strip():
                self.parallel_pairs.append({
                    'zolai': zo.strip(),
                    'english': en.strip(),
                    'source': r.get("direction") or "parallel",
                })

        log.info(
            "Loaded %d bible + %d parallel pairs",
            len(self.bible_pairs),
            len(self.parallel_pairs),
        )

    def build_translation_dataset(self, max_pairs: int = 50000) -> list[dict]:
        """Build EN↔ZO translation dataset (bidirectional)."""
        all_pairs = self.bible_pairs + self.parallel_pairs

        # Deduplicate
        seen: set[tuple[str, str]] = set()
        unique_pairs: list[dict] = []
        for pair in all_pairs:
            key = (pair['zolai'].lower(), pair['english'].lower())
            if key not in seen:
                seen.add(key)
                unique_pairs.append(pair)

        # Limit and shuffle
        if len(unique_pairs) > max_pairs:
            unique_pairs = random.sample(unique_pairs, max_pairs)

        # Format for Alpaca-style training (bidirectional)
        dataset: list[dict] = []
        for pair in unique_pairs:
            dataset.append({
                'instruction': 'Translate between Zolai and English',
                'input': pair['english'],
                'output': pair['zolai'],
                'source': pair['source'],
            })
            dataset.append({
                'instruction': 'Translate between Zolai and English',
                'input': pair['zolai'],
                'output': pair['english'],
                'source': pair['source'],
            })

        log.info(
            "Built translation dataset: %d pairs from %d unique",
            len(dataset),
            len(unique_pairs),
        )
        return dataset

    def build_conversation_dataset(self, max_pairs: int = 10000) -> list[dict]:
        """Build conversation dataset from Bible dialogues (quotative patterns)."""
        conversations: list[dict] = []

        for pair in self.bible_pairs:
            zo_lower = pair['zolai'].lower()
            if 'ci hi' in zo_lower or 'ci-in' in zo_lower or '"ci' in zo_lower:
                conversations.append({
                    'instruction': 'Continue the Zolai conversation',
                    'input': pair['english'],
                    'output': pair['zolai'],
                    'source': 'bible_dialogue',
                })

        if len(conversations) > max_pairs:
            conversations = random.sample(conversations, max_pairs)

        log.info("Built conversation dataset: %d pairs", len(conversations))
        return conversations

    def build_grammar_dataset(self, max_pairs: int = 5000) -> list[dict]:
        """Build grammar exercise dataset (SOV + ergative patterns)."""
        exercises: list[dict] = []

        for pair in self.bible_pairs:
            zo = pair['zolai']
            # SOV patterns: contains both ergative 'in' and declarative 'hi'
            if ' in ' in f' {zo} ' and ' hi ' in f' {zo} ':
                exercises.append({
                    'instruction': 'Convert to Zolai SOV word order',
                    'input': pair['english'],
                    'output': pair['zolai'],
                    'source': 'grammar_sov',
                })
            # Question patterns (hiam)
            elif 'hiam' in zo.lower():
                exercises.append({
                    'instruction': 'Form a Zolai question',
                    'input': pair['english'].rstrip('?') + '?',
                    'output': pair['zolai'],
                    'source': 'grammar_question',
                })

        if len(exercises) > max_pairs:
            exercises = random.sample(exercises, max_pairs)

        log.info("Built grammar dataset: %d exercises", len(exercises))
        return exercises

    def export_to_jsonl(self, dataset: list[dict], output_path: str) -> str:
        """Export dataset to JSONL format."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, 'w', encoding='utf-8') as f:
            for entry in dataset:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')

        log.info("Exported %d entries to %s", len(dataset), path)
        return str(path)

    def export_to_alpaca(self, dataset: list[dict], output_path: str) -> str:
        """Export dataset to Alpaca format (for SFT fine-tuning)."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        alpaca_data = []
        for entry in dataset:
            alpaca_data.append({
                'instruction': entry['instruction'],
                'input': entry['input'],
                'output': entry['output'],
            })

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(alpaca_data, f, ensure_ascii=False, indent=2)

        log.info("Exported Alpaca format (%d entries) to %s", len(alpaca_data), path)
        return str(path)

    def export_to_hf(self, dataset: list[dict], output_dir: str) -> str:
        """Export to HuggingFace-compatible JSONL (text field)."""
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)

        out_file = path / "train.jsonl"
        with open(out_file, 'w', encoding='utf-8') as f:
            for entry in dataset:
                text = (
                    f"### Instruction:\n{entry['instruction']}\n\n"
                    f"### Input:\n{entry['input']}\n\n"
                    f"### Response:\n{entry['output']}"
                )
                f.write(json.dumps({"text": text, "source": entry.get('source', '')}, ensure_ascii=False) + '\n')

        log.info("Exported HuggingFace format (%d entries) to %s", len(dataset), out_file)
        return str(path)

    def get_stats(self) -> dict:
        """Get dataset statistics."""
        all_pairs = self.bible_pairs + self.parallel_pairs
        sources = {}
        for pair in all_pairs:
            src = pair.get('source', 'unknown')
            sources[src] = sources.get(src, 0) + 1

        return {
            'bible_pairs': len(self.bible_pairs),
            'parallel_pairs': len(self.parallel_pairs),
            'total_pairs': len(all_pairs),
            'sources': sources,
        }


def get_training_builder() -> TrainingDatasetBuilder:
    """Get training dataset builder instance."""
    return TrainingDatasetBuilder()
