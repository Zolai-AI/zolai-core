#!/usr/bin/env python3
"""Create a schema + tiny seed DB for GitHub Actions (data/ is gitignored)."""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, text


def main() -> None:
    root = Path(os.environ.get("ZOLAI_DATA_ROOT", Path.cwd() / "data")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["ZOLAI_DATA_ROOT"] = str(root)

    from zolai.config import config
    from zolai.data.models import Base
    import zolai.data.models  # noqa: F401 — register tables

    db = config.paths.zolai_db
    eng = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(eng)

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

    print(f"CI DB ready at {db}")


if __name__ == "__main__":
    main()
