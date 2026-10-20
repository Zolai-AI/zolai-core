"""Strict ZVS 2018 compliance for AI-generated answers — the API call path.

Why this module exists (P0, 2026-10-18)
---------------------------------------
``zolai/api/zvs_checker.py`` (the *lenient* checker this replaces on the API
paths) lists the ZVS 2018 deprecated orthography tokens — the set defined in
``zolai/zvs/rules_data.py`` — inside its own ``HISTORICAL_EXCEPTIONS``, so
its ``check_response`` **skips** them: a sentence built entirely from
deprecated forms came back ``is_compliant: True``.  Both user-facing
validation paths (``server.py``'s ``/chat/zolai`` + ``/chat/gemini``
endpoints and ``AnswerValidator``) imported that lenient authority, so
user-facing answers could be rubber-stamped with deprecated orthography —
a direct violation of the org ground truth (ZVS 2018).

The strict authority
--------------------
This module routes those paths onto :func:`zolai.zvs.validate` — the
canonical validator behind engine-registry entry ``zolai.zvs:validate``, the
agent's ``zvs_review`` sanitize pass, the corpus validator and the grammar
agent.  The live MCP ``grammar_check`` tool reaches the same verdict through
``GrammarEditor._validate_zvs`` (``zolai.offline.rule_engine.ZVS_CORRECTIONS``);
both are unconditional forbidden-form matchers — only the lenient
``zvs_checker`` exempts the forbidden forms.

One deliberate difference for AI output
---------------------------------------
``zolai.zvs.validate``'s default exception registry suppresses *historical*
tokens when they are attested in the Tedim Bible corpus — the TDB77
translation uses the old orthography, so in production that whitelist would
once again let an AI answer in the old orthography pass.  An AI answer is
**modern text**, not a quoted scripture/historical snippet, so this adapter
passes an exception registry with **no** rule/token/phrase suppressions: every
ZVS 2018 forbidden form is reported, Bible-attestation or not.  The registry
is pre-seeded with a sentinel ``bible_words`` entry so ``validate`` never
lazy-loads the Bible word set from SQLite — this checker is database-free.

Drop-in compatibility
---------------------
The returned dict keeps the exact shape the lenient checker returned —
``is_compliant`` (bool), ``violations`` (list of ``(forbidden, suggested,
context)`` tuples) and ``corrected_text`` — so every call site is a one-line
import swap.  ``zolai/api/zvs_checker.py`` is untouched and is now a
**deletion candidate** (still referenced only by
``tests/test_zolai_conversation.py``).
"""

from __future__ import annotations

import re
from typing import Any

from ..zvs import validate
from ..zvs.exceptions import ExceptionRegistry

#: No rule/token/phrase suppression for AI answers.  ``bible_words`` is
#: pre-seeded with a sentinel so :func:`zolai.zvs.validate` skips its
#: ``get_bible_words()`` SQLite lazy-load (the token set is empty, so the
#: bible word set could never suppress anything anyway).
_ANSWER_EXCEPTIONS = ExceptionRegistry(
    rule_ids=set(),
    tokens=set(),
    phrases=set(),
    bible_words={"<zvs-strict-answers>"},
)


def check_response(text: str) -> dict[str, Any]:
    """Check ``text`` against ZVS 2018 with the strict authority.

    Returns the lenient checker's dict shape::

        {
            "is_compliant": bool,
            "violations": [(forbidden, suggested, context), ...],
            "corrected_text": str,   # forbidden forms replaced by preferred
        }
    """
    report = validate(
        text,
        source="api:answer",
        context="modern",  # an AI answer is never "scripture"/"historical"
        exceptions=_ANSWER_EXCEPTIONS,
    )
    violations: list[tuple[str, str, str]] = []
    corrected = text
    for v in report.violations:
        suggested = v.preferred or ""
        violations.append((v.forbidden, suggested, v.context))
        if suggested and v.forbidden:
            corrected = re.sub(
                rf"\b{re.escape(v.forbidden)}\b",
                suggested,
                corrected,
                flags=re.IGNORECASE,
            )
    return {
        "is_compliant": not violations,
        "violations": violations,
        "corrected_text": corrected,
    }


#: Same name as the lenient module's entry point — the call sites in
#: ``server.py`` keep their local name and only change the import.
check_zvs_compliance = check_response
