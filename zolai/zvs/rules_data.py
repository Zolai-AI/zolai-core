"""ZVS 2018 rule data.

All rules here are sourced from the wiki linguistic mandate and the existing
grammar auditor:

- ``zolai-wiki/docs/ZOLAI_LINGUISTIC_MANDATE.md``  (compound orthography, stems)
- ``zolai-wiki/grammar/forbidden_stems_auto.md``   (Stem II nominalizations)
- ``zolai-core/scripts/test_grammar_rules.py``    (dialect forbidden map)
- ``context/code-standards.md`` / ``AGENTS.md``    (forbidden list)

Rule categories:
- ``DIALECT``   -- deprecated non-Tedim forms mapped to standard Tedim.
- ``COMPOUND``  -- split compound orthography that must be joined.
- ``STEM``      -- Stem I nominalizations that must use Stem II.
- ``PHONOTACTIC`` -- phonotactic noise rules (``ti``-cluster, ``c`` + {a,e,o,aw}).
                   These are high-precision but noisy, so they are **off by
                   default** and opt-in behind config.
"""

from __future__ import annotations

from typing import Final, Mapping

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
CATEGORY_DIALECT: Final[str] = "dialect"
CATEGORY_COMPOUND: Final[str] = "compound"
CATEGORY_STEM: Final[str] = "stem"
CATEGORY_PHONOTACTIC: Final[str] = "phonotactic"

# Categories that are validated by default (documented dialect/compound/stem
# rules only). Phonotactic rules are excluded because they are too noisy.
DEFAULT_ENABLED_CATEGORIES: Final[tuple[str, ...]] = (
    CATEGORY_DIALECT,
    CATEGORY_COMPOUND,
    CATEGORY_STEM,
)

# ---------------------------------------------------------------------------
# DIALECT — forbidden form -> preferred standard Tedim form
# Sourced from scripts/test_grammar_rules.py `forbidden_dialect` plus the
# AGENTS.md forbidden list (pathian, ram, fapa, bawipa, siangpahrang, cu, cun).
# ---------------------------------------------------------------------------
DIALECT_FORBIDDEN_TO_PREFERRED: Final[Mapping[str, str]] = {
    "pathian": "pasian",
    "ram": "gam",
    "fapa": "tapa",
    "bawipa": "topa",
    "siangpahrang": "kumpipa",
    "cu": "tua",
    "cun": "tua",
    "suah": "chuak",
    "zalenna": "suahtakna",
    "nunnak": "nuntakna",
}

# ---------------------------------------------------------------------------
# COMPOUND — split multi-word compounds that must be joined as one word
# Sourced from ZOLAI_LINGUISTIC_MANDATE.md §2.3 (compound orthography).
# ---------------------------------------------------------------------------
COMPOUND_SPLIT_TO_PREFERRED: Final[Mapping[str, str]] = {
    "pa sian": "pasian",
    "ta pa": "tapa",
    "na sep": "nasep",
    "lei tung": "leitung",
    "na ding": "nading",
    "hi leh": "hihleh",
}

# ---------------------------------------------------------------------------
# STEM — forbidden Stem I nominalizations (the real corrections from
# grammar/forbidden_stems_auto.md; entries where the "correct" form is
# identical, e.g. kahna/kahna, are intentionally omitted).
# ---------------------------------------------------------------------------
STEM_FORBIDDEN_TO_PREFERRED: Final[Mapping[str, str]] = {
    "sina": "sihna",
    "neina": "neihna",
    "hauna": "hauhna",
    "hakna": "hahna",
    "thatna": "thahna",
    "samna": "sapna",
    "kipanna": "kipatna",
    "piangna": "pian'na",
}

# ---------------------------------------------------------------------------
# PHONOTACTIC — noise-prone rules that are available but OFF by default.
# ---------------------------------------------------------------------------
# Each entry: (rule_id, regex pattern, message)
PHONOTACTIC_PATTERNS: Final[tuple[tuple[str, str, str], ...]] = (
    (
        "PHON_01",
        r"\bti\b",
        "Forbidden 'ti' cluster.",
    ),
    (
        "PHON_02",
        r"\bc[aeo]|caw",
        "Forbidden 'c' + [a,e,o,aw] combination.",
    ),
)
