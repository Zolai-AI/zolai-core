"""Extended repositories for Phase 6+ (KG, Vector search, etc.)."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .base import BaseRepository

log = logging.getLogger(__name__)


class KGRepository(BaseRepository):
    """Repository for Knowledge Graph (kg_nodes + kg_edges)."""

    # Valid relation types per §21
    VALID_RELATIONS = {
        "has_form",
        "has_pos",
        "derived_from",
        "contains_morpheme",
        "occurs_with",
        "occurs_in",
        "participates_in",
        "similar_to",
        "variant_of",
        "attested_by",
    }

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "kg_nodes", id_column="id")
        self._edges_table = self._engine.dialect.identifier_preparer.quote("kg_edges")

    # --- Node operations ---------------------------------------------------
    def upsert_node(
        self,
        node_type: str,
        label: str,
        properties: dict[str, Any],
        source: str | None = None,
        source_id: str | None = None,
        confidence: float = 0.0,
        user: str = "system",
    ) -> int:
        """Upsert a node (idempotent on node_type+label+source+source_id)."""
        import json
        
        props_json = json.dumps(properties, ensure_ascii=False)
        now = self._get_timestamp()
        
        with self._engine.begin() as conn:
            # Check existing
            existing = conn.execute(
                text("""
                    SELECT id FROM kg_nodes 
                    WHERE node_type = :nt AND label = :lbl 
                    AND COALESCE(source, '') = COALESCE(:src, '') 
                    AND COALESCE(source_id, '') = COALESCE(:sid, '')
                """),
                {"nt": node_type, "lbl": label, "src": source or "", "sid": source_id or ""},
            ).first()
            
            if existing:
                node_id = existing[0]
                conn.execute(
                    text("""
                        UPDATE kg_nodes SET
                            properties = :props,
                            confidence = :conf,
                            updated_at = :now,
                            version = version + 1
                        WHERE id = :id
                    """),
                    {"props": props_json, "conf": confidence, "now": now, "id": node_id},
                )
                self._log_audit(conn, node_id, "update", None, json.dumps({"properties": properties}), user)
                return node_id
            else:
                result = conn.execute(
                    text("""
                        INSERT INTO kg_nodes (node_type, label, properties, source, source_id, confidence, created_at, updated_at)
                        VALUES (:nt, :lbl, :props, :src, :sid, :conf, :now, :now)
                    """),
                    {"nt": node_type, "lbl": label, "props": props_json, "src": source, "sid": source_id, "conf": confidence, "now": now},
                )
                node_id = result.lastrowid
                self._log_audit(conn, node_id, "create", None, json.dumps({"node_type": node_type, "label": label}), user)
                return node_id

    def get_node(self, node_id: int) -> dict[str, Any] | None:
        """Get node by ID."""
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM kg_nodes WHERE id = :id"),
                {"id": node_id},
            ).first()
        return self._row_to_dict(row) if row else None

    def find_nodes(
        self,
        node_type: str | None = None,
        label: str | None = None,
        source: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Find nodes by filters."""
        conditions = []
        params = {"limit": limit}
        if node_type:
            conditions.append("node_type = :node_type")
            params["node_type"] = node_type
        if label:
            conditions.append("label LIKE :label")
            params["label"] = f"%{label}%"
        if source:
            conditions.append("source = :source")
            params["source"] = source
        
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(f"SELECT * FROM kg_nodes {where} ORDER BY id LIMIT :limit"),
                params,
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # --- Edge operations ---------------------------------------------------
    def upsert_edge(
        self,
        source_id: int,
        target_id: int,
        relation: str,
        properties: dict[str, Any] | None = None,
        confidence: float = 0.0,
        source: str | None = None,
        user: str = "system",
    ) -> int:
        """Upsert an edge (idempotent on source_id+target_id+relation)."""
        import json
        
        if relation not in self.VALID_RELATIONS:
            raise ValueError(f"Invalid relation: {relation}. Valid: {self.VALID_RELATIONS}")
        
        props_json = json.dumps(properties or {}, ensure_ascii=False)
        now = self._get_timestamp()
        
        with self._engine.begin() as conn:
            existing = conn.execute(
                text("""
                    SELECT id FROM kg_edges 
                    WHERE source_id = :sid AND target_id = :tid AND relation = :rel
                """),
                {"sid": source_id, "tid": target_id, "rel": relation},
            ).first()
            
            if existing:
                edge_id = existing[0]
                conn.execute(
                    text("""
                        UPDATE kg_edges SET
                            properties = :props,
                            confidence = :conf,
                            updated_at = :now,
                            version = version + 1
                        WHERE id = :id
                    """),
                    {"props": json.dumps(properties or {}), "conf": 0.0, "now": self._get_timestamp(), "id": existing[0]},
                )
                self._log_audit(conn, edge_id, "update", None, json.dumps({"relation": relation}), "system")
                return edge_id
            else:
                result = conn.execute(
                    text("""
                        INSERT INTO kg_edges (source_id, target_id, relation, properties, confidence, source, created_at, updated_at)
                        VALUES (:sid, :tid, :rel, :props, :conf, :src, :now, :now)
                    """),
                    {
                        "sid": source_id, "tid": target_id, "rel": relation,
                        "props": json.dumps(properties or {}), "conf": 0.0,
                        "src": source or "auto", "now": self._get_timestamp(),
                    },
                )
                edge_id = result.lastrowid
                self._log_audit(conn, edge_id, "create", None, json.dumps({"source_id": source_id, "target_id": target_id, "relation": relation}), "system")
                return edge_id

    def get_edge(self, edge_id: int) -> dict[str, Any] | None:
        """Get edge by ID."""
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM kg_edges WHERE id = :id"),
                {"id": edge_id},
            ).first()
        if row:
            d = self._row_to_dict(row)
            d["properties"] = json.loads(d["properties"]) if d["properties"] else {}
            return d
        return None

    # --- Traversal ---------------------------------------------------------
    def traverse(
        self,
        start_node_id: int,
        relations: list[str] | None = None,
        direction: str = "out",  # "out" | "in" | "both"
        max_depth: int = 3,
        max_nodes: int = 1000,
    ) -> list[dict[str, Any]]:
        """Traverse graph from start node (BFS)."""
        if relations:
            rel_filter = "AND relation IN (" + ",".join("?" * len(relations)) + ")"
            rel_params = list(relations)
        else:
            rel_filter = ""
            rel_params = []
        
        if direction == "out":
            edge_sql = "source_id = :current AND target_id = next_id"
        elif direction == "in":
            edge_sql = "target_id = :current AND source_id = next_id"
        else:  # both
            edge_sql = "(source_id = :current AND target_id = next_id) OR (target_id = :current AND source_id = next_id)"
        
        visited = set()
        queue = [(start_node_id, 0, [])]  # (node_id, depth, path)
        results = []
        
        while queue and len(results) < max_nodes:
            current_id, depth, path = queue.pop(0)
            if current_id in visited or depth > max_depth:
                continue
            visited.add(current_id)
            
            # Get neighbors
            with self._engine.connect() as conn:
                if direction == "out":
                    rows = conn.execute(
                        text(f"SELECT target_id as next_id, relation, properties, confidence FROM kg_edges WHERE source_id = :current"),
                        {"current": current_id},
                    ).fetchall()
                elif direction == "in":
                    rows = conn.execute(
                        text(f"SELECT source_id as next_id, relation, properties, confidence FROM kg_edges WHERE target_id = :current"),
                        {"current": current_id},
                    ).fetchall()
                else:
                    rows = conn.execute(
                        text(f"""
                            SELECT target_id as next_id, relation, properties, confidence FROM kg_edges WHERE source_id = :current
                            UNION ALL
                            SELECT source_id as next_id, relation, properties, confidence FROM kg_edges WHERE target_id = :current
                        """),
                        {"current": current_id},
                    ).fetchall()
            
            for row in rows:
                next_id = row[0]
                if next_id not in visited:
                    new_path = path + [{"node_id": current_id, "relation": row[1], "edge_props": row[2], "edge_conf": row[3]}]
                    queue.append((next_id, depth + 1, new_path))
        
        return results

    def get_neighbors(self, node_id: int, relation: str | None = None) -> list[dict[str, Any]]:
        """Get direct neighbors of a node."""
        with self._engine.connect() as conn:
            if relation:
                rows = conn.execute(
                    text("""
                        SELECT e.*, n.label as target_label, n.node_type as target_type
                        FROM kg_edges e
                        JOIN kg_nodes n ON n.id = e.target_id
                        WHERE e.source_id = :id AND e.relation = :rel
                    """),
                    {"id": node_id, "rel": relation},
                ).fetchall()
            else:
                rows = conn.execute(
                    text("""
                        SELECT e.*, n.label as target_label, n.node_type as target_type
                        FROM kg_edges e
                        JOIN kg_nodes n ON n.id = e.target_id
                        WHERE e.source_id = :id
                    """),
                    {"id": node_id},
                ).fetchall()
        return [self._row_to_dict(r) for r in rows]


def get_extended_repositories(engine):
    """Get extended repositories including KG."""
    from .knowledge import ClaimRepository, HypothesisRepository, KnowledgeVersionRepository
    return {
        "claims": ClaimRepository(engine),
        "hypotheses": HypothesisRepository(engine),
        "knowledge_versions": KnowledgeVersionRepository(engine),
        "kg": KGRepository(engine),
    }


class CorrectionRepository(BaseRepository):
    def __init__(self, engine):
        super().__init__(engine, "corrections")

class GrammarInstructionRepository(BaseRepository):
    def __init__(self, engine):
        super().__init__(engine, "grammar_instructions")

class KnowledgeVectorRepository(BaseRepository):
    def __init__(self, engine):
        super().__init__(engine, "knowledge_vectors")

class NgramRepository(BaseRepository):
    def __init__(self, engine):
        super().__init__(engine, "ngram")

class ParticleRepository(BaseRepository):
    def __init__(self, engine):
        super().__init__(engine, "particle_database")

class SimbuRepository(BaseRepository):
    def __init__(self, engine):
        super().__init__(engine, "simbu")

class TrainingValidationRepository(BaseRepository):
    def __init__(self, engine):
        super().__init__(engine, "training_validation")

class VerbRepository(BaseRepository):
    def __init__(self, engine):
        super().__init__(engine, "verb_database")
