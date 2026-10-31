"""Content-addressed dataset versions.

A training manifest that only records a timestamp cannot answer "is this the
same dataset I evaluated?" — regenerating the manifest always produces a new
``generated_at`` even when nothing about the data changed. The version here is
a hash of the split hashes, so identical content yields an identical version
and any changed byte (or a split that disappeared) yields a different one.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any


def content_version(files: Mapping[str, Mapping[str, Any]]) -> str:
    """SHA256 over per-file facts; missing files are part of the identity.

    ``files`` maps a split name to the facts recorded by the manifest
    (``exists``, ``sha256``, ``bytes``). Ordering does not matter: names are
    sorted before hashing.
    """
    parts: list[str] = []
    for name in sorted(files):
        facts = files[name]
        if facts.get("exists"):
            parts.append(f"{name}:{facts.get('sha256', '')}:{facts.get('bytes', 0)}")
        else:
            parts.append(f"{name}:missing")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
