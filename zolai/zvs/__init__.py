"""ZVS 2018 compliance validator.

Public entry point::

    from zolai.zvs import validate
    report = validate("Ka ram a tam.")   # report.is_valid is False (no default exception)
    report = validate("Pathian tapa hi.", context="scripture")  # report.is_valid is True (historical default exception)
"""

from __future__ import annotations

import re
from typing import Iterable, Literal

from ..data.database import get_manager
from .exceptions import ExceptionRegistry
from .report import Report, Violation
from .rules import Ruleset
from .rules_data import DEFAULT_EXCEPTIONS

__all__ = [
    "ExceptionRegistry",
    "Report",
    "Ruleset",
    "Violation",
    "validate",
]

ContextType = Literal["modern", "historical", "scripture", "auto"]

# Patterns that indicate historical/scripture context
# Full book names
BOOK_NAMES_FULL = (
    "genesis|exodus|leviticus|numbers|deuteronomy|joshua|judges|ruth|"
    "samuel|kings|chronicles|ezra|nehemiah|esther|job|psalms|proverbs|"
    "ecclesiastes|song of solomon|isaiah|jeremiah|lamentations|ezekiel|"
    "daniel|hosea|joel|amos|obadiah|jonah|micah|nahum|habakkuk|zephaniah|"
    "haggai|zechariah|malachi|matthew|mark|luke|john|acts|romans|"
    "corinthians|galatians|ephesians|philippians|colossians|thessalonians|"
    "timothy|titus|philemon|hebrews|james|peter|jude|revelation"
)
# Abbreviated book names
BOOK_NAMES_ABBREV = (
    "gen|exod|lev|num|deut|josh|judg|ruth|1?\\s*sam|2?\\s*sam|"
    "1?\\s*king|2?\\s*king|1?\\s*chron|2?\\s*chron|ezra|neh|esth|job|ps|"
    "prov|eccl|song|isa|jer|lam|ezek|dan|hosea|joel|amos|obad|jonah|mic|"
    "nah|hab|zeph|hag|zech|mal|matt|mark|luke|john|acts|rom|1?\\s*cor|"
    "2?\\s*cor|gal|eph|phil|col|1?\\s*thess|2?\\s*thess|1?\\s*tim|"
    "2?\\s*tim|titus|phlm|heb|jas|1?\\s*pet|2?\\s*pet|1?\\s*john|"
    "2?\\s*john|3?\\s*john|jude|rev"
)

HISTORICAL_MARKERS = [
    rf"\b({BOOK_NAMES_FULL})\s+\d+:\d+\b",
    rf"\b({BOOK_NAMES_ABBREV})\s+\d+:\d+\b",
    r"\b(tedim\s+1932|lai\s+siangtho|kanaan\s+ramah|heshbon\s+kumpipa|persia\s+kumpipa)\b",
]

HISTORICAL_PATTERNS = [re.compile(p, re.IGNORECASE) for p in HISTORICAL_MARKERS]


# Cached Bible word set
_bible_words_cache: set[str] | None = None


def get_bible_words() -> set[str]:
    """Return cached set of all words in the Bible database."""
    global _bible_words_cache
    if _bible_words_cache is None:
        mgr = get_manager()
        _bible_words_cache = mgr.get_bible_words()
    return _bible_words_cache



def _detect_context(text: str) -> Literal["modern", "historical", "scripture"]:
    """Auto-detect whether text is modern, historical, or scripture.

    Returns:
        - "scripture" if text contains Bible verse references
        - "historical" if text contains known historical phrases
        - "modern" otherwise
    """
    lowered = text.lower()

    # Check for Bible verse references (scripture)
    for pattern in HISTORICAL_PATTERNS[:2]:  # First two are verse reference patterns
        if pattern.search(lowered):
            return "scripture"

    # Check for historical phrases
    for pattern in HISTORICAL_PATTERNS[2:]:
        if pattern.search(lowered):
            return "historical"

    return "modern"


def validate(
    text: str,
    *,
    source: str = "<text>",
    categories: Iterable[str] | None = None,
    include_noisy: bool = False,
    exceptions: ExceptionRegistry | None = None,
    disabled_rules: Iterable[str] | None = None,
    context: ContextType = "auto",
) -> Report:
    """Validate a single string against the ZVS 2018 rules.

    Args:
        text: The text to validate.
        source: A label for the source (file path, lineno, etc.).
        categories: Explicit rule categories to enable (defaults to the
            documented dialect/compound/stem rules).
        include_noisy: Enable the phonotactic noise rules (default off).
        exceptions: An exception registry; ``None`` uses the module's seeded
            historical ``DEFAULT_EXCEPTIONS`` (classic Bible-era / kingdom-era
            forms and phrases). Callers that pass a registry override defaults.
        disabled_rules: Rule ids to exclude from this run.
        context: Validation context - "modern" (default, flags historical forms),
            "historical" (allows historical tokens), "scripture" (allows historical
            tokens for Bible text), or "auto" (detect from text content).

    Returns:
        A :class:`Report` with any violations found.
    """
    registry = exceptions if exceptions is not None else DEFAULT_EXCEPTIONS

    # Pass Bible words to the registry for historical token verification
    if not registry.bible_words:
        registry.bible_words = get_bible_words()

    ruleset = Ruleset(
        categories=categories,
        include_noisy=include_noisy,
        disabled_rules=disabled_rules,
    )

    raw_violations = ruleset.apply(text)

    # Resolve context
    resolved_context: Literal["modern", "historical", "scripture"]
    if context == "auto":
        resolved_context = _detect_context(text)
    else:
        resolved_context = context

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
            or registry.suppresses_token(v.forbidden, resolved_context)
        )
    ]

    return Report(source=source, text=text, violations=violations)

