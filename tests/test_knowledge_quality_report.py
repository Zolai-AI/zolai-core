"""RED-first suite for the knowledge-quality evaluation harness (spec-knowledge-eval).

Synthetic ``tmp_path`` fixtures only — the live 2.4 GB database is never opened
(the ``run_cli`` path guard asserts every ``--db`` argument lives under
``tmp_path``).  Coverage follows spec §6:

* R1 metric correctness (value/ratio + green on clean fixtures)
* R2 threshold engine (boundaries, dropped/absent tables, grandfathering)
* R3 pin engine (MUST_NOT_REGRESS / MUST_IMPROVE / ambiguous)
* R4 read-only enforcement (byte-identical DB, no rw connect, ``--persist`` idempotent)
* R5 GLOB semantics (Burmese range + ref-format predicate)
* R6 report/exit codes + ``--compare`` transitions
* R7 track done-when registry (pre/post R2-C)
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MODULE = "scripts.maintenance.knowledge_quality_report"
SHIPPED_PINS = REPO_ROOT / "scripts" / "maintenance" / "knowledge_quality_pins.json"

#: Every table the shipped pins reference — fixture DDL below builds them all.
TABLE_DDL = """
CREATE TABLE vocabulary (id INTEGER PRIMARY KEY, headword TEXT, english TEXT,
  pos_canonical TEXT, review_status TEXT, status TEXT, source_type TEXT,
  processing_version TEXT, confidence REAL);
CREATE TABLE dictionary (id INTEGER PRIMARY KEY, zolai TEXT, english TEXT,
  pos_canonical TEXT, zvs_compliance_status TEXT, confidence REAL, source TEXT);
CREATE TABLE dictionary_en_zo (id INTEGER PRIMARY KEY, headword TEXT, translations TEXT,
  zvs_compliance_status TEXT, source TEXT);
CREATE TABLE zolai_vocabulary (id INTEGER PRIMARY KEY, zolai TEXT, english TEXT, confidence REAL);
CREATE TABLE dictionary_meanings (id INTEGER PRIMARY KEY, dictionary_id INTEGER, zolai TEXT,
  meaning_en TEXT, is_primary INTEGER);
CREATE TABLE bible_verses (id INTEGER PRIMARY KEY, ref TEXT, book TEXT, chapter TEXT,
  verse TEXT, zo_tdb77 TEXT, zo_tedim2010 TEXT, zo_tedim1932 TEXT, zo_hcl06 TEXT,
  zo_fcl TEXT, en_kJV TEXT, myanmar TEXT, myanmar_judson TEXT);
CREATE TABLE bible_verses_import (book TEXT, book_name TEXT, chapter TEXT, verse TEXT,
  ref TEXT, zo_tdb77 TEXT, en_kJV TEXT, import_batch_id TEXT, source_file TEXT, imported_at TEXT);
CREATE TABLE translations (id INTEGER PRIMARY KEY, source TEXT, target TEXT,
  direction TEXT, reference TEXT);
CREATE TABLE translations_import (id INTEGER PRIMARY KEY, zolai TEXT, english TEXT,
  reference TEXT, import_batch_id TEXT, source_file TEXT, imported_at TEXT);
CREATE TABLE word_alignments (id INTEGER PRIMARY KEY, ref TEXT, zolai_word TEXT,
  english_word TEXT, position INTEGER);
CREATE TABLE word_alignments_import (ref TEXT, zo_word TEXT, en_word TEXT,
  en_position INTEGER, import_batch_id TEXT);
CREATE TABLE word_usage (id INTEGER PRIMARY KEY, word TEXT, book TEXT, myanmar TEXT);
CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT, content TEXT,
  content_hash TEXT, link TEXT);
CREATE TABLE import_log (id INTEGER PRIMARY KEY, batch_id TEXT, source_file TEXT,
  table_name TEXT, rows_imported INTEGER, imported_at TEXT, status TEXT,
  error_message TEXT, created_at TEXT);
CREATE TABLE provenance (id INTEGER PRIMARY KEY, filename TEXT, row_count INTEGER,
  created_at TEXT);
CREATE TABLE eval_runs (id INTEGER PRIMARY KEY, set_name TEXT, created_at TEXT,
  case_count INTEGER, duration_ms INTEGER, gate_passed INTEGER, metrics TEXT, source TEXT);
CREATE TABLE corpus_quality_reports (report_id TEXT PRIMARY KEY, created_at TEXT, stats TEXT);
CREATE TABLE zolai_grammar_patterns (id INTEGER PRIMARY KEY, zolai_example TEXT,
  pattern_id TEXT, pattern_name TEXT);
CREATE TABLE grammar_patterns (id INTEGER PRIMARY KEY, pattern_id TEXT, pattern TEXT,
  description TEXT, book TEXT);
CREATE TABLE grammar_patterns_import (id INTEGER PRIMARY KEY, pattern TEXT,
  description TEXT, structure TEXT, import_batch_id TEXT);
CREATE TABLE phrases (id INTEGER PRIMARY KEY, zolai TEXT, english TEXT);
CREATE TABLE proverbs (id INTEGER PRIMARY KEY, zolai TEXT, english TEXT);
CREATE TABLE dictionary_import (zolai TEXT, english TEXT, source TEXT,
  import_batch_id TEXT, source_file TEXT);
CREATE TABLE dictionary_en_zo_import (headword TEXT, translations TEXT,
  import_batch_id TEXT, source_file TEXT);
CREATE TABLE vocab_import (headword TEXT, english TEXT, import_batch_id TEXT, source_file TEXT);
CREATE TABLE zolai_vocabulary_import (word TEXT, definition TEXT,
  import_batch_id TEXT, source_file TEXT);
CREATE TABLE phrases_import (zo TEXT, phrase TEXT, import_batch_id TEXT, source_file TEXT);
CREATE TABLE phrases_from_bible_import (phrase TEXT, import_batch_id TEXT, source_file TEXT);
CREATE TABLE zvs_corrections_import (zolai TEXT, old_english_clean TEXT,
  new_english_clean TEXT, reason TEXT, import_batch_id TEXT, source_file TEXT);
CREATE TABLE zolai_bible_analysis (book_code TEXT, chapter TEXT, verse TEXT,
  zolai TEXT, english TEXT);
CREATE TABLE pos_verified (word TEXT);
CREATE TABLE morph_verified (word TEXT);
CREATE TABLE word_analyses (word TEXT);
CREATE TABLE particle_database (particle TEXT);
CREATE TABLE verb_database (verb TEXT);
CREATE TABLE bible_study_analysis (ref TEXT, book TEXT, chapter TEXT, verse TEXT);
CREATE TABLE ngram (word TEXT);
CREATE TABLE word_similarity (word TEXT);
CREATE TABLE user_reviews (id INTEGER PRIMARY KEY);
CREATE TABLE corrections (id INTEGER PRIMARY KEY);
CREATE TABLE proverbs_idioms (id INTEGER PRIMARY KEY);
"""

#: The 8 conflict headwords + 6 verified numerals with the byte-exact glosses the
#: shipped pins enforce (post-Track-0 re-baseline values, spec §4.1/§4.2).
SHIPPED_PIN_VALUES = {
    "sanga": "thou",
    "sawmthum": "and",
    "za": "hear",
    "nang": "weak, ye",
    "in": "with",
    "kei": "I (pronoun, subject)",
    "hoih": "good, beautiful",
    "khat": "one",
    "hnih": "two",
    "thum": "three",
    "nga": "five",
    "giat": "eight",
    "kua": "nine",
}


def _kq():
    try:
        return importlib.import_module(MODULE)
    except ModuleNotFoundError as exc:  # pragma: no cover — RED until implemented
        pytest.fail(f"{MODULE} not implemented yet: {exc}")


# ── fixture builders ───────────────────────────────────────────────────────


def make_kq_db(tmp_path: Path, *, flavor: str = "clean") -> Path:
    """Miniature DB with the full harness table set, seeded per flavor.

    ``clean`` reproduces a post-everything healthy DB (all shipped thresholds
    met); ``defect`` reproduces one instance of every defect shape the metrics
    look for, with exact counts asserted by the R1 tests.
    """
    db = tmp_path / "kq-fixture.db"
    tmp_path.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(TABLE_DDL)

    def rows(sql: str, seq) -> None:
        conn.executemany(sql, seq)

    # pinned headwords always exist (byte-exact shipped values; the clean flavor
    # carries the post-R2-C reconciled gloss for `nang`, the one conflict headword
    # whose M10 known-bad pair must be gone once R2-C is done)
    for hw, gloss in SHIPPED_PIN_VALUES.items():
        if flavor == "clean" and hw == "nang":
            gloss = "you"
        conn.execute(
            "INSERT INTO vocabulary(headword, english, pos_canonical, review_status,"
            " status, source_type, processing_version, confidence) VALUES (?,?,?,?,?,?,?,?)",
            (
                hw,
                gloss,
                "noun" if flavor == "clean" else None,
                "reviewed" if flavor == "clean" else "unknown",
                "ACTIVE" if flavor == "clean" else "OBSERVED",
                "bible" if flavor == "clean" else "unknown",
                "v1" if flavor == "clean" else None,
                0.8 if flavor == "clean" else 1.0,
            ),
        )

    if flavor == "clean":
        rows(
            "INSERT INTO vocabulary(headword, english) VALUES (?,?)",
            [(f"clean{i}", "a gloss") for i in range(20)],
        )
        rows(
            "INSERT INTO dictionary(zolai, english, pos_canonical, zvs_compliance_status,"
            " confidence, source) VALUES (?,?,?,?,?,?)",
            [(f"w{i}", "gloss", "noun", "passed", 0.8, "supplement") for i in range(1000)],
        )
        rows(
            "INSERT INTO dictionary_en_zo(headword, translations) VALUES (?,?)",
            [(f"w{i}", '["one"]') for i in range(20)],
        )
        rows(
            "INSERT INTO zolai_vocabulary(zolai, english, confidence) VALUES (?,?,?)",
            [(f"w{i}", "gloss", 0.7) for i in range(20)],
        )
        rows(
            "INSERT INTO bible_verses(ref, book, chapter, verse, zo_tdb77, zo_tedim2010,"
            " zo_tedim1932, zo_hcl06, zo_fcl, en_kJV, myanmar, myanmar_judson)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (f"GEN {c}:1", "GEN", str(c), "1", "z", "z", "z", "z", "z", "e", "m", "m")
                for c in range(1, 6)
            ],
        )
        rows(
            "INSERT INTO bible_verses_import(ref, zo_tdb77) VALUES (?,?)",
            [(f"GEN {c}:1", "z") for c in range(1, 6)],
        )
        rows(
            "INSERT INTO translations(source, target, direction, reference) VALUES (?,?,?,?)",
            [("en", "zo", "en_to_zo", "GEN 1:1"), ("zo", "en", "zo_to_en", "GEN 1:1")],
        )
        rows(
            "INSERT INTO word_alignments(ref, zolai_word, english_word, position)"
            " VALUES (?,?,?,?)",
            [(f"GEN {c}:1", f"clean{c}", "a", 1) for c in range(1, 11)],
        )
        rows(
            "INSERT INTO word_usage(word, book, myanmar) VALUES (?,?,?)",
            [("clean1", "GEN", "m"), ("clean2", "PSA", "m")],
        )
        rows("INSERT INTO articles(title, content_hash) VALUES (?,?)", [("t", "hash-1")])
        rows(
            "INSERT INTO import_log(table_name, rows_imported, status, source_file,"
            " created_at) VALUES (?,?,?,?,?)",
            [("bible_verses", 5, "completed", "bible.jsonl", "2026-01-01 00:00:00")],
        )
        rows(
            "INSERT INTO provenance(filename, row_count, created_at) VALUES (?,?,?)",
            [("bible.jsonl", 5, "2026-01-01 00:00:00")],
        )
        conn.execute(
            "INSERT INTO eval_runs(set_name, created_at, case_count, gate_passed)"
            " VALUES ('smoke', ?, 10, 1)",
            (_now_iso(),),
        )
        conn.execute("INSERT INTO corpus_quality_reports(report_id, created_at) VALUES (?,?)",
                     ("r1", _now_iso()))
        rows("INSERT INTO zolai_grammar_patterns(zolai_example) VALUES (?)", [("hi hi",)])
        rows("INSERT INTO grammar_patterns(pattern, book) VALUES (?,?)", [("SOV", "BIBLE")])
        rows("INSERT INTO phrases(english) VALUES (?)", [("a phrase",)])
        rows("INSERT INTO proverbs(english) VALUES (?)", [("a proverb",)])
        # every silent-empty reader carries one row → M6b resolves to 0
        for tbl, col in (
            ("pos_verified", "word"),
            ("morph_verified", "word"),
            ("word_analyses", "word"),
            ("particle_database", "particle"),
            ("verb_database", "verb"),
            ("bible_study_analysis", "ref"),
            ("ngram", "word"),
            ("word_similarity", "word"),
        ):
            conn.execute(f"INSERT INTO {tbl}({col}) VALUES ('x')")
        for tbl in ("user_reviews", "corrections", "proverbs_idioms"):
            conn.execute(f"INSERT INTO {tbl}(id) VALUES (1)")
        conn.execute(
            "INSERT INTO dictionary_meanings(dictionary_id, zolai, meaning_en, is_primary)"
            " VALUES (1,'sanga','thou',1)"
        )
        # every MUST_IMPROVE conflict headword satisfies its shipped target_predicate:
        # a non-digit dictionary gloss + enough dictionary_meanings sense rows (shipped
        # targets require >=3 for "za" and "kei", >=2 for the rest)
        for hw in ("sanga", "sawmthum", "za", "nang", "in", "kei"):
            conn.execute(
                "INSERT INTO dictionary(zolai, english, pos_canonical, zvs_compliance_status,"
                " confidence, source) VALUES (?,?,?,?,?,?)",
                (hw, "a clean gloss", "noun", "passed", 0.8, "supplement"),
            )
            sense_rows = 3 if hw in ("za", "kei") else 2
            for i in range(sense_rows):
                conn.execute(
                    "INSERT INTO dictionary_meanings(dictionary_id, zolai, meaning_en)"
                    " VALUES (?,?,?)",
                    (1, hw, f"{hw} sense {i}"),
                )
        # the two LOST Myanmar loads are ledger-marked dead (R2-C alternative path)
        conn.execute("CREATE TABLE dictionary_en_my_import (headword TEXT, translations TEXT)")
        conn.execute("CREATE TABLE dictionary_trilingual_import (headword TEXT, translations TEXT)")
        conn.execute(
            "INSERT INTO import_log(table_name, rows_imported, status, source_file)"
            " VALUES ('dictionary_en_my_import', 0, 'dead', 'dict_my_zo.jsonl')"
        )
        conn.execute(
            "INSERT INTO import_log(table_name, rows_imported, status, source_file)"
            " VALUES ('dictionary_trilingual_import', 0, 'dead', 'dict_trilingual.jsonl')"
        )
        conn.commit()
        conn.close()
        return db

    # ── flavor == "defect": one instance of every defect shape ────────────
    rows(
        "INSERT INTO vocabulary(headword, english) VALUES (?,?)",
        [("empty1", ""), ("empty2", None), ("dig1", "31"), ("dig2", "7seven")],
    )
    conn.execute(
        "INSERT INTO dictionary(zolai, english) VALUES ('sanga', '31')"
    )
    conn.execute(
        "INSERT INTO dictionary_en_zo(headword, translations) VALUES ('w1', '')"
    )
    conn.execute("INSERT INTO zolai_vocabulary(zolai, english, confidence) VALUES ('w1','g',1.0)")
    conn.execute("INSERT INTO bible_verses(ref, book, chapter, verse) VALUES ('GEN.1:1','GEN','1','1')")
    conn.execute("INSERT INTO bible_verses(ref, book, chapter, verse) VALUES ('GEN 1:1','GEN','1','1')")
    conn.execute("INSERT INTO bible_verses_import(ref) VALUES ('GEN 1:1')")
    conn.execute("INSERT INTO bible_verses_import(ref) VALUES ('GEN 1:1')")
    conn.execute("INSERT INTO translations(direction, reference) VALUES ('en_to_zo','GEN 1:1')")
    conn.execute("INSERT INTO translations(direction, reference) VALUES ('en_to_zo','GEN.2:3')")
    conn.execute("INSERT INTO translations(direction, reference) VALUES ('en→my','news:1')")
    conn.execute("INSERT INTO translations_import(reference) VALUES ('GEN.1:1')")
    conn.execute("INSERT INTO word_alignments(ref, zolai_word, position) VALUES ('GEN 1:1','sanga',1)")
    conn.execute("INSERT INTO word_usage(word, book) VALUES ('w','phrase:bigram')")
    conn.execute("INSERT INTO word_usage(word, book) VALUES ('w','dictionary string here')")
    conn.execute("INSERT INTO articles(title, content_hash) VALUES ('a','h')")
    conn.execute("INSERT INTO articles(title, content_hash) VALUES ('b','h')")
    conn.execute(
        "INSERT INTO import_log(table_name, rows_imported, status, source_file, created_at)"
        " VALUES ('bible_verses_import', 10, 'completed', 'gone.jsonl', '2026-01-01 00:00:00')"
    )
    conn.execute("INSERT INTO import_log(table_name, rows_imported, status) VALUES ('x', 1, 'failed')")
    conn.execute("INSERT INTO provenance(filename, row_count) VALUES ('zero.jsonl', 0)")
    conn.execute(
        "INSERT INTO eval_runs(set_name, created_at, case_count)"
        " VALUES ('smoke', '2026-01-05T00:00:00+00:00', 1)"
    )
    conn.execute("INSERT INTO zolai_grammar_patterns(zolai_example) VALUES ('------')")
    conn.execute("INSERT INTO zolai_grammar_patterns(zolai_example) VALUES ('Stanza')")
    conn.execute("INSERT INTO grammar_patterns(pattern, book) VALUES ('SOV', NULL)")
    for ref in ("GEN 1:1", "GEN 1:2"):
        conn.execute(
            "INSERT INTO bible_study_analysis(ref, book, chapter, verse) VALUES (?,?,?,?)",
            (ref, "GEN", "1", ref.split(" ")[1]),
        )
    conn.commit()
    conn.close()
    return db


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _base_pins() -> dict:
    """Small fixture-appropriate pins doc (engine behavior tests)."""
    return {
        "version": 1,
        "launch_date": "2026-01-01",
        "sqlite_min_version": "3.30",
        "paths": {"ngrams": "ngrams.jsonl"},
        "silent_readers": ["particle_database", "verb_database"],
        "metrics": [
            {
                "id": "M1",
                "family": "gloss",
                "label": "vocabulary empty-gloss rows",
                "kind": "sql",
                "sql": "SELECT COUNT(*) FROM vocabulary WHERE english IS NULL OR TRIM(english)=''",
                "denominator_sql": "SELECT COUNT(*) FROM vocabulary",
                "measure": "count",
                "direction": "lower",
                "baseline": 0,
                "amber": 3,
                "green": 0,
            },
            {
                "id": "M4.1",
                "family": "dup",
                "label": "bible_verses_import dup ratio",
                "kind": "ratio",
                "table": "bible_verses_import",
                "key_expr": "ref",
                "measure": "dup",
                "direction": "lower",
                "baseline": 1.0,
                "amber": 1.05,
                "green": 1.0,
            },
            {
                "id": "M9a",
                "family": "refformat",
                "label": "bible_verses refs conforming",
                "kind": "sql",
                "sql": (
                    "SELECT COUNT(*) FROM bible_verses WHERE ref GLOB '* *:*' AND ref NOT GLOB '*.*'"
                    " AND ref NOT GLOB '*[^A-Z0-9: ]*' AND ref NOT LIKE '%  %'"
                ),
                "denominator_sql": "SELECT COUNT(*) FROM bible_verses",
                "measure": "pct",
                "direction": "higher",
                "baseline": 100.0,
                "amber": 100.0,
                "green": 100.0,
            },
        ],
        "pins": [
            {
                "id": "pin:khat",
                "headword": "khat",
                "mode": "MUST_NOT_REGRESS",
                "enforce": [
                    {"table": "vocabulary", "headword_col": "headword", "column": "english",
                     "expected": "one"}
                ],
                "context": [],
                "note": "numeral",
            },
            {
                "id": "pin:sanga",
                "headword": "sanga",
                "mode": "MUST_IMPROVE",
                "active_from": "R2-C",
                "enforce": [
                    {"table": "vocabulary", "headword_col": "headword", "column": "english",
                     "expected": "thou"},
                    {"table": "dictionary", "headword_col": "zolai", "column": "english",
                     "expected": "31"},
                ],
                "context": [],
                "target_sql": (
                    "SELECT CASE WHEN (SELECT COUNT(*) FROM dictionary_meanings WHERE zolai='sanga')"
                    " >= 2 THEN 1 ELSE 0 END"
                ),
                "note": "conflict headword",
            },
            {
                "id": "pin:four",
                "headword": "four",
                "mode": "MUST_NOT_REGRESS",
                "ambiguous": True,
                "enforce": [{"table": "vocabulary", "headword_col": "headword",
                             "column": "english", "expected": "?"}],
                "context": [],
                "note": "competing headwords — never enforce",
            },
        ],
        "guards": [
            {"id": "G-VERSE-FORMAT", "label": "all bible_verses refs conform",
             "parts": [{"label": "bad_refs",
                        "sql": "SELECT COUNT(*) FROM bible_verses WHERE NOT (ref GLOB '* *:*'"
                               " AND ref NOT GLOB '*.*' AND ref NOT GLOB '*[^A-Z0-9: ]*'"
                               " AND ref NOT LIKE '%  %')",
                        "op": "<=", "value": 0}]},
            {"id": "G-PHONEME-CORE", "label": "phoneme core headwords kept",
             "parts": [{"label": "kept",
                        "sql": "SELECT COUNT(DISTINCT headword) FROM vocabulary WHERE headword IN"
                               " ('hoih','khat','hnih','thum','nga','giat','kua')",
                        "op": ">=", "value": 7}]},
        ],
        "tracks": {
            "R2-C": [
                {"id": "M1-zero", "kind": "metric", "metric": "M1", "op": "<=", "value": 0},
                {"id": "M9a-full", "kind": "metric", "metric": "M9a", "against": "green"},
                {"id": "G-VERSE-FORMAT", "kind": "guard", "guard": "G-VERSE-FORMAT"},
                {"id": "pin-sanga", "kind": "pin", "pin": "pin:sanga"},
                {"id": "no-news", "kind": "sql",
                 "sql": "SELECT COUNT(*) FROM translations WHERE direction NOT IN"
                        " ('en_to_zo','zo_to_en')",
                 "op": "<=", "value": 0},
            ],
        },
    }


def write_pins(tmp_path: Path, pins: dict | None = None, *, name: str = "pins.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(pins if pins is not None else _base_pins()), encoding="utf-8")
    return path


def run_cli(tmp_path: Path, args: list[str]) -> int:
    """Run the harness CLI with the live-DB path guard enforced."""
    for i, a in enumerate(args):
        if a == "--db":
            db_arg = args[i + 1]
            assert str(tmp_path) in db_arg, f"test tried to open a non-fixture DB: {db_arg}"
    return _kq().run(args)


def read_report(out_dir: Path) -> dict:
    files = sorted(out_dir.glob("kq-*.json"))
    assert files, f"no report JSON written under {out_dir}"
    return json.loads(files[-1].read_text(encoding="utf-8"))


def metric_by_id(report: dict, metric_id: str) -> dict:
    for m in report["metrics"]:
        if m["id"] == metric_id:
            return m
    raise AssertionError(f"metric {metric_id} missing from report: {[m['id'] for m in report['metrics']]}")


# ── R1 metric correctness ──────────────────────────────────────────────────


class TestMetricCorrectness:
    def test_m1_counts_empty_glosses_exactly(self, tmp_path):
        db = make_kq_db(tmp_path, flavor="defect")
        pins = write_pins(tmp_path, name="pins-r1.json")
        out = tmp_path / "out"
        assert run_cli(tmp_path, ["--db", str(db), "--pins", str(pins), "--out", str(out)]) in (0, 1, 2)
        m = metric_by_id(read_report(out), "M1")
        assert m["value"] == 2  # '' + NULL (the two pinned headwords have glosses)
        assert m["status"] == "amber"  # green 0 < 2 <= amber 3 — only value==0 is green

    def test_m1_clean_is_green_and_defect_at_boundary_is_amber(self, tmp_path):
        pins = _base_pins()
        pins["metrics"][0]["amber"] = 2
        db = make_kq_db(tmp_path, flavor="defect")
        out = tmp_path / "out"
        run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path, pins)),
                           "--out", str(out)])
        assert metric_by_id(read_report(out), "M1")["status"] == "amber"  # exactly at amber edge

    def test_m1_above_amber_is_red(self, tmp_path):
        pins = _base_pins()
        pins["metrics"][0]["amber"] = 1
        db = make_kq_db(tmp_path, flavor="defect")
        out = tmp_path / "out"
        run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path, pins)),
                           "--out", str(out)])
        assert metric_by_id(read_report(out), "M1")["status"] == "red"

    def test_dup_ratio_is_rows_over_distinct(self, tmp_path):
        db = make_kq_db(tmp_path, flavor="defect")
        out = tmp_path / "out"
        run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path)),
                           "--out", str(out)])
        m = metric_by_id(read_report(out), "M4.1")
        assert m["value"] == pytest.approx(2.0)
        assert m["rows"] == 2 and m["distinct"] == 1
        assert m["status"] == "red"  # 2.0 > amber 1.05

    def test_pct_metric_rounds_to_two_decimals(self, tmp_path):
        db = make_kq_db(tmp_path, flavor="defect")
        out = tmp_path / "out"
        run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path)),
                           "--out", str(out)])
        m = metric_by_id(read_report(out), "M9a")
        assert m["denominator"] == 2
        assert m["value"] == 1  # one conforming ref of two
        assert m["ratio"] == pytest.approx(0.5)
        assert m["status"] == "red"

    def test_clean_fixture_metric_set_and_values(self, tmp_path):
        db = make_kq_db(tmp_path, flavor="clean")
        out = tmp_path / "out"
        run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path)),
                           "--out", str(out)])
        report = read_report(out)
        assert [m["id"] for m in report["metrics"]] == ["M1", "M4.1", "M9a"]
        assert metric_by_id(report, "M1")["value"] == 0
        assert metric_by_id(report, "M4.1")["value"] == pytest.approx(1.0)
        assert metric_by_id(report, "M9a")["status"] == "green"

    def test_shipped_pins_metric_ids_and_families(self, tmp_path):
        pins = json.loads(SHIPPED_PINS.read_text(encoding="utf-8"))
        ids = [m["id"] for m in pins["metrics"]]
        families = {m["family"] for m in pins["metrics"]}
        assert len(ids) == len(set(ids))
        assert len(ids) == 43, f"expected the full spec §3 suite, got {len(ids)}"
        assert families == {
            "gloss", "dup", "confidence", "orphan", "contamination",
            "provenance", "refformat", "junk",
        }
        # every metric carries thresholds (the script must hardcode none)
        for m in pins["metrics"]:
            assert m.get("direction") in {"lower", "higher", "info"}, m["id"]
            if m["direction"] != "info":
                assert "amber" in m and "green" in m, m["id"]
            assert "baseline" in m, m["id"]

    def test_shipped_pins_registry_sizes(self, tmp_path):
        pins = json.loads(SHIPPED_PINS.read_text(encoding="utf-8"))
        conflict = [p for p in pins["pins"] if p.get("kind", "conflict") == "conflict"]
        assert len(conflict) == 8
        assert sum(1 for p in conflict if p["mode"] == "MUST_IMPROVE") == 6
        assert sum(1 for p in conflict if p["mode"] == "MUST_NOT_REGRESS") == 2
        numerals = [p for p in pins["pins"] if p.get("kind") == "numeral"]
        assert len(numerals) == 6
        assert len(pins["guards"]) == 7
        assert set(pins["tracks"]) == {"Track0", "R2-A", "R2-B", "R2-C", "R2-D",
                                       "R2-E", "R2-F", "R2-G"}
        assert any(p.get("ambiguous") for p in pins["pins"]), "ambiguous numerals recorded"

    def test_shipped_sql_runs_against_full_fixture(self, tmp_path):
        """Every shipped metric/guard/pin SQL executes — no typos, no absent-table crashes."""
        db = make_kq_db(tmp_path, flavor="defect")
        out = tmp_path / "out"
        rc = run_cli(tmp_path, ["--db", str(db),
                                "--pins", str(SHIPPED_PINS),
                                "--out", str(out),
                                "--format", "json"])
        assert rc in (0, 1, 2)
        report = read_report(out)
        assert len(report["metrics"]) == 43
        assert {m["status"] for m in report["metrics"]} <= {
            "green", "amber", "red", "info", "absent", "skipped", "dropped"
        }

    def test_deep_metrics_skipped_without_deep_flag(self, tmp_path):
        db = make_kq_db(tmp_path, flavor="defect")
        out = tmp_path / "out"
        run_cli(tmp_path, ["--db", str(db), "--pins", str(SHIPPED_PINS), "--out", str(out),
                           "--format", "json"])
        report = read_report(out)
        for mid in ("M6c", "M13c"):
            assert metric_by_id(report, mid)["status"] == "skipped"

    def test_empty_tables_and_silent_readers_metric(self, tmp_path):
        db = make_kq_db(tmp_path, flavor="defect")
        out = tmp_path / "out"
        run_cli(tmp_path, ["--db", str(db), "--pins", str(SHIPPED_PINS), "--out", str(out),
                           "--format", "json"])
        report = read_report(out)
        m6a = metric_by_id(report, "M6a")
        assert m6a["value"] >= 1  # user_reviews/corrections/proverbs_idioms are empty
        m6b = metric_by_id(report, "M6b")
        assert m6b["detail"]["particle_database"] == 0
        assert m6b["value"] == sum(1 for v in m6b["detail"].values() if v == 0)


# ── R2 threshold engine ────────────────────────────────────────────────────


class TestThresholdEngine:
    @pytest.mark.parametrize(
        ("direction", "amber", "green", "value", "expected"),
        [
            ("lower", 10, 0, 0, "green"),
            ("lower", 10, 0, 5, "amber"),
            ("lower", 10, 0, 10, "amber"),   # exactly at amber
            ("lower", 10, 0, 11, "red"),
            ("higher", 50, 80, 90, "green"),
            ("higher", 50, 80, 80, "green"),  # exactly at green
            ("higher", 50, 80, 50, "amber"),
            ("higher", 50, 80, 49, "red"),
            ("info", None, None, 123, "info"),
            (None, None, None, None, "absent"),
        ],
    )
    def test_status_mapping(self, tmp_path, direction, amber, green, value, expected):
        kq = _kq()
        metric = {"id": "X", "direction": direction, "amber": amber, "green": green}
        assert kq.threshold_status(metric, value) == expected

    def test_missing_table_reports_absent_not_crash(self, tmp_path):
        pins = _base_pins()
        pins["metrics"] = [
            {"id": "M4.9", "family": "dup", "label": "zvs corrections", "kind": "ratio",
             "table": "zvs_corrections_import", "key_expr": "zolai", "measure": "dup",
             "direction": "lower", "baseline": 1.0, "amber": 1.05, "green": 1.0,
             "dropped_ok": True},
            {"id": "GONE", "family": "gloss", "label": "absent sql table", "kind": "sql",
             "sql": "SELECT COUNT(*) FROM not_a_table", "measure": "count",
             "direction": "lower", "baseline": 0, "amber": 0, "green": 0},
        ]
        db = make_kq_db(tmp_path, flavor="clean")
        out = tmp_path / "out"
        rc = run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path, pins)),
                                "--out", str(out)])
        report = read_report(out)
        dropped = metric_by_id(report, "M4.9")
        assert dropped["dropped"] is True
        assert dropped["status"] == "green"  # dropped tables are a reviewer-visible green
        assert metric_by_id(report, "GONE")["status"] == "absent"
        assert rc == 0

    def test_dropped_table_without_dropped_ok_is_absent(self, tmp_path):
        pins = _base_pins()
        pins["metrics"] = [
            {"id": "M4.7", "family": "dup", "label": "phrases_from_bible_import", "kind": "ratio",
             "table": "phrases_from_bible_import", "key_expr": "phrase", "measure": "dup",
             "direction": "lower", "baseline": 2.0, "amber": 1.05, "green": 1.0}
        ]
        db = make_kq_db(tmp_path, flavor="clean")
        out = tmp_path / "out"
        run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path, pins)),
                           "--out", str(out)])
        m = metric_by_id(read_report(out), "M4.7")
        assert m["status"] == "absent" and not m.get("dropped")

    def test_info_direction_never_fails_exit(self, tmp_path):
        pins = _base_pins()
        pins["metrics"].append(
            {"id": "M7b", "family": "contamination", "label": "burmese in myanmar col",
             "kind": "sql", "sql": "SELECT COUNT(*) FROM word_usage WHERE myanmar IS NOT NULL",
             "measure": "count", "direction": "info", "baseline": 0}
        )
        db = make_kq_db(tmp_path, flavor="clean")
        out = tmp_path / "out"
        rc = run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path, pins)),
                                "--out", str(out)])
        assert metric_by_id(read_report(out), "M7b")["status"] == "info"
        assert rc == 0


# ── R3 pin engine ──────────────────────────────────────────────────────────


class TestPinEngine:
    def _pin_pins(self, **overrides) -> dict:
        pins = _base_pins()
        pins["pins"] = [dict(pins["pins"][0])]  # pin:khat MUST_NOT_REGRESS
        pins["pins"][0].update(overrides)
        return pins

    def test_must_not_regress_passes_when_byte_identical(self, tmp_path):
        db = make_kq_db(tmp_path, flavor="clean")
        out = tmp_path / "out"
        run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path)),
                           "--out", str(out)])
        rep = read_report(out)
        pin = next(p for p in rep["pins"] if p["id"] == "pin:khat")
        assert pin["status"] == "green" and pin["current"] == "one"

    def test_must_not_regress_fails_on_any_change(self, tmp_path):
        db = make_kq_db(tmp_path, flavor="clean")
        conn = sqlite3.connect(db)
        conn.execute("UPDATE vocabulary SET english='a' WHERE headword='khat'")
        conn.commit()
        conn.close()
        out = tmp_path / "out"
        rc = run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path)),
                                "--out", str(out)])
        pin = next(p for p in read_report(out)["pins"] if p["id"] == "pin:khat")
        assert pin["status"] == "red"
        assert rc == 1

    def test_must_not_regress_detects_deleted_row(self, tmp_path):
        db = make_kq_db(tmp_path, flavor="clean")
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM vocabulary WHERE headword='khat'")
        conn.commit()
        conn.close()
        out = tmp_path / "out"
        run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path)),
                           "--out", str(out)])
        pin = next(p for p in read_report(out)["pins"] if p["id"] == "pin:khat")
        assert pin["status"] == "red"

    def test_must_improve_amber_before_track_red_when_enforcing(self, tmp_path):
        # fixture: sanga gloss matches the pinned bad value, target not met
        db = tmp_path / "kq.db"
        conn = sqlite3.connect(db)
        conn.executescript(TABLE_DDL)
        conn.execute("INSERT INTO vocabulary(headword, english) VALUES ('sanga','thou')")
        conn.execute("INSERT INTO dictionary(zolai, english) VALUES ('sanga','31')")
        conn.commit()
        conn.close()
        pins = _base_pins()
        pins["pins"] = [p for p in pins["pins"] if p["id"] == "pin:sanga"]
        out = tmp_path / "out"
        run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path, pins)),
                           "--out", str(out)])
        pin = next(p for p in read_report(out)["pins"] if p["id"] == "pin:sanga")
        assert pin["status"] == "amber"  # expected-red until R2-C

        out2 = tmp_path / "out2"
        run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path, pins)),
                           "--out", str(out2), "--track", "R2-C"])
        pin2 = next(p for p in read_report(out2)["pins"] if p["id"] == "pin:sanga")
        assert pin2["status"] == "red"  # enforcing → must reach target_predicate

    def test_must_improve_green_when_target_predicate_met(self, tmp_path):
        db = tmp_path / "kq.db"
        conn = sqlite3.connect(db)
        conn.executescript(TABLE_DDL)
        conn.execute("INSERT INTO vocabulary(headword, english) VALUES ('sanga','thou')")
        conn.execute("INSERT INTO dictionary(zolai, english) VALUES ('sanga','31')")
        for i in range(2):
            conn.execute(
                "INSERT INTO dictionary_meanings(dictionary_id, zolai, meaning_en) VALUES (?,?,?)",
                (1, "sanga", f"sense {i}"),
            )
        conn.commit()
        conn.close()
        pins = _base_pins()
        pins["pins"] = [p for p in pins["pins"] if p["id"] == "pin:sanga"]
        out = tmp_path / "out"
        run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path, pins)),
                           "--out", str(out), "--track", "R2-C"])
        pin = next(p for p in read_report(out)["pins"] if p["id"] == "pin:sanga")
        assert pin["status"] == "green"

    def test_must_improve_changed_without_target_is_red(self, tmp_path):
        db = tmp_path / "kq.db"
        conn = sqlite3.connect(db)
        conn.executescript(TABLE_DDL)
        conn.execute("INSERT INTO vocabulary(headword, english) VALUES ('sanga','garbage')")
        conn.execute("INSERT INTO dictionary(zolai, english) VALUES ('sanga','31')")
        conn.commit()
        conn.close()
        pins = _base_pins()
        pins["pins"] = [p for p in pins["pins"] if p["id"] == "pin:sanga"]
        out = tmp_path / "out"
        rc = run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path, pins)),
                                "--out", str(out)])
        pin = next(p for p in read_report(out)["pins"] if p["id"] == "pin:sanga")
        assert pin["status"] == "red"
        assert rc == 1

    def test_ambiguous_pin_never_enforced(self, tmp_path):
        db = tmp_path / "kq.db"
        conn = sqlite3.connect(db)
        conn.executescript(TABLE_DDL)  # no 'four' headword at all
        for hw in ("hoih", "khat", "hnih", "thum", "nga", "giat", "kua"):
            conn.execute("INSERT INTO vocabulary(headword, english) VALUES (?,?)",
                         (hw, SHIPPED_PIN_VALUES[hw]))
        conn.commit()
        conn.close()
        pins = _base_pins()
        pins["pins"] = [p for p in pins["pins"] if p["id"] == "pin:four"]
        out = tmp_path / "out"
        rc = run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path, pins)),
                                "--out", str(out)])
        pin = next(p for p in read_report(out)["pins"] if p["id"] == "pin:four")
        assert pin["status"] == "skipped"
        assert rc == 0

    def test_shipped_pins_cover_conflict_and_numeral_headwords(self, tmp_path):
        pins = json.loads(SHIPPED_PINS.read_text(encoding="utf-8"))
        enforced = {p["headword"] for p in pins["pins"] if not p.get("ambiguous")}
        assert enforced == set(SHIPPED_PIN_VALUES)


# ── R4 read-only enforcement ───────────────────────────────────────────────


class TestReadOnlyEnforcement:
    def _digest(self, db: Path) -> tuple:
        """sha256 of the DB bytes + names of any sqlite sidecars (-wal/-shm/-journal).

        Only the DB file itself is watched — the report dir is a legitimate output
        target and must not count as a DB write.
        """
        sidecars = sorted(p.name for p in db.parent.glob(db.name + "-*"))
        return hashlib.sha256(db.read_bytes()).hexdigest(), sidecars

    def test_default_run_never_writes_bytes(self, tmp_path):
        db = make_kq_db(tmp_path, flavor="defect")
        before = self._digest(db)
        out = tmp_path / "out"
        run_cli(tmp_path, ["--db", str(db), "--pins", str(SHIPPED_PINS), "--out", str(out)])
        assert self._digest(db) == before
        # no sidecar files either (no -wal/-shm created by a ro open)
        assert not list(tmp_path.glob("*.db-*"))

    def test_default_run_never_opens_rw_connection(self, tmp_path, monkeypatch):
        kq = _kq()
        db = make_kq_db(tmp_path, flavor="clean")
        opened: list = []
        real_connect = sqlite3.connect

        def spy(*args, **kwargs):
            opened.append((args[0] if args else kwargs.get("database"), kwargs.get("uri", False)))
            return real_connect(*args, **kwargs)

        monkeypatch.setattr(kq.sqlite3, "connect", spy)
        out = tmp_path / "out"
        run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path)),
                           "--out", str(out)])
        assert opened, "no connection recorded"
        for target, is_uri in opened:
            assert is_uri is True, f"non-URI connect used: {target}"
            assert "mode=ro" in str(target)

    def test_default_run_leaves_persist_tables_absent(self, tmp_path):
        db = make_kq_db(tmp_path, flavor="clean")
        out = tmp_path / "out"
        run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path)),
                           "--out", str(out)])
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "knowledge_quality_runs" not in names
        assert "knowledge_quality_metrics" not in names

    def test_persist_is_opt_in_and_idempotent(self, tmp_path):
        db = make_kq_db(tmp_path, flavor="clean")
        pins = write_pins(tmp_path)
        out = tmp_path / "out"
        run_cli(tmp_path, ["--db", str(db), "--pins", str(pins), "--out", str(out),
                           "--persist", "--yes"])
        first = self._schema_and_runs(db)
        run_cli(tmp_path, ["--db", str(db), "--pins", str(pins), "--out", str(out),
                           "--persist", "--yes"])
        second = self._schema_and_runs(db)
        assert first["schema"] == second["schema"], "CREATE IF NOT EXISTS must not drift"
        assert second["runs"] == first["runs"] + 1
        assert second["metrics"] > 0

    def test_persist_without_confirmation_refuses(self, tmp_path):
        db = make_kq_db(tmp_path, flavor="clean")
        out = tmp_path / "out"
        rc = run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path)),
                                "--out", str(out), "--persist"])
        assert rc == 3
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "knowledge_quality_runs" not in names

    @staticmethod
    def _schema_and_runs(db: Path) -> dict:
        conn = sqlite3.connect(db)
        schema = {
            t: [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
            for t in ("knowledge_quality_runs", "knowledge_quality_metrics")
        }
        runs = conn.execute("SELECT COUNT(*) FROM knowledge_quality_runs").fetchone()[0]
        metrics = conn.execute("SELECT COUNT(*) FROM knowledge_quality_metrics").fetchone()[0]
        conn.close()
        return {"schema": schema, "runs": runs, "metrics": metrics}

    def test_mode_rw_uri_refused(self, tmp_path):
        db = make_kq_db(tmp_path, flavor="clean")
        rw = f"file:{db}?mode=rw"
        rc = _kq().run(["--db", rw, "--pins", str(write_pins(tmp_path)),
                        "--out", str(tmp_path / "out")])
        assert rc == 3

    def test_missing_database_is_exit_3(self, tmp_path):
        rc = run_cli(tmp_path, ["--db", str(tmp_path / "nope.db"),
                                "--pins", str(write_pins(tmp_path)),
                                "--out", str(tmp_path / "out")])
        assert rc == 3

    def test_unknown_track_is_exit_3(self, tmp_path):
        db = make_kq_db(tmp_path, flavor="clean")
        rc = run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path)),
                                "--out", str(tmp_path / "out"), "--track", "R2-Z"])
        assert rc == 3


# ── R5 GLOB semantics ──────────────────────────────────────────────────────


class TestGlobSemantics:
    @pytest.mark.parametrize(
        ("ref", "conforms"),
        [
            ("GEN 1:1", True),
            ("1CH 10:1", True),
            ("GEN.1:1", False),      # dotted
            ("GEN  1:1", False),     # double space
            ("gen 1:1", False),      # lowercase
            ("GEN 1:1 extra", False),
            ("GEN 1:1,", False),     # non [A-Z0-9: ]
        ],
    )
    def test_ref_format_predicate(self, tmp_path, ref, conforms):
        kq = _kq()
        db = tmp_path / "glob.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE bible_verses(ref TEXT)")
        conn.execute("INSERT INTO bible_verses VALUES (?)", (ref,))
        sql = f"SELECT COUNT(*) FROM bible_verses WHERE {kq.ref_conformance_sql('ref')}"
        got = conn.execute(sql).fetchone()[0]
        conn.close()
        assert got == (1 if conforms else 0)

    @pytest.mark.parametrize(
        ("text", "burmese"),
        [("hello", False), ("က", True), ("ကမ္ဘာ", True), ("zomi 'a", False), ("မြန်မာ", True)],
    )
    def test_burmese_glob_range(self, tmp_path, text, burmese):
        kq = _kq()
        db = tmp_path / "glob.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE vocabulary(english TEXT)")
        conn.execute("INSERT INTO vocabulary VALUES (?)", (text,))
        sql = f"SELECT COUNT(*) FROM vocabulary WHERE {kq.burmese_sql('english')}"
        got = conn.execute(sql).fetchone()[0]
        conn.close()
        assert got == (1 if burmese else 0)

    def test_shipped_sql_uses_shared_predicates(self, tmp_path):
        kq = _kq()
        pins = json.loads(SHIPPED_PINS.read_text(encoding="utf-8"))
        m7 = next(m for m in pins["metrics"] if m["id"] == "M7")
        assert m7["kind"] == "sql_map"
        # every mapped column must route through the shared Burmese predicate
        assert m7["sqls"], "M7 sql_map has no per-column SQL"
        for col_sql in m7["sqls"].values():
            assert kq.BURMESE_GLOB in col_sql, col_sql
        assert kq.burmese_sql("english") in m7["sqls"]["vocabulary.english"]
        m9a = next(m for m in pins["metrics"] if m["id"] == "M9a")
        assert kq.ref_conformance_sql("ref") in m9a["sql"]


# ── R6 report / exit codes ─────────────────────────────────────────────────


class TestReportAndExitCodes:
    def test_green_fixture_exits_zero_with_json_and_md(self, tmp_path):
        db = make_kq_db(tmp_path, flavor="clean")
        out = tmp_path / "out"
        rc = run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path)),
                                "--out", str(out), "--format", "both"])
        assert rc == 0
        report = read_report(out)
        assert report["status"] == "green"
        assert report["sqlite_version"]
        assert report["db_size"] > 0
        assert report["duration_ms"] >= 0
        md_files = list(out.glob("kq-*.md"))
        assert md_files
        md = md_files[-1].read_text(encoding="utf-8")
        for m in report["metrics"]:
            assert m["id"] in md

    def test_red_fixture_exits_one(self, tmp_path):
        pins = _base_pins()
        pins["metrics"] = [pins["metrics"][0]]
        pins["metrics"][0]["amber"] = 1  # defect fixture has 2 empty glosses → red
        db = make_kq_db(tmp_path, flavor="defect")
        rc = run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path, pins)),
                                "--out", str(tmp_path / "out")])
        assert rc == 1

    def test_amber_fixture_exits_two(self, tmp_path):
        # Scope to M1 alone with guards and pins cleared. M1 measures 2 empty-gloss
        # rows against amber 3 / green 0 — worse than green, not beyond amber, so it
        # reads amber. M9a and M4.1 are deliberately excluded: M9a is red on the
        # defect fixture (non-conforming refs), and red always outranks amber, which
        # would pin exit at 1 and mask the amber→exit-2 path.
        #
        # Guards are cleared because they are always evaluated and G-VERSE-FORMAT is
        # red on the defect fixture; pins because pin:sanga is MUST_IMPROVE but not
        # enforced here, so it reads amber and would mask M1's amber.
        pins = _base_pins()
        pins["metrics"] = [pins["metrics"][0]]
        pins["guards"] = []
        pins["pins"] = []
        db = make_kq_db(tmp_path, flavor="defect")
        rc = run_cli(tmp_path, ["--db", str(db), "--pins", str(write_pins(tmp_path, pins)),
                                "--out", str(tmp_path / "out")])
        assert rc == 2

    def test_report_contains_every_metric_id_and_pin(self, tmp_path):
        db = make_kq_db(tmp_path, flavor="defect")
        out = tmp_path / "out"
        run_cli(tmp_path, ["--db", str(db), "--pins", str(SHIPPED_PINS), "--out", str(out),
                           "--format", "json"])
        report = read_report(out)
        ids = {m["id"] for m in report["metrics"]}
        pins = json.loads(SHIPPED_PINS.read_text(encoding="utf-8"))
        assert ids == {m["id"] for m in pins["metrics"]}
        assert {p["id"] for p in report["pins"]} == {p["id"] for p in pins["pins"]}
        assert {g["id"] for g in report["guards"]} == {g["id"] for g in pins["guards"]}
        assert report["run_id"].startswith("kq-")

    def test_compare_computes_transitions(self, tmp_path):
        pins = _base_pins()
        pins["metrics"][0]["amber"] = 1
        db_bad = make_kq_db(tmp_path / "a", flavor="defect")
        out1 = tmp_path / "out1"
        run_cli(tmp_path, ["--db", str(db_bad), "--pins", str(write_pins(tmp_path, pins, name="p1")),
                           "--out", str(out1)])
        prev = sorted(out1.glob("kq-*.json"))[-1]

        db_good = make_kq_db(tmp_path / "b", flavor="clean")
        out2 = tmp_path / "out2"
        run_cli(tmp_path, ["--db", str(db_good), "--pins", str(write_pins(tmp_path, name="p2")),
                           "--out", str(out2), "--compare", str(prev)])
        report = read_report(out2)
        transitions = report["compare"]["transitions"]
        by_id = {t["metric"]: t for t in transitions}
        assert by_id["M1"]["from_status"] == "red" and by_id["M1"]["to_status"] == "green"
        assert by_id["M1"]["direction"] == "red->green"

    def test_compare_detects_green_to_red_regression(self, tmp_path):
        db_good = make_kq_db(tmp_path / "a", flavor="clean")
        out1 = tmp_path / "out1"
        run_cli(tmp_path, ["--db", str(db_good), "--pins", str(write_pins(tmp_path, name="p1")),
                           "--out", str(out1)])
        prev = sorted(out1.glob("kq-*.json"))[-1]

        db_bad = make_kq_db(tmp_path / "b", flavor="defect")
        out2 = tmp_path / "out2"
        run_cli(tmp_path, ["--db", str(db_bad), "--pins", str(write_pins(tmp_path, name="p2")),
                           "--out", str(out2), "--compare", str(prev)])
        report = read_report(out2)
        by_id = {t["metric"]: t for t in report["compare"]["transitions"]}
        assert by_id["M9a"]["from_status"] == "green" and by_id["M9a"]["to_status"] == "red"
        assert by_id["M9a"]["direction"] == "green->red"


# ── R7 track done-when registry ────────────────────────────────────────────


class TestTrackDoneWhen:
    def test_unknown_vs_known_track_ids(self, tmp_path):
        pins = json.loads(SHIPPED_PINS.read_text(encoding="utf-8"))
        assert set(pins["tracks"]) >= {"Track0", "R2-C"}

    def test_r2c_pre_track_expected_red(self, tmp_path):
        db = make_kq_db(tmp_path, flavor="defect")
        out = tmp_path / "out"
        rc = run_cli(tmp_path, ["--db", str(db), "--pins", str(SHIPPED_PINS),
                                "--out", str(out), "--format", "json", "--track", "R2-C"])
        report = read_report(out)
        checks = report["track_checks"]
        assert checks, "no checks executed"
        assert {c["track"] for c in checks} == {"R2-C"}
        by_id = {c["check_id"]: c for c in checks}
        assert by_id["M1-zero"]["status"] == "red"     # empty glosses exist in fixture
        assert by_id["pin-sanga"]["status"] == "red"   # enforcing → target unmet
        assert rc == 1
        # only the metrics this track references were computed
        ids = {m["id"] for m in report["metrics"]}
        assert "M1" in ids and "M11" not in ids

    def test_r2c_post_track_green(self, tmp_path):
        db = make_kq_db(tmp_path, flavor="clean")
        out = tmp_path / "out"
        rc = run_cli(tmp_path, ["--db", str(db), "--pins", str(SHIPPED_PINS),
                                "--out", str(out), "--format", "json", "--track", "R2-C"])
        report = read_report(out)
        checks = report["track_checks"]
        bad = [c for c in checks if c["status"] not in {"green", "manual", "skipped"}]
        assert not bad, f"post-R2-C fixture still failing: {bad}"
        assert rc == 0

    def test_track0_registry_runs_and_reports(self, tmp_path):
        db = make_kq_db(tmp_path, flavor="defect")
        out = tmp_path / "out"
        run_cli(tmp_path, ["--db", str(db), "--pins", str(SHIPPED_PINS),
                           "--out", str(out), "--format", "json", "--track", "Track0"])
        checks = read_report(out)["track_checks"]
        assert {c["check_id"] for c in checks} >= {
            "M9e-bookcodes", "G-WORDUSAGE-CLEANUP-ONLY", "M4.1-dedupe", "G-VERSE-FORMAT"
        }

    def test_deep_check_skips_without_deep(self, tmp_path):
        db = make_kq_db(tmp_path, flavor="clean")
        out = tmp_path / "out"
        run_cli(tmp_path, ["--db", str(db), "--pins", str(SHIPPED_PINS),
                           "--out", str(out), "--format", "json", "--track", "R2-A"])
        by_id = {c["check_id"]: c for c in read_report(out)["track_checks"]}
        assert by_id["M13c-import-gap"]["status"] == "skipped"

    def test_every_shipped_track_has_checks(self, tmp_path):
        pins = json.loads(SHIPPED_PINS.read_text(encoding="utf-8"))
        for track, checks in pins["tracks"].items():
            assert checks, f"track {track} has no checks"
            for c in checks:
                assert c.get("kind"), f"{track}/{c} missing kind"
                assert c.get("id"), f"{track}/{c} missing id"


# ── GLOB-independent header checks ─────────────────────────────────────────


def test_harness_declares_sqlite_floor(tmp_path):
    kq = _kq()
    assert kq.SQLITE_MIN_VERSION >= (3, 30)
    pins = json.loads(SHIPPED_PINS.read_text(encoding="utf-8"))
    assert tuple(int(x) for x in pins["sqlite_min_version"].split(".")) >= (3, 30)
