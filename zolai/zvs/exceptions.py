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


@dataclass
class ExceptionRegistry:
    """Mutable registry of ZVS exceptions."""

    rule_ids: set[str] = field(default_factory=set)
    tokens: set[str] = field(default_factory=set)
    phrases: set[str] = field(default_factory=set)

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

    def suppresses_token(self, forbidden: str) -> bool:
        """True if a specific forbidden token should be ignored."""
        return forbidden.lower() in self.tokens

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
