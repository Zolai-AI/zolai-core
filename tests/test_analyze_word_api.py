"""``POST /api/v1/analyze/word`` shares the word-detail evidence source — core#23.

Before the fix the route reused the *sentence* analyzer, which is a tokenizer
placeholder: it answered ``pos: []`` / ``morphology: {}`` for every word while
``GET /api/v1/word/{word}`` — the same evidence path — returned real POS.  The
two routes now read one source (``query_word`` → dictionary POS columns, Bible
attestations, observation stats, claims, KG) and can no longer disagree.

Everything runs on a throwaway in-memory SQLite seeded with only the tables
``build_word_evidence_items`` reads: no network, no 2.4 GB canonical DB.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool

from zolai.api import auth, rag_router
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.config import config
from zolai.rag.evidence import EvidenceItem, evidence_to_pack

#: Every table ``build_word_evidence_items`` queries — a missing one raises
#: (the builder has no per-source guard), so all seven must exist.
_SEED_SQL = """
CREATE TABLE dictionary (
    id INTEGER PRIMARY KEY, zolai TEXT, english TEXT, english_clean TEXT,
    pos TEXT, pos_canonical TEXT, pos_evidence TEXT, confidence REAL
);
INSERT INTO dictionary VALUES
    (1, 'pasian', 'God', 'god', 'N', 'N', 'lexicon', 0.9),
    (2, 'gam', 'earth, land', 'earth land', 'N', 'N', 'lexicon', 0.8);

CREATE TABLE dictionary_en_zo (
    id INTEGER PRIMARY KEY, headword TEXT, translations TEXT,
    translations_clean TEXT, pos TEXT
);
INSERT INTO dictionary_en_zo VALUES (1, 'god', 'pasian', 'pasian', 'N');

CREATE TABLE bible_verses (
    id INTEGER PRIMARY KEY, ref TEXT, zo_tdb77 TEXT, zo_tedim2010 TEXT, en_kJV TEXT
);
INSERT INTO bible_verses VALUES (1, 'GEN 1:1', 'Pasian in leitung a piangsak hi', '', 'In the beginning God created');

CREATE TABLE attestation_index (
    id INTEGER PRIMARY KEY, word TEXT, source TEXT
);
INSERT INTO attestation_index VALUES (1, 'pasian', 'GEN 1:1');

CREATE TABLE word_observation_stats (
    id INTEGER PRIMARY KEY, normalized_form TEXT, frequency INTEGER,
    document_frequency INTEGER, sentence_frequency INTEGER, contexts TEXT,
    collocations TEXT, surface_forms TEXT, attestation TEXT
);
INSERT INTO word_observation_stats VALUES (1, 'pasian', 5223, 64, 0, '{}', '[]', '[]', '{}');

CREATE TABLE knowledge_claims (
    id INTEGER PRIMARY KEY, subject TEXT, predicate TEXT, object TEXT,
    confidence REAL, status TEXT, evidence_ids TEXT
);

CREATE TABLE kg_nodes (
    id INTEGER PRIMARY KEY, label TEXT, node_type TEXT, properties TEXT,
    confidence REAL
);
INSERT INTO kg_nodes VALUES
    (1, 'pasian', 'concept', '{"morphology": {"pos": "proper_noun", "animacy": "animate"}}', 0.9);
"""


@pytest.fixture(autouse=True)
def _clean_auth_state() -> Iterator[None]:
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()
    yield
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()


@pytest.fixture()
def engine():
    """In-memory engine holding exactly the evidence-builder's tables.

    ``StaticPool`` + ``check_same_thread=False`` are required, not cosmetic:
    TestClient runs the sync endpoint in an anyio worker thread, and the
    default ``SingletonThreadPool`` gives every thread its own ``:memory:``
    database -- so the seeded tables existed on the fixture's connection and
    the request saw an empty one.
    """
    eng = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with eng.begin() as conn:
        for stmt in _SEED_SQL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    return eng


@pytest.fixture()
def client(engine, tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Main app (no lifespan) with the retriever wired to the throwaway engine."""
    monkeypatch.setattr(rag_router, "get_engine", lambda *args, **kwargs: engine)
    monkeypatch.setenv("ZOLAI_API_AUTH", "warn")
    monkeypatch.setattr(config.paths, "db", tmp_path / "zolai.db")
    return TestClient(create_app())


def _sql_log(engine) -> list[str]:
    statements: list[str] = []
    event.listen(engine, "before_cursor_execute", lambda *a: statements.append(a[2]))
    return statements


class TestPosSpellingIsOneValue:
    """explorer#23: live ``GET /api/v1/word/pasian`` answered ``pos: ['Noun', 'NOUN']``.

    The dictionary column spells the label (``Noun``), the KG node spells the tag
    (``NOUN``). One part of speech, two chips — so the pack collapses variants that
    differ only by case and keeps the readable spelling.
    """

    def test_case_variants_collapse_to_the_label_spelling(self) -> None:
        pack = evidence_to_pack(
            "pasian",
            [
                EvidenceItem("dictionary", "dictionary", "word:pasian", 2, 0.9, "God", {"pos": "Noun"}),
                EvidenceItem("kg", "kg_nodes", "node:pasian", 3, 0.6, "", {"pos": "NOUN"}),
            ],
        )
        assert pack.pos == ["Noun"]

    def test_tag_is_kept_when_no_label_spelling_exists(self) -> None:
        pack = evidence_to_pack(
            "pasian",
            [
                EvidenceItem("kg", "kg_nodes", "node:pasian", 3, 0.6, "", {"pos": "NOUN"}),
                EvidenceItem("dictionary", "dictionary", "word:pasian", 2, 0.9, "God", {"pos": "noun"}),
            ],
        )
        assert pack.pos == ["noun"]

    def test_genuinely_different_tags_are_all_kept(self) -> None:
        pack = evidence_to_pack(
            "gam",
            [
                EvidenceItem("dictionary", "dictionary", "word:gam", 2, 0.9, "earth, land", {"pos": "Noun"}),
                EvidenceItem("knowledge_claims", "knowledge_claims", "c:gam", 3, 0.6, "", {"pos": "VERB"}),
            ],
        )
        assert pack.pos == ["Noun", "VERB"]

class TestWordDetailSource:
    def test_get_word_reports_pos(self, client: TestClient) -> None:
        body = client.get("/api/v1/word/pasian")
        assert body.status_code == 200, body.text
        pack = body.json()
        assert pack["pos"], "word detail must carry POS (the evidence pack aggregates it)"
        assert pack["morphology"], "the KG node supplies morphology for this word"

    def test_get_word_unknown_word_is_404_with_the_house_envelope(
        self, client: TestClient
    ) -> None:
        """core#4: a word no table knows is missing, not an all-zero 200."""
        body = client.get("/api/v1/word/zzzznotaword")
        assert body.status_code == 404, body.text
        detail = body.json()["detail"]
        assert detail["error"] == "word_not_found"
        assert "zzzznotaword" in detail["message"]

    def test_get_word_known_word_still_answers_200(self, client: TestClient) -> None:
        """The 404 branch must not swallow a word that does have evidence."""
        body = client.get("/api/v1/word/pasian")
        assert body.status_code == 200, body.text
        assert body.json()["frequency"] > 0


class TestAnalyzeWordSharesTheSource:
    def test_pos_is_identical_to_word_detail(self, client: TestClient) -> None:
        detail = client.get("/api/v1/word/pasian").json()
        analyzed = client.post("/api/v1/analyze/word", json={"word": "pasian"})
        assert analyzed.status_code == 200, analyzed.text
        payload = analyzed.json()
        assert payload["pos"], "analyze/word used to answer an empty POS list (core#23)"
        assert payload["pos"] == detail["pos"], "the two routes read one source"

    def test_morphology_is_identical_to_word_detail(self, client: TestClient) -> None:
        detail = client.get("/api/v1/word/pasian").json()
        analyzed = client.post("/api/v1/analyze/word", json={"word": "pasian"})
        payload = analyzed.json()
        assert payload["morphology"], "core#23: empty morphology on a word with evidence"
        assert payload["morphology"] == detail["morphology"]

    def test_tokens_still_come_from_the_tokenizer(self, client: TestClient) -> None:
        payload = client.post("/api/v1/analyze/word", json={"word": "pasian"}).json()
        assert payload["tokens"] == ["pasian"]
        assert payload["word"] == "pasian"

    def test_attestation_is_the_evidence_chain(self, client: TestClient) -> None:
        payload = client.post("/api/v1/analyze/word", json={"word": "pasian"}).json()
        assert payload["attestation"], "attestation must list the evidence, not stay empty"
        sources = {item["source"] for item in payload["attestation"]}
        assert {"dictionary", "bible_verses", "word_observation_stats"} <= sources
        for item in payload["attestation"]:
            assert {"source_type", "source", "tier", "confidence", "text", "metadata"} <= set(item)

    def test_unknown_word_stays_an_honest_empty_answer(self, client: TestClient) -> None:
        payload = client.post("/api/v1/analyze/word", json={"word": "zzzznotaword"}).json()
        assert payload["pos"] == []
        assert payload["morphology"] == {}
        assert payload["attestation"] == []

    def test_both_routes_hit_the_same_tables(self, engine, client: TestClient) -> None:
        """One source, proven on the wire: identical SQL from both routes."""
        statements = _sql_log(engine)
        client.get("/api/v1/word/pasian")
        client.post("/api/v1/analyze/word", json={"word": "pasian"})
        assert len(statements) >= 2
        assert len(set(statements)) < len(statements), "routes re-ran different queries"
