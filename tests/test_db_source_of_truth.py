"""The canonical database must have exactly one resolver.

``zolai/config.py`` ``paths.db`` is the whole answer to "which database?" — the
``ZOLAI_DB_PATH`` / ``ZOLAI_DATA_ROOT`` env vars are applied there and nowhere
else. Every module that re-reads those variables or derives the filename from
``__file__`` has, in this repo, read or written a different file than the rest of
the stack: the eval store, five API analyzers, the QA/MT/NER loaders, and two
maintenance scripts all did until 2026-10-27. These checks are the regression
gate for that class of bug, not for style.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from zolai import config as config_module
from zolai.config import config

PACKAGE_ROOT = Path(config_module.__file__).resolve().parent
CONFIG_PY = PACKAGE_ROOT / "config.py"

#: Env vars that only :mod:`zolai.config` may read. ``ZOLAI_EVAL_DB_PATH`` is
#: absent because the one place honouring it migrates the *retired* side store.
DB_ENV_VARS = frozenset({"ZOLAI_DB_PATH", "ZOLAI_DATA_ROOT"})

CANONICAL_FILENAME = "zolai.db"


def _package_sources() -> list[Path]:
    return sorted(p for p in PACKAGE_ROOT.rglob("*.py") if p != CONFIG_PY)


def _env_reads(tree: ast.AST) -> list[int]:
    """Lines that read a DB env var (``.get("X")``, ``getenv("X")``, ``["X"]``)."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            if name in {"get", "getenv"} and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and first.value in DB_ENV_VARS:
                    lines.append(node.lineno)
        elif isinstance(node, ast.Subscript):
            base = node.value
            if (
                isinstance(base, ast.Attribute)
                and base.attr == "environ"
                and isinstance(node.slice, ast.Constant)
                and node.slice.value in DB_ENV_VARS
            ):
                lines.append(node.lineno)
    return lines


def _file_derived_db_paths(tree: ast.AST) -> list[int]:
    """Lines building ``<somewhere>/zolai.db`` from ``__file__``."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            strings = {c.value for c in ast.walk(node) if isinstance(c, ast.Constant) and isinstance(c.value, str)}
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            if CANONICAL_FILENAME in strings and "__file__" in names:
                lines.append(node.lineno)
    return lines


def _offenders(scan) -> str:
    """Render ``file:line`` offenders for a scan function over the package."""
    root = PACKAGE_ROOT.parent
    hits = []
    for path in _package_sources():
        for line in scan(ast.parse(path.read_text(encoding="utf-8"))):
            hits.append(f"  {path.relative_to(root)}:{line}")
    return "\n".join(hits)


def test_package_never_re_reads_db_env() -> None:
    """Only ``config.py`` may decide the database from the environment."""
    offenders = _offenders(_env_reads)
    assert not offenders, (
        "these modules read the DB env vars instead of `config.paths.db`, so a "
        "path set in config and a path set in env can disagree at runtime:\n" + offenders
    )


def test_package_never_derives_the_db_path_from_dunder_file() -> None:
    """A path computed from a module's own location is a second source of truth."""
    offenders = _offenders(_file_derived_db_paths)
    assert not offenders, (
        "these modules build the canonical DB path from `__file__`:\n" + offenders
    )


def test_only_config_names_the_canonical_file() -> None:
    """No package module hardcodes the filename ``zolai.db``."""
    hits = [
        f"{path.relative_to(PACKAGE_ROOT.parent)}:{node.lineno}"
        for path in _package_sources()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value == CANONICAL_FILENAME
    ]
    assert not hits, "only zolai/config.py may name the canonical database file:\n" + "\n".join(f"  {h}" for h in hits)


@pytest.fixture
def seeded_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tiny database at ``config.paths.db`` with the rows the loaders expect.

    ``Paths`` is a dataclass whose defaults read the environment once at import,
    so patching ``config.paths.db`` is the only way to move the whole stack — and
    the only way to prove a module actually followed it.
    """
    db = tmp_path / "moved.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE dictionary (zolai TEXT, english_clean TEXT, pos TEXT)")
    conn.execute("INSERT INTO dictionary VALUES ('vantung', 'forest', 'N')")
    conn.execute("CREATE TABLE dictionary_en_zo (headword TEXT, translations_clean TEXT)")
    conn.execute("INSERT INTO dictionary_en_zo VALUES ('forest', 'vantung')")
    conn.execute(
        "CREATE TABLE bible_verses (book_name TEXT, chapter INTEGER, verse INTEGER, zo_tdb77 TEXT, en_kJV TEXT)"
    )
    conn.execute("INSERT INTO bible_verses VALUES ('Genesis', 1, 1, 'Thuatun in', 'In the beginning')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(config.paths, "db", db)
    return db


def test_loaders_follow_the_configured_database(seeded_db: Path) -> None:
    """QA, MT and NER read from wherever ``config.paths.db`` points."""
    from zolai.mt import ZolaiMT
    from zolai.ner import ZolaiNER
    from zolai.qa import ZolaiQA

    qa = ZolaiQA()
    qa._ensure_loaded()
    assert [v["book"] for v in qa._bible_cache] == ["Genesis"]

    mt = ZolaiMT()
    mt._ensure_loaded()
    assert mt._dict_zo_en["vantung"] == "forest"
    assert mt._dict_en_zo["forest"] == "vantung"

    ner = ZolaiNER()
    ner._ensure_loaded()
    assert ner._dict_cache["vantung"]["pos"] == "N"


def test_eval_store_follows_the_configured_database(seeded_db: Path) -> None:
    """The eval store schema lands in the configured DB, not beside the old one."""
    from zolai.eval import store

    assert store.ensure_schema() == seeded_db
    conn = sqlite3.connect(seeded_db)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()
    assert {"eval_sets", "eval_cases"} <= tables
