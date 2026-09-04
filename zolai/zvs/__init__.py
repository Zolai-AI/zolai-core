"""ZVS 2018 compliance validator.

Public entry point::

    from zolai.zvs import validate
    report = validate("Pathian tapa hi")   # report.is_valid is False
"""

from __future__ import annotations

from typing import Iterable

from .exceptions import ExceptionRegistry
from .report import Report, Violation
from .rules import Ruleset

__all__ = [
    "ExceptionRegistry",
    "Report",
    "Ruleset",
    "Violation",
    "validate",
]


def validate(
    text: str,
    *,
    source: str = "<text>",
    categories: Iterable[str] | None = None,
    include_noisy: bool = False,
    exceptions: ExceptionRegistry | None = None,
    disabled_rules: Iterable[str] | None = None,
) -> Report:
    """Validate a single string against the ZVS 2018 rules.

    Args:
        text: The text to validate.
        source: A label for the source (file path, lineno, etc.).
        categories: Explicit rule categories to enable (defaults to the
            documented dialect/compound/stem rules).
        include_noisy: Enable the phonotactic noise rules (default off).
        exceptions: An exception registry; ``None`` uses a fresh per-call
            registry (so caller-added exceptions are not shared).
        disabled_rules: Rule ids to exclude from this run.

    Returns:
        A :class:`Report` with any violations found.
    """
    registry = exceptions if exceptions is not None else ExceptionRegistry()

    ruleset = Ruleset(
        categories=categories,
        include_noisy=include_noisy,
        disabled_rules=disabled_rules,
    )

    raw_violations = ruleset.apply(text)

    # Whole-text phrase exception: when a registered historical/source snippet
    # is present, suppress every violation within this text (the source is
    # explicitly marked as not-to-be-corrected).
    phrase_exempt = (
        registry.phrases and registry.suppress_phrase(text)
    )

    # Apply the remaining exceptions: rule-level and token-level.
    violations = [
        v
        for v in raw_violations
        if not (
            phrase_exempt
            or registry.suppresses_rule(v.rule_id)
            or registry.suppresses_token(v.forbidden)
        )
    ]

    return Report(source=source, text=text, violations=violations)
