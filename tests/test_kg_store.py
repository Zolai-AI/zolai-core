"""The KG extraction scripts write the one canonical database.

`scripts/kg/store.py` replaced a scratch ``data/kg.db`` whose ``kg_node`` /
``kg_edge`` schema was never created anywhere — every extraction run either
failed on the missing table or silently produced a second store beside the real
one.  These tests pin the contract: one file (``config.paths.db``), the
canonical ``kg_nodes`` / ``kg_edges`` tables, idempotent upserts.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from zolai.config import config
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_kg_tables


def _load_store():
    path = Path(__file__).resolve().parents[1] / "scripts" / "kg" / "store.py"
    spec = importlib.util.spec_from_file_location("kg_store", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def store(tmp_path, monkeypatch):
    module = _load_store()
    db_path = tmp_path / "zolai.db"
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    create_kg_tables(mgr.engine)
    monkeypatch.setattr(config.paths, "db", db_path)
    yield module, db_path, tmp_path
    mgr.dispose()


def test_connect_opens_the_canonical_file(store) -> None:
    module, db_path, tmp_path = store
    with module.connect() as conn:
        opened = conn.execute("PRAGMA database_list").fetchone()[2]
        assert Path(opened).resolve() == db_path.resolve()
        assert conn.execute("SELECT 1 FROM kg_nodes").fetchall() == []
    assert not (tmp_path / "kg.db").exists()


def test_upsert_node_is_idempotent_on_the_iri(store) -> None:
    module, db_path, _ = store
    with module.connect() as conn:
        first = module.upsert_node(conn, label="Pasian", source_id="zolai:ent:pasian")
        second = module.upsert_node(conn, label="Pathian", source_id="zolai:ent:pasian")
        conn.commit()
        assert first == second
        row = conn.execute("SELECT label, source, COUNT(*) FROM kg_nodes").fetchone()
        assert row == ("Pathian", module.SOURCE, 1)


def test_upsert_edge_is_idempotent_and_resolves_node_ids(store) -> None:
    module, _, _ = store
    with module.connect() as conn:
        gam = module.upsert_node(conn, label="gam", source_id="zolai:ent:gam")
        pasian = module.upsert_node(conn, label="Pasian", source_id="zolai:ent:pasian")
        first = module.upsert_edge(conn, source_row=pasian, target_row=gam, relation="piangsak")
        second = module.upsert_edge(conn, source_row=pasian, target_row=gam, relation="piangsak")
        conn.commit()
        assert first == second
        assert conn.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0] == 1


def test_an_edge_to_a_missing_node_is_refused(store) -> None:
    module, _, _ = store
    with module.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            module.upsert_edge(conn, source_row=999, target_row=998, relation="orphan")
