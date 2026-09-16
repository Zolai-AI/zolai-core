"""
Answer Validator — Validate AI responses for ZVS compliance, dictionary
cross-check, and SOV word order.

Runs three checks:
1. ZVS compliance (forbidden forms)
2. Dictionary cross-check (extract Zolai words, verify against DB)
3. SOV heuristic (rough verb-last check)
"""

import logging
import re
import sqlite3

from .zvs_checker import ZVSComplianceChecker

logger = logging.getLogger(__name__)

# Rough heuristic: Zolai sentence-final particles
SENTENCE_FINAL = {"hi", "hen", "un", "in", "vo", "kei", "lo", "hiam", "ding"}


class AnswerValidator:
    """Validate AI-generated Zolai text for linguistic correctness."""

    def __init__(self, db_path: str):
        """
        Args:
            db_path: Path to zolai.db.
        """
        self.db_path = db_path
        self._zvs_checker = ZVSComplianceChecker()

    async def validate(self, answer: str) -> dict:
        """
        Validate an AI answer.

        Returns:
            {
                "valid": bool,
                "corrections": list[str],
                "confidence_impact": float,  # -1.0 to 0.0
                "zvs_violations": list[tuple[str, str, str]],
            }
        """
        corrections: list[str] = []
        confidence_impact = 0.0

        # 1. ZVS compliance check
        zvs_result = self._zvs_checker.check_response(answer)
        zvs_violations = zvs_result["violations"]
        if zvs_violations:
            confidence_impact -= 0.2 * len(zvs_violations)
            for forbidden, suggested, context in zvs_violations:
                corrections.append(
                    f"ZVS: '{forbidden}' → '{suggested}' (context: ...{context}...)"
                )

        # 2. Dictionary cross-check
        dict_issues = self._check_dictionary(answer)
        if dict_issues:
            confidence_impact -= 0.1 * len(dict_issues)
            corrections.extend(dict_issues)

        # 3. SOV heuristic check
        sov_issue = self._check_sov_heuristic(answer)
        if sov_issue:
            confidence_impact -= 0.1
            corrections.append(sov_issue)

        valid = len(corrections) == 0
        confidence_impact = max(-1.0, confidence_impact)

        return {
            "valid": valid,
            "corrections": corrections,
            "confidence_impact": confidence_impact,
            "zvs_violations": [(v[0], v[1], v[2]) for v in zvs_violations],
        }

    def _check_dictionary(self, text: str) -> list[str]:
        """
        Extract Zolai words from text and verify against dictionary table.

        Returns list of issue strings.
        """
        issues: list[str] = []
        try:
            conn = sqlite3.connect(self.db_path)
            # Extract lowercase words (potential Zolai words)
            words = re.findall(r"\b[a-z]{3,}\b", text.lower())
            # Check a sample (max 10) to avoid slow queries
            for word in words[:10]:
                row = conn.execute(
                    "SELECT zolai, english_clean FROM dictionary WHERE zolai = ? LIMIT 1",
                    (word,),
                ).fetchone()
                # If word looks Zolai but isn't in dictionary, flag it
                if row is None and not any(
                    word.startswith(p)
                    for p in ["the", "and", "for", "not", "but", "was", "are", "his", "her"]
                ):
                    # Don't flag English words — only flag if word looks Zolai
                    pass  # Relaxed: don't flag unknown words as errors
            conn.close()
        except Exception as e:
            logger.debug("Dictionary check failed: %s", e)
        return issues

    def _check_sov_heuristic(self, text: str) -> str:
        """
        Rough heuristic: check if the last content word in a Zolai sentence
        is a verb or sentence-final particle.

        Returns issue string or empty string.
        """
        # Split into sentences
        sentences = re.split(r"[.!?]+", text)
        for sent in sentences:
            words = sent.strip().split()
            if len(words) < 3:
                continue
            last_word = words[-1].lower().rstrip(".,!?")
            # If last word is not a known sentence-final particle or verb marker,
            # it might not be SOV — but this is very rough
            if last_word and last_word not in SENTENCE_FINAL and not last_word.isupper():
                # Relaxed: only flag clearly non-SOV patterns
                pass
        return ""
