"""Desktop Router — handles all 14 tool categories for the Zolai Desktop UI.
Routes desktop UI calls to the appropriate zolai-core functions and scripts."""
import json
import subprocess
import sys

from fastapi import APIRouter, Query

from ..config import config

router = APIRouter(prefix="/desktop", tags=["desktop"])

SCRIPTS_DIR = config.paths.datasets_scripts
ZOLAI_CORE = str(config.paths.root)

# Scripts are grouped under zolai-datasets/scripts/<category>/. Try the
# category folders in this order after a direct lookup.
SCRIPT_SUBDIRS = ("my", "bible", "training", "gemini", "annotation", "online", "zvs")

def resolve_script(name: str):
    """Locate a script under SCRIPTS_DIR, checking the root then category folders."""
    direct = SCRIPTS_DIR / name
    if direct.exists():
        return direct
    for sub in SCRIPT_SUBDIRS:
        candidate = SCRIPTS_DIR / sub / name
        if candidate.exists():
            return candidate
    return None

def run_script(name: str, *args: str, timeout: int = 120):
    """Run a zolai script and return parsed JSON output."""
    script = resolve_script(name)
    if not script:
        return {"error": f"Script not found: {name}"}
    try:
        r = subprocess.run(
            [sys.executable, str(script), *args],
            capture_output=True, text=True, timeout=timeout,
            cwd=ZOLAI_CORE
        )
        if r.returncode != 0:
            return {"error": f"Script failed: {r.stderr[:500]}"}
        try:
            return json.loads(r.stdout) if r.stdout.strip() else {"success": True}
        except json.JSONDecodeError:
            return {"output": r.stdout[:2000]}
    except subprocess.TimeoutExpired:
        return {"error": "Script timed out"}
    except Exception as e:
        return {"error": str(e)}

def get_db():
    """Get SQLite connection to the canonical DB."""
    import sqlite3
    db_path = config.paths.zolai_db
    return sqlite3.connect(str(db_path), timeout=5)

def serialize_row(row):
    """Serialize a database row."""
    return [str(c) if c is not None else "" for c in row]

def rows_to_dicts(cursor, rows):
    """Convert rows to list of dicts."""
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in rows]


# ═══════════════════════════════════════════
# DATABASE TOOLS
# ═══════════════════════════════════════════

@router.get("/tables")
async def db_tables():
    """List all database tables with row counts."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [r[0] for r in cur.fetchall() if r[0] != 'sqlite_sequence']
        result = []
        for t in tables:
            cur.execute(f"SELECT COUNT(*) FROM [{t}]")
            count = cur.fetchone()[0]
            result.append({"table": t, "rows": count})
        cur.close()
        conn.close()
        return {"tables": result, "total": len(result)}
    except Exception as e:
        return {"error": str(e)}

@router.get("/query")
async def db_query(sql: str = Query(...)):
    """Run a SQL query (SELECT only)."""
    try:
        if not sql.strip().upper().startswith("SELECT"):
            return {"error": "Only SELECT queries allowed"}
        conn = get_db()
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        cur.close()
        conn.close()
        return {"columns": cols, "rows": [serialize_row(r) for r in rows], "count": len(rows)}
    except Exception as e:
        return {"error": str(e)}

@router.get("/stats")
async def db_stats():
    """Get database statistics (dashboard shape)."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [r[0] for r in cur.fetchall() if r[0] != 'sqlite_sequence']

        def n(name: str) -> int:
            try:
                cur.execute(f"SELECT COUNT(*) FROM [{name}]")
                return cur.fetchone()[0]
            except Exception:
                return 0

        result = {}
        total = 0
        for t in tables:
            c = n(t)
            result[t] = c
            total += c

        # Capture specific counts before touching pragma (avoids cursor issues)
        dict_total = n("dictionary")
        my_count = 0
        try:
            cur.execute(
                "SELECT COUNT(*) FROM dictionary WHERE myanmar IS NOT NULL AND myanmar != ''"
            )
            my_count = cur.fetchone()[0]
        except Exception:
            my_count = 0
        pct = round(my_count / dict_total * 100, 1) if dict_total else 0.0

        # DB size — pragma query can contaminate cursor, so do it LAST
        cur.execute("SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()")
        size_bytes = cur.fetchone()[0]
        cur.close()
        conn.close()
        return {
            "total_tables": len(tables),
            "total_rows": total,
            "database_size_mb": round(size_bytes / 1024 / 1024, 1),
            "dictionary": {"total": result.get("dictionary", 0), "coverage_myanmar_pct": pct},
            "bible": {"total_verses": result.get("bible_verses", 0)},
            "training": {
                "translation_pairs": result.get("translations", 0),
                "training_exercises": result.get("training_exercises", 0),
                "vocabulary_entries": result.get("vocab", 0),
                "phrases": result.get("phrases", 0),
                "grammar_patterns": result.get("grammar_patterns", 0),
                "proverbs": result.get("proverbs", 0),
                "word_alignments": result.get("word_alignments", 0),
                "word_usage_profiles": result.get("word_usage", 0),
            },
            "provenance": {"audit_log_entries": result.get("data_audit_log", 0)},
            "table_details": result,
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/table-data")
async def table_data(
    table: str = Query(...),
    page: int = Query(1),
    page_size: int = Query(25),
    sort_by: str = Query(None),
    sort_dir: str = Query("asc"),
):
    """Get paginated rows from any table with optional sorting."""
    try:
        conn = get_db()
        cur = conn.cursor()

        # Validate table exists
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        if not cur.fetchone():
            cur.close()
            conn.close()
            return {"error": f"Table not found: {table}"}

        # Total rows
        cur.execute(f"SELECT COUNT(*) FROM [{table}]")
        total_rows = cur.fetchone()[0]
        total_pages = max(1, (total_rows + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))

        # Build query
        offset = (page - 1) * page_size
        sql = f"SELECT * FROM [{table}]"
        if sort_by:
            direction = "DESC" if sort_dir.lower() == "desc" else "ASC"
            sql += f" ORDER BY [{sort_by}] {direction}"
        sql += " LIMIT ? OFFSET ?"

        cur.execute(sql, (page_size, offset))
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]

        cur.close()
        conn.close()
        return {
            "table": table,
            "columns": cols,
            "rows": [dict(zip(cols, r)) for r in rows],
            "total_rows": total_rows,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/table-schema")
async def table_schema(table: str = Query(...)):
    """Get column schema for a table via PRAGMA table_info."""
    try:
        conn = get_db()
        cur = conn.cursor()

        # Validate table exists
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        if not cur.fetchone():
            cur.close()
            conn.close()
            return {"error": f"Table not found: {table}"}

        cur.execute(f"PRAGMA table_info([{table}])")
        rows = cur.fetchall()
        cur.close()
        conn.close()

        columns = []
        for r in rows:
            columns.append({
                "cid": r[0],
                "name": r[1],
                "type": r[2],
                "notnull": bool(r[3]),
                "default_value": r[4],
                "pk": bool(r[5]),
            })

        return {"table": table, "columns": columns}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════
# DICTIONARY TOOLS
# ═══════════════════════════════════════════

@router.get("/dict/browse")
async def dict_browse(limit: int = 50, offset: int = 0, q: str = None, clean: bool = False):
    """Browse dictionary entries with pagination, optional letter filter, and clean filter."""
    try:
        conn = get_db()
        cur = conn.cursor()

        # Build WHERE clause for clean filter
        clean_where = ""
        if clean:
            clean_where = "WHERE zolai NOT LIKE '-%' AND zolai NOT LIKE '%-%' AND english NOT GLOB '*[0-9]*' AND myanmar IS NOT NULL AND myanmar != ''"

        if q:
            # Letter filter - search for entries starting with the letter
            if clean:
                cur.execute(
                    f"SELECT zolai, english, myanmar, pos, source FROM dictionary "
                    f"{clean_where} AND zolai LIKE ? ORDER BY zolai LIMIT ? OFFSET ?",
                    (f"{q}%", limit, offset),
                )
            else:
                cur.execute(
                    "SELECT zolai, english, myanmar, pos, source FROM dictionary "
                    "WHERE zolai LIKE ? ORDER BY zolai LIMIT ? OFFSET ?",
                    (f"{q}%", limit, offset),
                )
        else:
            if clean:
                cur.execute(
                    f"SELECT zolai, english, myanmar, pos, source FROM dictionary "
                    f"{clean_where} ORDER BY zolai LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            else:
                cur.execute(
                    "SELECT zolai, english, myanmar, pos, source FROM dictionary "
                    "ORDER BY zolai LIMIT ? OFFSET ?",
                    (limit, offset),
                )

        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]

        # Get total count for pagination
        if q:
            if clean:
                cur.execute(f"SELECT COUNT(*) FROM dictionary {clean_where} AND zolai LIKE ?", (f"{q}%",))
            else:
                cur.execute("SELECT COUNT(*) FROM dictionary WHERE zolai LIKE ?", (f"{q}%",))
        else:
            if clean:
                cur.execute(f"SELECT COUNT(*) FROM dictionary {clean_where}")
            else:
                cur.execute("SELECT COUNT(*) FROM dictionary")
        total = cur.fetchone()[0]

        cur.close()
        conn.close()
        return {
            "results": [dict(zip(cols, r)) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    except Exception as e:
        return {"error": str(e)}

@ router.get("/dict/stats")
async def dict_stats():
    """Get dictionary statistics."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM dictionary")
        total = cur.fetchone()[0]
        cur.execute("SELECT pos, COUNT(*) FROM dictionary GROUP BY pos ORDER BY COUNT(*) DESC LIMIT 10")
        pos_counts = dict(cur.fetchall())
        cur.execute("SELECT COUNT(*) FROM dictionary WHERE myanmar IS NOT NULL AND myanmar != ''")
        my_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM dictionary WHERE english IS NOT NULL AND english != ''")
        en_clean = cur.fetchone()[0]
        cur.close()
        conn.close()
        return {"total": total, "with_myanmar": my_count, "with_english": en_clean, "pos_breakdown": pos_counts}
    except Exception as e:
        return {"error": str(e)}

@ router.get("/dict/non-zolai")
async def dict_non_zolai():
    """Check for non-Zolai words."""
    return run_script("check_non_zolai.py", "--quiet")


# ═══════════════════════════════════════════
# BIBLE TOOLS
# ═══════════════════════════════════════════

@ router.get("/bible/study")
async def bible_study(book: str = Query(...)):
    """Study a Bible book."""
    return run_script("bible_engine.py", "--study", "--book", book)

@ router.get("/bible/learn")
async def bible_learn(level: str = Query(...)):
    """Progressive learning exercise."""
    return run_script("bible_engine.py", "--learn", "--level", level)

@ router.get("/bible/context/book")
async def bible_context_book(book: str = Query(...)):
    """Per-book context analysis."""
    return run_script("context_deep_learner.py", "--book", book)

@ router.get("/bible/context/word")
async def bible_context_word(word: str = Query(...)):
    """Word usage profile."""
    return run_script("context_deep_learner.py", "--word", word)

@ router.get("/bible/context/topics")
async def bible_context_topics():
    """List topic clusters."""
    return run_script("context_deep_learner.py", "--topics")


# ═══════════════════════════════════════════
# BIBLE NAVIGATION ENDPOINTS
# ═══════════════════════════════════════════

# Canonical Bible book order (66 books, GEN → REV)
BIBLE_BOOK_ORDER = [
    'GEN', 'EXO', 'LEV', 'NUM', 'DEU', 'JOS', 'JDG', 'RUT',
    '1SA', '2SA', '1KI', '2KI', '1CH', '2CH', 'EZR', 'NEH',
    'EST', 'JOB', 'PSA', 'PRO', 'ECC', 'SNG', 'ISA', 'JER',
    'LAM', 'EZK', 'DAN', 'HOS', 'JOL', 'AMO', 'OBA', 'JON',
    'MIC', 'NAM', 'HAB', 'ZEP', 'HAG', 'ZEC', 'MAL',
    'MAT', 'MRK', 'LUK', 'JHN', 'ACT', 'ROM', '1CO', '2CO',
    'GAL', 'EPH', 'PHP', 'COL', '1TH', '2TH', '1TI', '2TI',
    'TIT', 'PHM', 'HEB', 'JAS', '1PE', '2PE', '1JN', '2JN',
    '3JN', 'JUD', 'REV',
]
_BIBLE_ORDER_MAP = {abbr: i for i, abbr in enumerate(BIBLE_BOOK_ORDER)}


@router.get("/bible/books")
async def bible_books():
    """List all Bible books with verse counts in canonical order."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT book, book_name,
                   COUNT(*) as verse_count,
                   MIN(chapter) as min_chapter,
                   MAX(chapter) as max_chapter
            FROM bible_verses
            GROUP BY book
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        books = []
        for r in rows:
            books.append({
                "abbr": r[0],
                "name": r[1] or r[0],
                "verses": r[2],
                "min_chapter": r[3],
                "max_chapter": r[4],
            })
        books.sort(key=lambda b: _BIBLE_ORDER_MAP.get(b["abbr"], 999))
        return {"books": books, "total": len(books)}
    except Exception as e:
        return {"error": str(e)}

@router.get("/bible/chapters")
async def bible_chapters(book: str = Query(...)):
    """List chapters for a Bible book with verse counts."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT chapter, COUNT(*) as verse_count
            FROM bible_verses
            WHERE book = ?
            GROUP BY chapter
            ORDER BY chapter
        """, (book,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        chapters = [{"chapter": r[0], "verses": r[1]} for r in rows]
        return {"book": book, "chapters": chapters, "total": len(chapters)}
    except Exception as e:
        return {"error": str(e)}

@router.get("/bible/verses")
async def bible_verses(
    book: str = Query(...),
    chapter: int = Query(...),
    verse_start: int = Query(None),
    verse_end: int = Query(None),
    versions: str = Query("tdb77,tedim2010,kjv"),
):
    """Get all verses for a Bible chapter with parallel translations.

    Optional verse_start/verse_end filter a range of verses within the chapter.
    """
    try:
        conn = get_db()
        cur = conn.cursor()

        if verse_start is not None and verse_end is not None:
            cur.execute("""
                SELECT verse, zo_tdb77, zo_tedim2010, en_kJV, myanmar, book_name
                FROM bible_verses
                WHERE book = ? AND chapter = ? AND verse >= ? AND verse <= ?
                ORDER BY verse
            """, (book, chapter, verse_start, verse_end))
        elif verse_start is not None:
            cur.execute("""
                SELECT verse, zo_tdb77, zo_tedim2010, en_kJV, myanmar, book_name
                FROM bible_verses
                WHERE book = ? AND chapter = ? AND verse >= ?
                ORDER BY verse
            """, (book, chapter, verse_start))
        else:
            cur.execute("""
                SELECT verse, zo_tdb77, zo_tedim2010, en_kJV, myanmar, book_name
                FROM bible_verses
                WHERE book = ? AND chapter = ?
                ORDER BY verse
            """, (book, chapter))

        rows = cur.fetchall()
        cur.close()
        conn.close()
        verses = []
        for r in rows:
            v = {
                "verse": r[0],
                "zo_tdb77": r[1] or "",
                "zo_tedim2010": r[2] or "",
                "en_kJV": r[3] or "",
                "myanmar": r[4] or "",
                "book_name": r[5] or book,
            }
            verses.append(v)
        return {
            "book": book,
            "chapter": chapter,
            "book_name": verses[0]["book_name"] if verses else book,
            "verses": verses,
            "total": len(verses),
        }
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════
# GEMINI TOOLS
# ═══════════════════════════════════════════

@ router.get("/gemini/fill-en")
async def gemini_fill_en(limit: int = Query(50)):
    """Fill missing English translations."""
    return run_script("gemini_translate.py", "--fill-en", "--limit", str(limit))

@ router.get("/gemini/fill-my")
async def gemini_fill_my(limit: int = Query(50)):
    """Fill missing Myanmar translations."""
    return run_script("gemini_translate.py", "--fill-my", "--limit", str(limit))

@router.get("/gemini/coverage")
async def gemini_coverage():
    """Check translation coverage from database."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM dictionary")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM dictionary WHERE english IS NOT NULL AND english != ''")
        with_english = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM dictionary WHERE myanmar IS NOT NULL AND myanmar != ''")
        with_myanmar = cur.fetchone()[0]
        cur.close()
        conn.close()
        return {
            "total": total,
            "with_english": with_english,
            "with_myanmar": with_myanmar,
            "english_pct": round(with_english / total * 100, 1) if total else 0,
            "myanmar_pct": round(with_myanmar / total * 100, 1) if total else 0,
        }
    except Exception as e:
        return {"error": str(e)}

@ router.get("/gemini/fill")
async def gemini_fill(text: str = Query(...), lang: str = Query("my")):
    """Quick Gemini fill for a word."""
    return run_script("gemini_translate.py", "--fill", "--text", text, "--lang", lang)


# ═══════════════════════════════════════════
# TRAINING TOOLS
# ═══════════════════════════════════════════

@ router.get("/training/generate")
async def training_generate(type: str = Query(...), count: int = Query(1000)):
    """Generate training data."""
    return run_script("generate_training_data.py", "--type", type, "--count", str(count))

@ router.get("/training/generate-sentences")
async def training_sentences():
    """Generate Bible-template sentences."""
    return run_script("generate_sentences.py")

@ router.get("/training/validate")
async def training_validate(text: str = Query(...)):
    """Validate sentences."""
    return run_script("validate_sentences.py", "--text", text)

@ router.get("/training/deep-validate")
async def training_deep_validate(text: str = Query(...)):
    """Deep validate sentences."""
    return run_script("deep_validate.py", "--text", text)

@ router.get("/training/build")
async def training_build():
    """Build training dataset."""
    return run_script("build_training_corpus.py")

@ router.get("/training/build-qwen")
async def training_build_qwen():
    """Build Qwen3 format."""
    return run_script("format_training_data.py")

@ router.get("/training/export")
async def training_export(fmt: str = Query("jsonl")):
    """Export training data."""
    return run_script("build_training_corpus.py", "--export", fmt)

@ router.get("/training/build-corpus")
async def training_corpus_build():
    """Build corpus."""
    return run_script("build_training_corpus.py", "--full")

@ router.get("/training/corpus-stats")
async def training_corpus_stats():
    """Get corpus statistics."""
    return run_script("build_training_corpus.py", "--stats")


# ═══════════════════════════════════════════
# TEST & QUIZ TOOLS
# ═══════════════════════════════════════════

@ router.get("/test/quiz")
async def test_quiz(level: str = Query("A1"), qtype: str = Query("all")):
    """Proficiency quiz."""
    return run_script("proficiency_test.py", "--level", level, "--type", qtype)

@ router.get("/test/stats")
async def test_stats():
    """Test statistics."""
    return run_script("proficiency_test.py", "--stats")


# ═══════════════════════════════════════════
# GRAMMAR TOOLS
# ═══════════════════════════════════════════

@ router.get("/grammar/check")
async def grammar_check(text: str = Query(...)):
    """Check grammar."""
    return run_script("grammar_check.py", "--text", text)

@ router.get("/grammar/negation-rules")
async def grammar_negation():
    """Get negation rules reference."""
    return run_script("grammar_check.py", "--negation-rules")


# ═══════════════════════════════════════════
# PARAGRAPH TOOLS
# ═══════════════════════════════════════════

@ router.get("/paragraph/analyze")
async def paragraph_analyze(text: str = Query(...)):
    """Analyze paragraph."""
    return run_script("paragraph_engine.py", "--analyze", "--text", text)

@ router.get("/paragraph/style")
async def paragraph_style(text: str = Query(...), style: str = Query("FORMAL")):
    """Style transfer."""
    return run_script("paragraph_engine.py", "--style", style, "--text", text)

@ router.get("/paragraph/paraphrase")
async def paragraph_paraphrase(text: str = Query(...), level: str = Query("Minimal")):
    """Paraphrase text."""
    return run_script("paragraph_engine.py", "--paraphrase", "--level", level, "--text", text)


# ═══════════════════════════════════════════
# ZVS TOOLS
# ═══════════════════════════════════════════

@ router.get("/zvs/validate")
async def zvs_validate(text: str = Query(...)):
    """Validate ZVS 2018 compliance."""
    return run_script("zolai-zvs", "validate", text)

@ router.get("/zvs/forbidden")
async def zvs_forbidden():
    """List forbidden forms."""
    return run_script("zolai-zvs", "forbidden")


# ═══════════════════════════════════════════
# PATTERN TOOLS
# ═══════════════════════════════════════════

@ router.get("/pattern/stats")
async def pattern_stats():
    """Get grammar pattern statistics from grammar_patterns table."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM grammar_patterns")
        total = cur.fetchone()[0]
        cur.execute("SELECT function, COUNT(*) FROM grammar_patterns GROUP BY function ORDER BY COUNT(*) DESC")
        by_function = dict(cur.fetchall())
        cur.close()
        conn.close()
        output = json.dumps({"total": total, "by_function": by_function}, indent=2)
        return {"output": output}
    except Exception as e:
        return {"error": str(e)}


@ router.get("/pattern/learn")
async def pattern_learn(limit: int = Query(20)):
    """Get sample grammar patterns from grammar_patterns table."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT pattern_id, pattern, function, examples, frequency "
            "FROM grammar_patterns ORDER BY frequency DESC LIMIT ?",
            (limit,),
        )
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        cur.close()
        conn.close()
        output = json.dumps([dict(zip(cols, r)) for r in rows], indent=2, ensure_ascii=False)
        return {"output": output}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════
# EXPORT TOOLS
# ═══════════════════════════════════════════

@ router.get("/export/{data_type}")
async def export_data(data_type: str, limit: int = Query(20)):
    """Export data to JSONL format — returns count + sample as formatted JSON string."""
    table_map = {
        "dictionary": "dictionary",
        "bible": "bible_verses",
        "vocabulary": "vocabulary",
        "grammar": "grammar_patterns",
        "phrases": "phrases",
        "exercises": "training_exercises",
    }
    table = table_map.get(data_type)
    if not table:
        return {"error": f"Must be one of: {', '.join(table_map.keys())}"}

    try:
        conn = get_db()
        cur = conn.cursor()

        # Total count
        cur.execute(f"SELECT COUNT(*) FROM [{table}]")
        total = cur.fetchone()[0]

        # Sample rows
        cur.execute(f"SELECT * FROM [{table}] LIMIT ?", (limit,))
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        sample = [dict(zip(cols, r)) for r in rows]

        cur.close()
        conn.close()

        output = json.dumps({"table": table, "total": total, "sample": sample}, indent=2, ensure_ascii=False)
        return {"output": output}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════
# AUDIT TOOLS
# ═══════════════════════════════════════════

@ router.get("/audit/recent")
async def audit_recent(limit: int = Query(50)):
    """Get recent audit log entries from data_audit_log table."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, table_name, row_id, field, old_value, new_value, changed_at, reason
            FROM data_audit_log
            ORDER BY changed_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        cur.close()
        conn.close()
        return {"results": [dict(zip(cols, r)) for r in rows]}
    except Exception as e:
        return {"error": str(e)}

# ═══════════════════════════════════════════
# AI MODELS ENDPOINT
# ═══════════════════════════════════════════


# ═══════════════════════════════════════════
# AI MODELS ENDPOINT (Dynamic)
# ═══════════════════════════════════════════

@router.get("/ai/models")
async def get_ai_models():
    """Get available AI models from zolai-core configuration."""
    try:
        # Try to get Gemini models from environment or config
        gemini_models = [
            "gemini-3-flash",
            "gemini-3-pro-plus",
            "gemini-3-pro",
            "gemini-3-flash-thinking",
            "gemini-3-flash-plus",
            "gemini-3-flash-thinking-plus",
            "gemini-3-pro-advanced",
            "gemini-3-flash-advanced",
            "gemini-3-flash-thinking-advanced",
        ]

        # OpenRouter free models
        openrouter_models = [
            "mimo-v2.5-free",
            "nemotron-3-ultra-free",
            "hy3-free",
            "muse-spark-1.2-contributor-free",
        ]

        # Local Zolai model
        local_models = [
            {"name": "zolai-local", "provider": "zolai", "type": "local", "endpoint": "/chat/zolai"},
        ]

        models = []
        for m in gemini_models:
            models.append({"name": m, "provider": "gemini", "type": "cloud"})
        for m in openrouter_models:
            models.append({"name": m, "provider": "openrouter", "type": "cloud"})
        for m in local_models:
            models.append(m)

        # Try to load from config if available
        try:
            from ..config import config
            # Check if config has model overrides
            if hasattr(config, 'ai_models'):
                pass
        except:
            pass

        return {"models": models, "default": "zolai-local"}
    except Exception as e:
        return {"models": [{"name": "zolai-local", "provider": "zolai", "type": "local", "endpoint": "/chat/zolai"}], "default": "zolai-local", "error": str(e)}


# ═══════════════════════════════════════════
# OLLAMA MODELS ENDPOINT (Dynamic)
# ═══════════════════════════════════════════

@router.get("/ollama/models")
async def get_ollama_models():
    """Get available Ollama models from local Ollama server."""
    try:
        import httpx
        # Default Ollama URL from config
        ollama_url = getattr(config, 'ollama_url', 'http://localhost:11434')

        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{ollama_url}/api/tags")
            if response.status_code == 200:
                data = response.json()
                models = []
                for m in data.get("models", []):
                    models.append({
                        "name": m.get("name", ""),
                        "provider": "ollama",
                        "type": "local",
                        "size": m.get("size", 0),
                        "digest": m.get("digest", ""),
                        "endpoint": "/chat/zolai"
                    })
                return {"models": models, "default": "zolai-local"}
            else:
                return {"models": [], "default": "zolai-local", "error": f"Ollama returned {response.status_code}"}
    except Exception as e:
        return {"models": [], "default": "zolai-local", "error": str(e)}
