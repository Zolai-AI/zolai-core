"""One UTC serialisation per timestamp column, and the clock that produces it.

Why this file exists (measured 2026-10-27 against ``data/zolai.db``, 201
name-matching timestamp columns across 121 tables): 11 columns stored two or
three serialisations of the same instant at once —
``data_audit_log.changed_at`` held 40,067 ``+00:00`` values and 5,977
space-separated ones, ``knowledge_claims.created_at`` 48 and 124,
``foundation_evidence.created_at`` 1,862 and 380 naive stamps written *that day*.
TEXT timestamps only ever order as text, and ``" "`` is ``0x20`` while ``"T"``
is ``0x54``, so ``ORDER BY changed_at DESC`` ranked a 01:00 ``T`` row above a
23:59 space row of the same day, and ``WHERE changed_at >= '2026-10-03T10:00'``
dropped every space-format row of that day without an error.

The writers were SQLite's own ``datetime('now')`` (which answers
``YYYY-MM-DD HH:MM:SS``) in INSERTs, UPDATEs, and — invisibly to any code review
— in column ``DEFAULT`` clauses, plus 16 local stamp helpers each choosing a
format (18 counting the two left in the exempt auth lane), 47 deprecated
``datetime.utcnow()`` calls, and 10 ``default_factory=datetime.utcnow`` dataclass
fields.

``test_normalization_sorts_what_the_raw_column_cannot`` is the failure this lane
exists to prevent, asserted as data rather than described in prose.
"""

from __future__ import annotations

import ast
import importlib.util
import io
import re
import sqlite3
import tokenize
from datetime import datetime, timezone
from pathlib import Path

import pytest

from zolai.shared.clock import SQL_NOW, now, parse, stamp

TOOLKIT_ROOT = Path(__file__).resolve().parents[1]
CANONICAL = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")


def _load_normalizer():
    path = TOOLKIT_ROOT / "scripts" / "normalize_timestamps.py"
    spec = importlib.util.spec_from_file_location("normalize_timestamps", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestClock:
    def test_stamp_is_the_canonical_shape(self):
        assert CANONICAL.match(stamp())

    def test_naive_input_is_read_as_utc_not_local(self):
        # A stamp that silently means "wherever this process ran" is the bug.
        assert stamp(datetime(2026, 10, 3, 23, 0, 0)) == "2026-10-03T23:00:00+00:00"

    def test_now_is_aware(self):
        assert now().tzinfo is not None

    @pytest.mark.parametrize(
        ("legacy", "microsecond"),
        [
            ("2026-10-03 10:42:39", 0),  # datetime('now') — the schema default
            ("2026-10-03T10:42:39", 0),  # datetime.utcnow().isoformat()
            ("2026-10-03T10:42:39.466973", 466973),
            ("2026-10-03T10:42:39+00:00", 0),
            ("2026-10-03T10:42:39Z", 0),  # the bloom-manifest shape
        ],
    )
    def test_every_serialisation_in_the_corpus_parses_to_the_same_instant(self, legacy, microsecond):
        assert parse(legacy) == datetime(2026, 10, 3, 10, 42, 39, microsecond, tzinfo=timezone.utc)

    def test_parse_orders_across_formats_where_text_cannot(self):
        space, tight = "2026-10-03 23:59:00", "2026-10-03T00:01:00"
        assert sorted([space, tight])[0] == space  # as text: the later time first
        assert sorted([space, tight], key=parse)[0] == tight  # as instants: fixed


@pytest.fixture()
def mixed_db(tmp_path: Path) -> Path:
    """A scratch DB holding every legacy serialisation in a stamp column."""
    db = tmp_path / "stamps.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY, created_at TEXT, changed_at TEXT, label TEXT)"
    )
    conn.execute(
        "INSERT INTO events (created_at, changed_at, label) VALUES "
        "('2026-10-03 23:59:00', '2026-10-03T10:42:39.466973', 'space'),"
        "('2026-10-03T00:01:00',  '2026-10-03 09:00:00',        'naive-T-and-space'),"
        "('2026-10-03T11:00:00Z', '2026-10-03T11:00:00+00:00',  'zulu'),"
        "('2026-10-03',           '2026-10-03',                 'date-only-must-be-left-alone'),"
        "(NULL, NULL, 'null-stays-null')"
    )
    conn.commit()
    conn.close()
    return db


class TestNormalize:
    def test_finds_the_mixed_columns_only(self, mixed_db: Path):
        normalizer = _load_normalizer()
        conn = sqlite3.connect(f"file:{mixed_db}?mode=ro", uri=True)
        try:
            report = normalizer.scan(conn)
            found = [(t, c) for t, c, _ in normalizer.mixed(report)]
        finally:
            conn.close()
        assert ("events", "created_at") in found
        assert ("events", "changed_at") in found

    def test_rewrites_legacy_shapes_and_leaves_dates_and_nulls_alone(self, mixed_db: Path, tmp_path: Path):
        normalizer = _load_normalizer()
        ledger = tmp_path / "ledger.jsonl"
        assert normalizer.apply_migration(mixed_db, apply=True, sidecar=ledger) == 0

        conn = sqlite3.connect(mixed_db)
        rows = conn.execute("SELECT created_at, changed_at, label FROM events ORDER BY id").fetchall()
        conn.close()
        for created, changed, label in rows:
            if label == "date-only-must-be-left-alone":
                assert created == "2026-10-03"  # inventing midnight would state a thing the row never said
                continue
            if label == "null-stays-null":
                assert (created, changed) == (None, None)
                continue
            assert CANONICAL.match(created), (label, created)
            assert CANONICAL.match(changed), (label, changed)

    def test_the_instant_never_moves(self, mixed_db: Path, tmp_path: Path):
        normalizer = _load_normalizer()
        before = sqlite3.connect(mixed_db).execute("SELECT id, created_at FROM events ORDER BY id").fetchall()
        normalizer.apply_migration(mixed_db, apply=True, sidecar=tmp_path / "ledger.jsonl")
        after = sqlite3.connect(mixed_db).execute("SELECT id, created_at FROM events ORDER BY id").fetchall()

        def instants(rows):
            return [(row_id, None if value is None else parse(value)) for row_id, value in rows]

        assert instants(before) == instants(after)

    def test_idempotent(self, mixed_db: Path, tmp_path: Path):
        normalizer = _load_normalizer()
        ledger = tmp_path / "ledger.jsonl"
        normalizer.apply_migration(mixed_db, apply=True, sidecar=ledger)
        second = normalizer.apply_migration(mixed_db, apply=True, sidecar=ledger)
        assert second == 0
        conn = sqlite3.connect(f"file:{mixed_db}?mode=ro", uri=True)
        try:
            assert normalizer.mixed(normalizer.scan(conn)) == []
        finally:
            conn.close()

    def test_revert_restores_the_original_rows(self, mixed_db: Path, tmp_path: Path):
        normalizer = _load_normalizer()
        q = "SELECT id, created_at, changed_at FROM events ORDER BY id"
        before = sqlite3.connect(mixed_db).execute(q).fetchall()
        ledger = tmp_path / "ledger.jsonl"
        normalizer.apply_migration(mixed_db, apply=True, sidecar=ledger)
        assert normalizer.revert(ledger, mixed_db) == 0
        after = sqlite3.connect(mixed_db).execute(q).fetchall()
        assert before == after

    def test_normalization_sorts_what_the_raw_column_cannot(self, mixed_db: Path, tmp_path: Path):
        """The whole point, asserted as data: a same-day window query.

        ``created_at >= '2026-10-03T00:30:00'`` should return the 23:59 row and
        the 11:00 row.  Against the raw column it returns only the 11:00 one:
        ``'2026-10-03 23:59:00'`` starts with a space, ``0x20``, which is less
        than the ``T`` of the bound at the same byte, so a row nine hours after
        the bound is dropped — no error, no warning.  After normalization both
        come back, and ``ORDER BY`` runs in real time order.
        """
        conn = sqlite3.connect(mixed_db)
        raw = {
            r[0]
            for r in conn.execute(
                "SELECT label FROM events WHERE created_at >= '2026-10-03T00:30:00' AND label IS NOT NULL"
            )
        }
        conn.close()

        normalizer = _load_normalizer()
        normalizer.apply_migration(mixed_db, apply=True, sidecar=tmp_path / "ledger.jsonl")

        conn = sqlite3.connect(mixed_db)
        fixed = {
            r[0]
            for r in conn.execute(
                "SELECT label FROM events WHERE created_at >= '2026-10-03T00:30:00+00:00' AND label IS NOT NULL"
            )
        }
        order = [
            r[0]
            for r in conn.execute(
                "SELECT label FROM events WHERE created_at LIKE '%T%' ORDER BY created_at"
            )
        ]
        conn.close()

        assert raw == {"zulu"}  # the 23:59 row is missing, and nothing said so
        assert fixed == {"space", "zulu"}
        assert order == ["naive-T-and-space", "zulu", "space"]  # 00:01, 11:00, 23:59


class TestPackageWritesOneFormat:
    """The rule stays machine-enforced, the way the single-resolver rule does.

    Re-picking a format in a new file is invisible in review: it works until a
    query joins two writers, and then it returns the wrong rows quietly.
    """

    ALLOWED_SQL_DEFAULT = frozenset()

    #: The auth lane historically stamped live session and API-key expiry in the
    #: space format and compared stored values as text, so flipping the writer
    #: without also flipping the stored rows made every existing session read as
    #: expired.  That lane is closed as of the 2026-10-29 migration: readers use
    #: ``clock.parse()`` for aware-datetime compare, writers use ``clock.stamp()``,
    #: and the ``normalize_timestamps.py --apply`` pass rewrites every legacy row.
    #: Kept as a named (empty) set so re-addings are visible in the diff.
    AUTH_LANE = frozenset()

    def _sources(self, *, skip: frozenset[str] = frozenset()):
        for path in sorted((TOOLKIT_ROOT / "zolai").rglob("*.py")):
            rel = str(path.relative_to(TOOLKIT_ROOT))
            if "__pycache__" in rel or rel in skip:
                continue
            yield path, rel

    @staticmethod
    def _code_lines(text: str) -> list[str]:
        """The file with comments and docstrings blanked out, line for line.

        Scanning raw text would force a "skip comment lines" rule, and that rule
        would also hide a genuine offender sitting inside a string literal —
        ``sa_text("datetime('now')")`` is exactly the thing being hunted here.
        So prose is masked structurally and string literals stay visible.
        """
        masked = text.splitlines()
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return masked
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc is None or not getattr(node, "body", None):
                    continue
                first = node.body[0]
                if not (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                        and first.value.value == doc):
                    continue
                for ln in range(first.lineno, (first.end_lineno or first.lineno) + 1):
                    masked[ln - 1] = ""
        try:
            toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
        except (tokenize.TokenError, IndentationError):
            toks = []
        for tok in toks:
            if tok.type == tokenize.COMMENT:
                s, e = tok.start, tok.end
                if s[0] == e[0]:
                    line = masked[s[0] - 1]
                    masked[s[0] - 1] = line[: s[1]] + " " * (len(line) - s[1])
        return masked

    def test_no_sql_side_now_expression(self):
        # datetime('now') is SQLite for "YYYY-MM-DD HH:MM:SS" — the space format.
        offenders = [
            f"{rel}:{i}"
            for path, rel in self._sources(skip={"zolai/shared/clock.py"})
            for i, line in enumerate(self._code_lines(path.read_text(encoding="utf-8")), 1)
            if re.search(r"datetime\(\s*'now'\s*\)|datetime\(\s*\"now\"\s*\)|CURRENT_TIMESTAMP", line, re.I)
        ]
        assert not offenders, "route these through zolai.shared.clock: " + ", ".join(offenders)

    def test_no_deprecated_utcnow(self):
        offenders = [
            f"{rel}:{i}"
            for path, rel in self._sources(skip={"zolai/shared/clock.py"})
            for i, line in enumerate(self._code_lines(path.read_text(encoding="utf-8")), 1)
            if re.search(r"\butcnow\b", line)
        ]
        assert not offenders, "datetime.utcnow() is naive and deprecated: " + ", ".join(offenders)

    def test_stamp_helpers_do_not_each_choose_a_format(self):
        """One local ``_now()`` per module is how the split started."""
        offenders = [
            f"{rel}:{i}"
            for path, rel in self._sources(skip=self.AUTH_LANE)
            for i, line in enumerate(self._code_lines(path.read_text(encoding="utf-8")), 1)
            if re.search(r"def _(now|utc_now|stamp|timestamp)\(", line)
        ]
        assert not offenders, "import zolai.shared.clock instead: " + ", ".join(offenders)

    def test_no_inline_space_format_strftime(self):
        """A one-line ``strftime("%Y-%m-%d %H:%M:%S")`` is a local ``_stamp()``
        that never got a name — invisible to the helper scan.

        This class was what ``data_audit_log.changed_at`` accumulated to 5,977
        space-format rows: an inline ``datetime.now(timezone.utc).strftime(...)``
        beside canonical ``stamp()`` calls writing the same column. The auth
        lane is exempt for the same reason as the helpers — the writer and the
        stored expiry rows must flip together, not one side at a time.
        """
        offenders = []
        for path, rel in self._sources(skip=self.AUTH_LANE):
            for i, line in enumerate(self._code_lines(path.read_text(encoding="utf-8")), 1):
                if re.search(r"""strftime\(\s*['"]%Y-%m-%d %H:%M:%S['"]""", line):
                    offenders.append(f"{rel}:{i}: {line.strip()}")
        assert not offenders, "use zolai.shared.clock.stamp() instead: " + "; ".join(offenders)

    def test_inlined_sql_defaults_match_the_clock_constant(self):
        """DDL literals are copied, not interpolated, so they can drift.

        ``CREATE TABLE`` text can't be an f-string — the DDL carries JSON column
        defaults like ``DEFAULT '{}'`` whose braces would be eaten. So the
        canonical expression appears as a literal in dozens of places, and
        nothing but this check keeps those copies equal to the one definition.
        """
        expr = SQL_NOW
        drift = []
        for path, rel in self._sources():
            text = path.read_text(encoding="utf-8")
            for i, line in enumerate(self._code_lines(text), 1):
                if "strftime(" in line and "'now'" in line and expr not in line:
                    drift.append(f"{rel}:{i}: {line.strip()}")
        assert not drift, "DDL default differs from zolai.shared.clock.SQL_NOW: " + "; ".join(drift)


CANONICAL_DB = TOOLKIT_ROOT.parent / "data" / "zolai.db"


class TestFreshSchemaDefaultsAreCanonical:
    """The code gate can't see the schema, and the schema is the second half of the bug.

    ``CREATE TABLE IF NOT EXISTS`` never re-runs against an existing database, so
    editing the DDL text only fixes *new* databases. Whatever the values look
    like after a normalization pass, an INSERT that omits a timestamp still
    inherits whatever ``DEFAULT`` the stored schema carries. These two tests
    split that into "the DDL we hand out from now on" and "the schema already on
    disk", because they need different fixes and only the first is free.
    """

    def _ddl_constants(self):
        from zolai.data import migrations

        return {
            name: value
            for name, value in vars(migrations).items()
            if name.endswith("_DDL") and isinstance(value, str)
        }

    def test_shipped_ddl_creates_no_space_format_defaults(self):
        """Every ``now``-shaped column default in the shipped DDL must evaluate canonical.

        The default is read off ``PRAGMA table_info`` and then *executed*, rather
        than grep-matched out of the DDL text, because the text is exactly what
        SQLite rewrites: ``DEFAULT (datetime('now'))`` and
        ``DEFAULT (strftime(...,'now'))`` are different strings that both have to
        be judged on what they store. ``INSERT ... DEFAULT VALUES`` is not
        available here — most of these tables have NOT NULL columns with no
        default at all, so a blank row is rejected for unrelated reasons.
        """
        conn = sqlite3.connect(":memory:")
        wrong: list[str] = []
        try:
            for name, ddl in sorted(self._ddl_constants().items()):
                try:
                    conn.execute(ddl)
                except sqlite3.Error as exc:
                    pytest.fail(f"{name} did not execute cleanly: {exc}")
                table = re.search(
                    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`']?(\w+)", ddl, re.I
                )
                if not table:
                    continue
                for _cid, column, _type, _nn, dflt, _pk in conn.execute(
                    f'PRAGMA table_info("{table.group(1)}")'
                ):
                    if not dflt or "now" not in str(dflt).lower():
                        continue
                    stored = conn.execute(f"SELECT {dflt}").fetchone()[0]
                    if not isinstance(stored, str) or not CANONICAL.match(stored):
                        wrong.append(f"{table.group(1)}.{dflt!r} -> {stored!r}")
        finally:
            conn.close()
        assert not wrong, "column default does not store the canonical stamp: " + "; ".join(wrong)

    @pytest.mark.parametrize(
        ("module_name", "helper"),
        [
            ("zolai.data.migrations", "_now"),
            ("zolai.data.migrations", "_utc_now"),
            ("zolai.data.migrations", "_stamp"),
            ("zolai.data.schema_v2", "_now"),
            ("zolai.data.schema_v2", "_utc_now"),
            ("zolai.data.schema_v2", "_stamp"),
        ],
    )
    def test_stamp_helpers_are_gone_from_the_ddl_sources(self, module_name, helper):
        """One local ``_now()`` per module is how the split started.

        ``_sources`` already asserts this across the package; it is repeated here
        against the live module objects because the two DDL constants in this test
        are only meaningful if those modules stamp the way the clock does.
        """
        mod = importlib.import_module(module_name)
        assert not hasattr(mod, helper)

    def test_live_schema_has_no_space_format_defaults(self):
        if not CANONICAL_DB.exists():
            pytest.skip(f"no canonical database at {CANONICAL_DB}")
        conn = sqlite3.connect(f"file:{CANONICAL_DB}?mode=ro", uri=True)
        try:
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            stale = []
            for table in tables:
                for _cid, column, _type, _nn, dflt, _pk in conn.execute(f'PRAGMA table_info("{table}")'):
                    if dflt and re.search(r"datetime\(\s*'now'\s*\)", str(dflt), re.I):
                        stale.append(f"{table}.{column}")
        finally:
            conn.close()
        assert not stale, f"{len(stale)} live column(s) still default to the space format: {stale[:8]}"
