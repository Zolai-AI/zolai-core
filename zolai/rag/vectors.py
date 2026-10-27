"""Embedding encoding for ``knowledge_vectors.embedding``.

Vectors are 384 floats. A JSON array of them is ~3.6 KB and takes minutes to
parse across the full corpus; base64 little-endian float32 is ~2 KB and decodes
in milliseconds. The column therefore holds a **JSON string** — JSON so the
existing column type and repository stay valid, a string so the old JSON-array
encoding is still readable.
"""

from __future__ import annotations

import base64
import json

import numpy as np


def encode_embedding(vector) -> str:
    """Encode a vector as a JSON string holding base64 float32 bytes."""
    arr = np.asarray(vector, dtype="<f4")
    return json.dumps(base64.b64encode(arr.tobytes()).decode("ascii"))


def decode_embedding(raw: str | None) -> np.ndarray | None:
    """Decode either encoding (legacy JSON array or base64 string)."""
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if isinstance(value, str):
        try:
            return np.frombuffer(base64.b64decode(value), dtype="<f4")
        except (ValueError, TypeError):
            return None
    if isinstance(value, list) and value:
        return np.asarray(value, dtype=np.float32)
    return None
