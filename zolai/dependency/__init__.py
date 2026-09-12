"""Dependency parsing for Zolai using Gemini ensemble + rule-based fallback."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Zolai dependency labels
DEP_LABELS = [
    "nsubj",   # nominal subject
    "dobj",    # direct object
    "iobj",    # indirect object
    "nmod",    # nominal modifier
    "amod",    # adjectival modifier
    "advmod",  # adverbial modifier
    "root",    # root verb
    "aux",     # auxiliary
    "case",    # case marker
    "conj",    # conjunct
    "cc",      # coordinating conjunction
    "mark",    # marker
    "det",     # determiner
    "clf",     # classifier
    "obl",     # oblique
    "appos",   # appositional
]

# Common Zolai particles and their dependency roles
PARTICLE_ROLES = {
    "in": "case",       # ergative marker
    "a": "aux",         # agreement marker
    "ka": "nsubj",      # 1SG subject
    "na": "nsubj",      # 2SG subject
    "ki": "nsubj",      # 1PL subject
    "hi": "mark",       # declarative
    "hiam": "mark",     # question
    "kei": "aux",       # negation
    "lo": "aux",        # negation (literary)
    "ding": "aux",      # future
    "zo": "aux",        # completive
    "khin": "aux",      # experiential
    "lai": "aux",       # progressive
    "ta": "aux",        # past/completive
    "leh": "cc",        # and
    "tua": "mark",      # that (conjunction)
    "tawh": "case",     # with
    "ah": "case",       # at/to
    "sungah": "case",   # inside
}


class ZolaiDependency:
    """Dependency parsing for Zolai using Gemini ensemble."""

    def __init__(self, voter: Any = None):
        """Initialize dependency parser.

        Args:
            voter: EnsembleVoter instance for Gemini integration (optional).
        """
        self.voter = voter

    async def parse(self, sentence: str) -> dict:
        """Parse sentence dependencies using Gemini ensemble.

        Args:
            sentence: Zolai sentence to parse.

        Returns:
            {"root": "piangsak", "dependencies": [
                {"head": "Pasian", "dep": "nsubj", "tail": "piangsak"},
                ...
            ]}
        """
        if self.voter is None:
            return self.parse_rulebased(sentence)

        prompt = (
            "Parse dependencies in this Zolai SOV sentence. "
            "Identify: root verb, subjects, objects, auxiliaries, markers.\n"
            f"Sentence: {sentence}\n"
            'Output JSON: {"root": "...", "dependencies": ['
            '{"head": "...", "dep": "...", "tail": "..."}]}'
        )
        try:
            raw = await self.voter.vote(prompt)
            result = json.loads(raw) if isinstance(raw, str) else raw
            data = result.get("result", result)
            root = data.get("root", "")
            deps = data.get("dependencies", [])
            # Validate dependency labels
            valid_deps = [
                d for d in deps
                if d.get("dep") in DEP_LABELS
            ]
            return {"root": root, "dependencies": valid_deps}
        except Exception as e:
            logger.warning("Gemini dependency parse failed, using rules: %s", e)
            return self.parse_rulebased(sentence)

    def parse_rulebased(self, sentence: str) -> dict:
        """Rule-based dependency parsing for Zolai SOV sentences.

        Args:
            sentence: Zolai sentence to parse.

        Returns:
            {"root": "...", "dependencies": [...]}
        """
        words = sentence.split()
        if not words:
            return {"root": "", "dependencies": []}

        dependencies = []
        root = ""
        root_idx = -1

        # Find root verb (typically last content word before 'hi')
        for i in range(len(words) - 1, -1, -1):
            w = words[i].lower().strip(".,;:!?")
            # Skip particles and markers
            if w in ("hi", "hiam", "hen", "un", "in", "vo", "a", "ka", "na", "ki"):
                continue
            # This should be the main verb
            root = w.rstrip(".")
            root_idx = i
            break

        if not root and words:
            root = words[-1].lower().strip(".,;:!?")
            root_idx = len(words) - 1

        # Analyze each word
        for i, word in enumerate(words):
            w_clean = word.strip(".,;:!?")
            w_lower = w_clean.lower()

            if i == root_idx:
                dependencies.append({
                    "head": "_ROOT_",
                    "dep": "root",
                    "tail": w_clean,
                })
                continue

            # Check particles
            if w_lower in PARTICLE_ROLES:
                dep_label = PARTICLE_ROLES[w_lower]
                # Find what this particle modifies
                if dep_label == "case" and i > 0:
                    # Case marker attaches to preceding noun
                    dependencies.append({
                        "head": words[i - 1].strip(".,;:!?"),
                        "dep": dep_label,
                        "tail": w_clean,
                    })
                elif dep_label == "aux":
                    dependencies.append({
                        "head": root,
                        "dep": dep_label,
                        "tail": w_clean,
                    })
                elif dep_label == "mark":
                    dependencies.append({
                        "head": root,
                        "dep": dep_label,
                        "tail": w_clean,
                    })
                elif dep_label == "cc":
                    dependencies.append({
                        "head": root,
                        "dep": dep_label,
                        "tail": w_clean,
                    })
                else:
                    dependencies.append({
                        "head": root,
                        "dep": dep_label,
                        "tail": w_clean,
                    })
                continue

            # SOV heuristic: before root is typically subject/object
            if i < root_idx:
                # Check if next word is a case marker
                next_w = words[i + 1].lower().strip(".,;:!?") if i + 1 < len(words) else ""
                if next_w == "in":
                    # Ergative subject
                    dependencies.append({
                        "head": root,
                        "dep": "nsubj",
                        "tail": w_clean,
                    })
                elif i == 0:
                    # First word is likely subject
                    dependencies.append({
                        "head": root,
                        "dep": "nsubj",
                        "tail": w_clean,
                    })
                else:
                    # Other pre-root words are objects or modifiers
                    dependencies.append({
                        "head": root,
                        "dep": "dobj",
                        "tail": w_clean,
                    })
            elif i > root_idx:
                # Post-root words are auxiliaries or markers
                dependencies.append({
                    "head": root,
                    "dep": "aux",
                    "tail": w_clean,
                })

        return {"root": root, "dependencies": dependencies}

    async def parse_batch(self, sentences: list[str]) -> list[dict]:
        """Parse multiple sentences.

        Args:
            sentences: List of Zolai sentences.

        Returns:
            List of parse results.
        """
        results = []
        for sentence in sentences:
            result = await self.parse(sentence)
            results.append(result)
        return results


# Module-level singleton
_dependency_instance: ZolaiDependency | None = None


def get_dependency(voter: Any = None) -> ZolaiDependency:
    """Get or create the singleton dependency parser instance."""
    global _dependency_instance
    if _dependency_instance is None:
        _dependency_instance = ZolaiDependency(voter=voter)
    return _dependency_instance
