"""Evidence grounding utilities for building verification prompts."""

from __future__ import annotations

import json
from typing import Any


def build_context(evidence_list: list[dict[str, Any]]) -> str:
    """Format a list of evidence items into a prompt-ready context string.

    Each evidence item is a dict with keys like:
        - ``"source"``: where the evidence came from (e.g. ``"dictionary"``, ``"bible"``)
        - ``"text"``: the evidence content
        - ``"confidence"``: optional confidence score
        - ``"metadata"``: optional extra info

    Args:
        evidence_list: List of evidence dicts.

    Returns:
        A formatted string suitable for injection into a prompt.
    """
    if not evidence_list:
        return "No evidence available."

    parts: list[str] = []
    for i, ev in enumerate(evidence_list, 1):
        source = ev.get("source", "unknown")
        text = ev.get("text", "")
        confidence = ev.get("confidence")
        meta = ev.get("metadata")

        header = f"[Evidence {i}] Source: {source}"
        if confidence is not None:
            header += f" (confidence: {confidence:.2f})"

        parts.append(header)
        if text:
            parts.append(f"  Content: {text}")
        if meta:
            parts.append(f"  Metadata: {json.dumps(meta, ensure_ascii=False)}")

    return "\n".join(parts)


def inject_evidence(
    prompt_template: str,
    evidence_context: str,
    candidate_json: dict[str, Any],
) -> str:
    """Fill a prompt template with evidence and candidate data.

    The template must contain ``{{evidence}}`` and ``{{candidate}}``
    placeholders (jinja2-style, but simple string replacement — no
    engine dependency).

    Args:
        prompt_template: The prompt template string.
        evidence_context: Pre-formatted evidence string from :func:`build_context`.
        candidate_json: Candidate data as a dict (will be JSON-serialised).

    Returns:
        The filled prompt string.

    Raises:
        ValueError: If required placeholders are missing from the template.
    """
    if "{{evidence}}" not in prompt_template:
        raise ValueError("Prompt template must contain {{evidence}} placeholder")
    if "{{candidate}}" not in prompt_template:
        raise ValueError("Prompt template must contain {{candidate}} placeholder")

    candidate_str = json.dumps(candidate_json, ensure_ascii=False, indent=2)

    return (
        prompt_template
        .replace("{{evidence}}", evidence_context)
        .replace("{{candidate}}", candidate_str)
    )
