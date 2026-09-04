"""Rule compilation and application for the ZVS 2018 validator.

Rules are loaded from :mod:`zolai.zvs.rules_data` and compiled into a
:class:`Ruleset`. Compilation produces regex-based :class:`Rule` objects that
scan text and emit :class:`~zolai.zvs.report.Violation` objects. Phonotactic
rules are excluded unless requested, since they are too noisy for general use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from . import rules_data as data
from .report import Violation


@dataclass(frozen=True)
class Rule:
    """A compiled, matchable ZVS rule."""

    rule_id: str
    category: str
    forbidden: str
    preferred: str | None
    message: str
    pattern: re.Pattern[str]
    noisy: bool = False

    def find(self, text: str) -> list[Violation]:
        """Return violations for every match of this rule in ``text``."""
        violations: list[Violation] = []
        for match in self.pattern.finditer(text):
            violations.append(
                Violation(
                    rule_id=self.rule_id,
                    category=self.category,
                    forbidden=self.forbidden,
                    preferred=self.preferred,
                    message=self.message,
                    start=match.start(),
                    end=match.end(),
                    context=text[max(0, match.start() - 30) : match.end() + 30],
                )
            )
        return violations


def _word_boundary(token: str) -> re.Pattern[str]:
    """Match ``token`` as a whole lowercase word (word boundaries)."""
    escaped = re.escape(token)
    return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Match a split compound phrase as a sequence of whole words."""
    words = "\\s+".join(re.escape(w) for w in phrase.split())
    return re.compile(rf"(?<!\w){words}(?!\w)", re.IGNORECASE)


def default_rules() -> list[Rule]:
    """Build the default (dialect + compound + stem) rule set."""
    rules: list[Rule] = []

    for i, (forbidden, preferred) in enumerate(
        data.DIALECT_FORBIDDEN_TO_PREFERRED.items(), start=1
    ):
        rules.append(
            Rule(
                rule_id=f"DIALECT_{i:02d}",
                category=data.CATEGORY_DIALECT,
                forbidden=forbidden,
                preferred=preferred,
                message=(
                    f"Use standard Tedim '{preferred}' instead of '{forbidden}'."
                ),
                pattern=_word_boundary(forbidden),
            )
        )

    for i, (split, preferred) in enumerate(
        data.COMPOUND_SPLIT_TO_PREFERRED.items(), start=1
    ):
        rules.append(
            Rule(
                rule_id=f"COMPOUND_{i:02d}",
                category=data.CATEGORY_COMPOUND,
                forbidden=split,
                preferred=preferred,
                message=f"Write '{preferred}' as a single joined word.",
                pattern=_phrase_pattern(split),
            )
        )

    for i, (forbidden, preferred) in enumerate(
        data.STEM_FORBIDDEN_TO_PREFERRED.items(), start=1
    ):
        rules.append(
            Rule(
                rule_id=f"STEM_{i:02d}",
                category=data.CATEGORY_STEM,
                forbidden=forbidden,
                preferred=preferred,
                message=(
                    f"Use Stem II nominalization '{preferred}' instead of "
                    f"'{forbidden}'."
                ),
                pattern=_word_boundary(forbidden),
            )
        )

    return rules


def phonotactic_rules() -> list[Rule]:
    """Build the noise-prone phonotactic rules (off by default)."""
    rules: list[Rule] = []
    for rule_id, raw_pattern, message in data.PHONOTACTIC_PATTERNS:
        rules.append(
            Rule(
                rule_id=rule_id,
                category=data.CATEGORY_PHONOTACTIC,
                forbidden=raw_pattern,
                preferred=None,
                message=message,
                pattern=re.compile(raw_pattern, re.IGNORECASE),
                noisy=True,
            )
        )
    return rules


def all_rules() -> list[Rule]:
    """All compiled rules (default + phonotactic)."""
    return default_rules() + phonotactic_rules()


class Ruleset:
    """A configurable collection of active ZVS rules."""

    def __init__(
        self,
        rules: Sequence[Rule] | None = None,
        *,
        categories: Iterable[str] | None = None,
        include_noisy: bool = False,
        disabled_rules: Iterable[str] | None = None,
    ) -> None:
        enabled_categories = set(categories or data.DEFAULT_ENABLED_CATEGORIES)
        disabled = set(disabled_rules or ())

        self.rules: list[Rule] = []
        for rule in rules if rules is not None else all_rules():
            if rule.rule_id in disabled:
                continue
            if rule.noisy and not include_noisy:
                continue
            if rule.category not in enabled_categories:
                continue
            self.rules.append(rule)

    def apply(
        self,
        text: str,
        *,
        exception_phrase_suppress: bool = False,
    ) -> list[Violation]:
        """Run every active rule over ``text``, returning violations.

        ``exception_phrase_suppress`` is handled by the caller-level validator
        (which has access to the full exception registry); it is kept here to
        allow rule-level suppression of phrase-based exceptions.
        """
        violations: list[Violation] = []
        for rule in self.rules:
            violations.extend(rule.find(text))
        return violations
