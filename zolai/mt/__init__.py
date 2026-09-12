"""Machine Translation EN↔ZO using Gemini ensemble + dictionary fallback."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)


class ZolaiMT:
    """Machine Translation EN↔ZO using Gemini ensemble."""

    def __init__(
        self,
        voter: Any = None,
        db_path: str | None = None,
    ):
        """Initialize MT.

        Args:
            voter: EnsembleVoter instance for Gemini integration (optional).
            db_path: Path to zolai.db for dictionary fallback.
        """
        self.voter = voter
        self._db_path = db_path
        self._dict_zo_en: dict[str, str] | None = None
        self._dict_en_zo: dict[str, str] | None = None

    def _ensure_loaded(self) -> None:
        """Lazy-load dictionaries from DB."""
        if self._dict_zo_en is not None:
            return
        self._dict_zo_en = {}
        self._dict_en_zo = {}

        db_path = self._db_path or os.environ.get(
            "ZOLAI_DB_PATH", str(
                __import__("pathlib").Path(__file__).resolve().parent.parent.parent.parent
                / "data" / "zolai.db"
            )
        )
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            # ZO→EN
            for row in conn.execute(
                "SELECT zolai, english_clean FROM dictionary "
                "WHERE english_clean IS NOT NULL AND english_clean != '' LIMIT 100000"
            ):
                self._dict_zo_en[row["zolai"].lower()] = row["english_clean"]
            # EN→ZO
            try:
                for row in conn.execute(
                    "SELECT headword, translations_clean FROM dictionary_en_zo "
                    "WHERE translations_clean IS NOT NULL AND translations_clean != '' LIMIT 100000"
                ):
                    self._dict_en_zo[row["headword"].lower()] = row["translations_clean"]
            except Exception:
                # dictionary_en_zo table may not exist
                pass
            conn.close()
            logger.info(
                "Loaded MT dictionaries: ZO→EN=%d, EN→ZO=%d",
                len(self._dict_zo_en), len(self._dict_en_zo),
            )
        except Exception as e:
            logger.warning("Failed to load MT dictionaries from DB: %s", e)

    async def translate_zo_en(self, text: str) -> dict:
        """Translate Zolai to English using Gemini ensemble.

        Args:
            text: Zolai text to translate.

        Returns:
            {"translation": "...", "confidence": 0.9, "method": "ensemble"}
        """
        if self.voter is None:
            return self.translate_dict(text, direction="zo_en")

        prompt = (
            "Translate this Zolai (Tedim) text to English. "
            "Use ZVS 2018 orthography. Output JSON only.\n"
            f"Zolai: {text}\n"
            'Output JSON: {"translation": "...", "confidence": 0.9}'
        )
        try:
            raw = await self.voter.vote(prompt)
            result = json.loads(raw) if isinstance(raw, str) else raw
            data = result.get("result", result)
            return {
                "translation": data.get("translation", ""),
                "confidence": float(data.get("confidence", 0.7)),
                "method": "ensemble",
            }
        except Exception as e:
            logger.warning("Gemini ZO→EN failed, falling back to dict: %s", e)
            return self.translate_dict(text, direction="zo_en")

    async def translate_en_zo(self, text: str) -> dict:
        """Translate English to Zolai using Gemini ensemble.

        Args:
            text: English text to translate.

        Returns:
            {"translation": "...", "confidence": 0.9, "method": "ensemble"}
        """
        if self.voter is None:
            return self.translate_dict(text, direction="en_zo")

        prompt = (
            "Translate this English text to Zolai (Tedim). "
            "Use ZVS 2018 orthography. Use SOV word order. "
            "Use ergative 'in' for transitive subjects. "
            "Output JSON only.\n"
            f"English: {text}\n"
            'Output JSON: {"translation": "...", "confidence": 0.9}'
        )
        try:
            raw = await self.voter.vote(prompt)
            result = json.loads(raw) if isinstance(raw, str) else raw
            data = result.get("result", result)
            return {
                "translation": data.get("translation", ""),
                "confidence": float(data.get("confidence", 0.7)),
                "method": "ensemble",
            }
        except Exception as e:
            logger.warning("Gemini EN→ZO failed, falling back to dict: %s", e)
            return self.translate_dict(text, direction="en_zo")

    def translate_dict(self, word: str, direction: str = "zo_en") -> dict:
        """Dictionary-based translation fallback.

        Args:
            word: Word to translate.
            direction: "zo_en" or "en_zo".

        Returns:
            {"translation": "...", "confidence": 0.5, "method": "dictionary"}
        """
        self._ensure_loaded()

        if direction == "zo_en":
            result = self._dict_zo_en.get(word.lower(), "")
            if not result:
                # Try partial match
                for key, val in self._dict_zo_en.items():
                    if word.lower() in key or key in word.lower():
                        result = val
                        break
        else:
            result = self._dict_en_zo.get(word.lower(), "")
            if not result:
                for key, val in self._dict_en_zo.items():
                    if word.lower() in key or key in word.lower():
                        result = val
                        break

        return {
            "translation": result,
            "confidence": 0.5 if result else 0.0,
            "method": "dictionary",
        }

    def get_word_info(self, word: str) -> dict:
        """Get detailed word information from dictionary.

        Args:
            word: Zolai or English word.

        Returns:
            {"zolai": "...", "english": "...", "pos": "...", "found": True}
        """
        self._ensure_loaded()

        # Try ZO→EN
        en = self._dict_zo_en.get(word.lower(), "")
        if en:
            return {
                "zolai": word,
                "english": en,
                "pos": "",
                "found": True,
                "direction": "zo_en",
            }

        # Try EN→ZO
        zo = self._dict_en_zo.get(word.lower(), "")
        if zo:
            return {
                "zolai": zo,
                "english": word,
                "pos": "",
                "found": True,
                "direction": "en_zo",
            }

        return {
            "zolai": word,
            "english": "",
            "pos": "",
            "found": False,
            "direction": "unknown",
        }


# Module-level singleton
_mt_instance: ZolaiMT | None = None


def get_mt(voter: Any = None, db_path: str | None = None) -> ZolaiMT:
    """Get or create the singleton MT instance."""
    global _mt_instance
    if _mt_instance is None:
        _mt_instance = ZolaiMT(voter=voter, db_path=db_path)
    return _mt_instance
