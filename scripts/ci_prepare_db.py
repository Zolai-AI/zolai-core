#!/usr/bin/env python3
"""Create a schema + tiny seed DB for GitHub Actions (data/ is gitignored)."""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import text


def main() -> None:
    root = Path(os.environ.get("ZOLAI_DATA_ROOT", Path.cwd() / "data")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["ZOLAI_DATA_ROOT"] = str(root)

    from zolai.config import config
    from zolai.data.database import DatabaseManager
    from zolai.data.migrations import create_kg_tables, run_all_migrations

    db = config.paths.zolai_db
    mgr = DatabaseManager(f"sqlite:///{db}")
    # Same bootstrap the app lifespan performs: ORM tables, then the additive
    # migrations.  A create_all-only store is missing the raw-DDL knowledge
    # graph and the phase2d lexicon lifecycle columns, which RAG word evidence
    # and publishing read unconditionally.
    mgr.init_db()
    create_kg_tables(mgr.engine)
    run_all_migrations(mgr)
    eng = mgr.engine

    with eng.begin() as conn:
        # Dictionary ZO→EN
        conn.execute(
            text(
                "INSERT OR IGNORE INTO dictionary (id, zolai, english, english_clean, myanmar, source, pos) "
                "VALUES (1, 'pasian', 'God', 'God', NULL, 'ci-seed', 'n'), "
                "(2, 'tapa', 'son', 'son', NULL, 'ci-seed', 'n'), "
                "(3, 'topa', 'lord', 'lord', NULL, 'ci-seed', 'n'), "
                "(4, 'gam', 'country', 'country', NULL, 'ci-seed', 'n'), "
                "(5, 'tua', 'that', 'that', NULL, 'ci-seed', 'det')"
            )
        )
        # Bible verses (columns used by RAG/tests)
        conn.execute(
            text(
                'INSERT OR IGNORE INTO bible_verses (id, ref, book, chapter, verse, zo_tdb77, zo_tedim2010, "en_kJV", myanmar) '
                "VALUES "
                "(1, 'GEN.1.1', 'GEN', 1, 1, 'A kipatna ah Pasian in vantung leh leitung a piangsak hi.', "
                "'A kipatna ah Pasian in vantung leh leitung a piangsak hi.', "
                "'In the beginning God created the heaven and the earth.', NULL), "
                "(2, 'JHN.3.16', 'JHN', 3, 16, 'Pasian in leitung a itna a thuk mahmah hi.', "
                "'Pasian in leitung a itna a thuk mahmah hi.', "
                "'For God so loved the world.', NULL)"
            )
        )
        # Phrases
        try:
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO phrases (id, zolai, english, source) "
                    "VALUES (1, 'dam tak in', 'hello', 'ci-seed'), "
                    "(2, 'kong pai ding', 'I will go', 'ci-seed')"
                )
            )
        except Exception:
            pass

    # Eval fixtures (DB-first): eval_sets/eval_cases + smoke/eval_v1 rows so
    # `zolai-eval --set db:...` works on the CI DB.
    from zolai.eval.store import ensure_schema, seed_sets

    ensure_schema(db)
    print(f"eval sets: {seed_sets(('smoke', 'eval_v1'), db_path=db)}")

    mgr.dispose()
    print(f"CI DB ready at {db}")


if __name__ == "__main__":
    main()
