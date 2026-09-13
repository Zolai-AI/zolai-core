"""Exception registry for the ZVS 2018 validator.

Exceptions let legitimate vocabulary and historical/source text pass without
being flagged. This is important so quoted scripture, historical records, or
loanword contexts are never silently "corrected".

Three kinds of exceptions are supported:

- ``rule_ids``  -- fully disable a rule id (e.g. run everything except DIALECT_03).
- ``tokens``    -- ignore a specific forbidden token everywhere.
- ``phrases``   -- allow whole text snippets; any violation found inside a
                   registered phrase is suppressed (for historical quotes).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ContextType = Literal["modern", "historical", "scripture", "auto"]


@dataclass
class ExceptionRegistry:
    """Mutable registry of ZVS exceptions.

    Exceptions let legitimate vocabulary and historical/source text pass without
    being flagged. This is important so quoted scripture, historical records, or
    loanword contexts are never silently "corrected".

    Three kinds of exceptions are supported:

    - ``rule_ids``  -- fully disable a rule id (e.g. run everything except DIALECT_03).
    - ``tokens``    -- ignore a specific forbidden token everywhere.
    - ``phrases``   -- allow whole text snippets; any violation found inside a
                       registered phrase is suppressed (for historical quotes).

    Historical tokens (pathian, bawipa, siangpahrang, fapa, zalenna, cun, cu)
    are ONLY suppressed if they actually appear in the Tedim Bible database
    (bible_verses table, zo_tdb77 or zo_tedim2010 columns). This ensures that
    modern/generated text using historical forms is flagged, while only
    exact Bible-verified usages are exempt.
    """

    rule_ids: set[str] = field(default_factory=set)
    tokens: set[str] = field(default_factory=set)
    phrases: set[str] = field(default_factory=set)
    bible_words: set[str] = field(default_factory=set)

    # Historical tokens that should only be suppressed if they appear in the Bible
    HISTORICAL_TOKENS: set[str] = field(
        default_factory=lambda: {
            "pathian", "bawipa", "siangpahrang", "fapa", "zalenna", "cun", "cu"
        },
        init=False,
        repr=False,
    )

    # -- registration -------------------------------------------------------
    def add_rule(self, rule_id: str) -> None:
        """Suppress an entire rule by id."""
        self.rule_ids.add(rule_id)

    def add_token(self, token: str) -> None:
        """Ignore a specific forbidden token everywhere (case-insensitive)."""
        self.tokens.add(token.lower())

    def add_phrase(self, phrase: str) -> None:
        """Allow a full text snippet; violations contained in it are ignored."""
        self.phrases.add(phrase.strip().lower())

    # -- queries ------------------------------------------------------------
    def suppresses_rule(self, rule_id: str) -> bool:
        """True if the whole rule should be ignored."""
        return rule_id in self.rule_ids

    def suppresses_token(self, forbidden: str, context: ContextType = "modern") -> bool:
        """True if a specific forbidden token should be ignored.

        Historical tokens (e.g., pathian, bawipa, siangpahrang, fapa, zalenna, cun, cu)
        are only suppressed if:
        1. They are registered in the tokens set, AND
        2. They actually appear in the Bible database (bible_words set)

        Non-historical tokens are suppressed everywhere if registered.
        """
        token_lower = forbidden.lower()
        if token_lower not in self.tokens:
            return False

        # If it's a historical token, only suppress if it appears in the Bible
        if token_lower in self.HISTORICAL_TOKENS:
            return token_lower in self.bible_words

        # Non-historical tokens are suppressed everywhere
        return True

    def suppress_phrase(self, text: str) -> bool:
        """True if the text matches/sits inside a registered allowed phrase."""
        if not self.phrases:
            return False
        lowered = text.lower()
        return any(phrase in lowered for phrase in self.phrases)

    def as_dict(self) -> dict[str, list[str]]:
        """Stable, JSON-serialisable snapshot of the registry."""
        return {
            "rule_ids": sorted(self.rule_ids),
            "tokens": sorted(self.tokens),
            "phrases": sorted(self.phrases),
        }

