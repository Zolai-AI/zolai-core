"""Dataset loaders for the offline evaluation package.

Two record styles are supported:

- ``.jsonl`` — one JSON object per line for the ZVS (``text``), translation
  (``hyp``/``ref``) and QA (``hyp``/``ref``) lanes.
- Paired ``.txt`` — translation pairs supplied as two line-parallel files
  (``<base>_hyp.txt`` + ``<base>_ref.txt``).

A ``--set`` value is either the literal ``"smoke"`` (read the small bundled
fixtures under :data:`SMOKE_DIR`) or a path/base prefix resolved relative to
:data:`SMOKE_DIR` (or an absolute/relative path).
"""

from __future__ import annotations

import json
from pathlib import Path

SMOKE = "smoke"
SMOKE_DIR = Path(__file__).resolve().parent / "sets"

_KINDS = ("zvs", "translation", "qa")


def _base_path(spec: str, base_dir: str | None) -> Path:
    """Resolve ``smoke`` or a path/base prefix to a file-name stem."""
    if spec == SMOKE:
        return (Path(base_dir) if base_dir else SMOKE_DIR) / "smoke"
    path = Path(spec)
    if base_dir is not None and not path.is_absolute():
        return Path(base_dir) / path
    return path


def resolve_set(
    spec: str = SMOKE, *, base_dir: str | None = None
) -> dict[str, Path | tuple[Path, Path]]:
    """Return the names/sources that exist for a set specifier.

    Args:
        spec: ``"smoke"`` or a path/base prefix.
        base_dir: Optional override directory used to locate ``smoke`` fixtures.

    Returns:
        A mapping ``kind -> source`` where ``source`` is a ``Path`` for JSONL
        records or a ``(hyp, ref)`` path pair for paired ``.txt`` translation
        files. Only sources that exist are included.
    """
    base = _base_path(spec, base_dir)
    found: dict[str, Path | tuple[Path, Path]] = {}
    for kind in _KINDS:
        jsonl = Path(f"{base}_{kind}.jsonl")
        if jsonl.exists():
            found[kind] = jsonl
    if "translation" not in found:
        hyp = Path(f"{base}_hyp.txt")
        ref = Path(f"{base}_ref.txt")
        if hyp.exists() and ref.exists():
            found["translation"] = (hyp, ref)
    return found


def _iter_records(path: Path):
    """Yield parsed JSON objects from a JSONL file."""
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def load_zvs(path: Path) -> list[str]:
    """Load ``text`` records from a ZVS JSONL set."""
    return [record["text"] for record in _iter_records(path) if record.get("text")]


def _load_paired(path: Path) -> tuple[list[str], list[str]]:
    """Load ``hyp``/``ref`` fields from a JSONL set (translation or QA)."""
    hyps: list[str] = []
    refs: list[str] = []
    for record in _iter_records(path):
        hyps.append(record.get("hyp", ""))
        refs.append(record.get("ref", "") or record.get("answer", ""))
    return hyps, refs


def load_translation(path: Path) -> tuple[list[str], list[str]]:
    """Load ``hyp``/``ref`` pairs from a translation JSONL set."""
    return _load_paired(path)


def load_qa(path: Path) -> tuple[list[str], list[str]]:
    """Load ``hyp``/``answer`` pairs from a QA JSONL set."""
    return _load_paired(path)


def load_translation_txt(hyp_path: Path, ref_path: Path) -> tuple[list[str], list[str]]:
    """Load line-parallel paired ``.txt`` translation files."""
    hyps = [line for line in hyp_path.read_text(encoding="utf-8").splitlines() if line]
    refs = [line for line in ref_path.read_text(encoding="utf-8").splitlines() if line]
    return hyps, refs


def load_dataset(
    spec: str = SMOKE, *, base_dir: str | None = None
) -> dict[str, list[str] | tuple[list[str], list[str]]]:
    """Load every available set section into ready-to-score structures.

    Args:
        spec: ``"smoke"`` or a path/base prefix.
        base_dir: Optional override directory for the ``smoke`` set.

    Returns:
        A mapping with the keys:

        - ``"zvs"`` -> list of texts
        - ``"translation"`` -> ``(hyps, refs)``
        - ``"qa"`` -> ``(hyps, refs)``

        Keys are present only when the corresponding source file exists.
    """
    sources = resolve_set(spec, base_dir=base_dir)
    data: dict[str, list[str] | tuple[list[str], list[str]]] = {}
    if "zvs" in sources:
        data["zvs"] = load_zvs(sources["zvs"])  # type: ignore[arg-type]
    if "translation" in sources:
        source = sources["translation"]
        if isinstance(source, tuple):
            data["translation"] = load_translation_txt(*source)
        else:
            data["translation"] = load_translation(source)
    if "qa" in sources:
        data["qa"] = load_qa(sources["qa"])  # type: ignore[arg-type]
    return data
