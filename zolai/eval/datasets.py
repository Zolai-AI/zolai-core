"""Dataset loaders for the offline evaluation package.

Three source styles are supported:

- **DB sets** (runtime source of truth) — the ``eval_sets`` / ``eval_cases``
  tables of the canonical SQLite store. ``--set db`` merges every active set;
  ``--set db:<name>`` selects one (e.g. ``db:smoke``, ``db:eval_v1``).
- ``.jsonl`` — one JSON object per line for the ZVS (``text``), translation
  (``hyp``/``ref``) and QA (``hyp``/``answer``) lanes. Bundled files are
  import/export interchange, not the source of truth (see
  :mod:`zolai.eval.store`).
- Paired ``.txt`` — translation pairs supplied as two line-parallel files
  (``<base>_hyp.txt`` + ``<base>_ref.txt``).

A ``--set`` value is either the literal ``"smoke"`` (read the small bundled
fixtures under :data:`SMOKE_DIR`), a ``db`` / ``db:<name>``, or a path/base
prefix resolved relative to :data:`SMOKE_DIR` (or an absolute/relative path).
Every style yields the same :func:`load_dataset` shape, so callers never care
where the records came from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import store

SMOKE = "smoke"
SMOKE_DIR = Path(__file__).resolve().parent / "sets"
DB_SPEC = "db"

_KINDS = ("zvs", "translation", "qa")


@dataclass(frozen=True)
class DbRef:
    """Sentinel source: evaluation cases that live in the SQLite store.

    Attributes:
        set_name: Set to read, or ``"*"`` (:data:`zolai.eval.store.ALL_SETS`)
            when every active set is merged.
        kind: Case lane — ``zvs``, ``qa`` or ``translation``.
    """

    set_name: str
    kind: str


Source = Path | tuple[Path, Path] | DbRef


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
) -> dict[str, Source]:
    """Return the names/sources that exist for a set specifier.

    Args:
        spec: ``"smoke"``, ``"db"`` (all active sets), ``"db:<name>"`` (one DB
            set), or a path/base prefix.
        base_dir: Optional override directory used to locate ``smoke`` fixtures
            (ignored for DB specifiers).

    Returns:
        A mapping ``kind -> source`` where ``source`` is a ``Path`` for JSONL
        records, a ``(hyp, ref)`` path pair for paired ``.txt`` translation
        files, or a :class:`DbRef` for DB-backed sets. Only sources that exist
        are included.
    """
    if spec == DB_SPEC:
        return {kind: DbRef(store.ALL_SETS, kind) for kind in store.list_kinds()}
    if spec.startswith(f"{DB_SPEC}:"):
        name = spec.split(":", 1)[1]
        return {kind: DbRef(name, kind) for kind in store.list_kinds(name)}
    base = _base_path(spec, base_dir)
    found: dict[str, Source] = {}
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


def _load_db_section(ref: DbRef) -> list[str] | tuple[list[str], list[str]]:
    """Load one DB-backed lane as the same structure the file loaders return.

    Records whose payload lacks the lane's required fields (for example the
    static ``benchmark_qa`` payloads inside a merged ``db`` read) are skipped
    so a malformed or non-metric record can never poison a score.
    """
    records = [case["payload"] for case in store.fetch_cases(ref.set_name, ref.kind)]
    if ref.kind == "zvs":
        return [text for record in records if (text := record.get("text"))]
    hyps: list[str] = []
    refs: list[str] = []
    for record in records:
        hyp = record.get("hyp")
        if not hyp:
            continue
        if ref.kind == "translation":
            gold = record.get("ref")
        else:
            gold = record.get("ref") or record.get("answer")
        if gold is None:
            continue
        hyps.append(hyp)
        refs.append(gold)
    return hyps, refs


def load_dataset(
    spec: str = SMOKE, *, base_dir: str | None = None
) -> dict[str, list[str] | tuple[list[str], list[str]]]:
    """Load every available set section into ready-to-score structures.

    Args:
        spec: ``"smoke"``, ``"db"``, ``"db:<name>"`` or a path/base prefix.
        base_dir: Optional override directory for the ``smoke`` set.

    Returns:
        A mapping with the keys:

        - ``"zvs"`` -> list of texts
        - ``"translation"`` -> ``(hyps, refs)``
        - ``"qa"`` -> ``(hyps, refs)``

        Keys are present only when the corresponding source exists. DB sections
        with no loadable record are omitted, exactly like a missing file.
    """
    sources = resolve_set(spec, base_dir=base_dir)
    data: dict[str, list[str] | tuple[list[str], list[str]]] = {}
    for kind, source in sources.items():
        if isinstance(source, DbRef):
            section = _load_db_section(source)
            loadable = section if isinstance(section, list) else section[0]
            if loadable:
                data[kind] = section
        elif kind == "zvs":
            data["zvs"] = load_zvs(source)  # type: ignore[arg-type]
        elif kind == "translation":
            if isinstance(source, tuple):
                data["translation"] = load_translation_txt(*source)
            else:
                data["translation"] = load_translation(source)
        else:
            data["qa"] = load_qa(source)  # type: ignore[arg-type]
    return data
