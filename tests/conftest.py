"""Session-wide test isolation.

Three process-global facts made this suite unsafe to run:

* ``zolai.config.config.paths`` is a single mutable ``Paths`` instance. Several
  test modules assign to ``paths.db`` / ``paths.data`` directly rather than
  through ``monkeypatch``, and the ~20 modules that open their own
  ``sqlite3.connect()`` read that instance at call time. A test that raised
  before its own restore line left every later test pointed at a
  since-deleted ``/tmp`` database.
* ``zolai.data.database._manager`` caches one ``DatabaseManager`` (and its
  connection pool) for the life of the process. The first test to call
  ``get_manager()`` pinned the path for every later test, whichever way the
  config had since moved.
* The default path *is* the 2.4 GB corpus. ``get_engine()`` handed out the real
  database to any test that never patched anything, so a write-behaviour test
  (``test_discovery_pipeline``) committed 120 hypothesis rows into it while the
  rest of the suite was reading it.

The first two are unwound around every test. The third is fenced: reading the
corpus is allowed, modifying it from inside the test process is a failure. Set
``ZOLAI_ALLOW_CANONICAL_WRITES=1`` to lift the fence deliberately.

The fence watches *statements issued by this process* rather than the file,
because the corpus is a live database: ``zolai rag build``, the dev server, or
an agent session may be writing it while the suite runs, and any file- or
header-level signal (size, mtime, ``PRAGMA data_version``) attributes their
writes to whichever test happens to be in flight.
"""

from __future__ import annotations

import os
import re
import sqlite3
import traceback
from collections.abc import Iterator
from dataclasses import fields
from pathlib import Path

import pytest

from zolai import zvs as zvs_mod
from zolai.config import config
from zolai.data import database as database_mod

#: Every ``Paths`` dataclass field — the properties derive from these.
_PATH_FIELDS = tuple(f.name for f in fields(type(config.paths)))

#: The corpus database, captured before any test had a chance to move it.
CANONICAL_DB = Path(config.paths.db).resolve()

_FENCE_LIFTED = os.environ.get("ZOLAI_ALLOW_CANONICAL_WRITES") == "1"

#: Statement prefixes that change database content. ``PRAGMA`` is deliberately
#: absent: opening a WAL database issues pragmas that rewrite the header
#: without touching a row, which is not what this fence is for.
_WRITE_STATEMENT = re.compile(
    r"^\s*(?:BEGIN\s+(?:IMMEDIATE|EXCLUSIVE)|INSERT|REPLACE|UPDATE|DELETE|"
    r"CREATE|DROP|ALTER|VACUUM|REINDEX)",
    re.IGNORECASE,
)

#: Writes this process issued against the corpus during the current test.
_CANONICAL_WRITES: list[tuple[str, str]] = []


def _is_canonical(target: object) -> bool:
    """Does this sqlite3 target name resolve to the corpus file?"""
    name = str(target)
    if name.startswith("file:"):
        name = name[5:].split("?", 1)[0]
    if not name or name == ":memory:":
        return False
    try:
        return Path(name).resolve() == CANONICAL_DB
    except OSError:
        return False


def _note_write(statement: object) -> None:
    sql = str(statement)
    if _WRITE_STATEMENT.match(sql):
        stack = "".join(traceback.format_stack(limit=16)[:-4])
        _CANONICAL_WRITES.append((sql[:160], stack))


def _watch(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Trace every statement issued on a connection to the corpus.

    ``set_trace_callback`` sees statements from cursors as well as from the
    connection, which is how SQLAlchemy executes — subclassing
    ``sqlite3.Connection`` and overriding ``execute`` catches nothing that goes
    through a cursor.
    """
    try:
        conn.set_trace_callback(_note_write)
    except (AttributeError, sqlite3.Error):  # pragma: no cover — non-sqlite3 factory
        pass
    return conn


_CONNECT = sqlite3.connect


def _fenced_connect(database, *args, **kwargs):  # noqa: ANN001, ANN002
    conn = _CONNECT(database, *args, **kwargs)
    return _watch(conn) if _is_canonical(database) else conn


if not _FENCE_LIFTED:
    # ``sqlite3.connect`` and ``sqlite3.dbapi2.connect`` are the same builtin but
    # live on two module objects, and SQLAlchemy holds the *dbapi2* one. Patching
    # only ``sqlite3.connect`` leaves every engine built through DatabaseManager
    # invisible to the fence.
    import sqlite3.dbapi2

    sqlite3.connect = _fenced_connect  # type: ignore[method-assign]
    sqlite3.dbapi2.connect = _fenced_connect  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def isolate_db_globals() -> Iterator[None]:
    """Restore the shared config paths and drop the manager singleton per test."""
    saved = {name: getattr(config.paths, name) for name in _PATH_FIELDS}
    saved_words = zvs_mod._bible_words_cache
    database_mod.reset_manager()

    yield

    for name, value in saved.items():
        setattr(config.paths, name, value)
    # The ZVS validator caches the Bible word set in a module global, so a test
    # that ran against a scratch database would keep answering with the scratch
    # (usually empty) set for every later test.
    zvs_mod._bible_words_cache = saved_words
    database_mod.reset_manager()


@pytest.fixture
def scratch_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the whole process at an empty database under ``tmp_path``.

    Anything that writes through ``get_manager()`` *and* anything that reads
    ``config.paths.db`` directly (the eval store, ``default_db_url()``) resolve
    the same file, so a run cannot half-write to the corpus. Used by tests that
    exercise real CLI entry points, which persist history as a side effect.
    """
    db = tmp_path / "zolai.db"
    monkeypatch.setattr(config.paths, "db", db)
    database_mod.reset_manager()
    return db


@pytest.fixture(autouse=True)
def no_canonical_db_writes() -> Iterator[None]:
    """Fail any test that writes a row into the corpus database."""
    if _FENCE_LIFTED or not CANONICAL_DB.exists():
        yield
        return

    _CANONICAL_WRITES.clear()
    yield
    if _CANONICAL_WRITES:
        statement, stack = _CANONICAL_WRITES[0]
        pytest.fail(
            f"test issued {len(_CANONICAL_WRITES)} write(s) against the canonical "
            f"corpus {CANONICAL_DB}; first: {statement!r}\n{stack}\n"
            "Point the test at a throwaway DB — patch `config.paths.db` or build "
            "`DatabaseManager` on a tmp_path file — or run with "
            "ZOLAI_ALLOW_CANONICAL_WRITES=1 if the write is real."
        )
