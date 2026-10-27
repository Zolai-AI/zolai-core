"""``GET /api/v1/lexicon/search`` ranks by graded relevance — core#5.

Before the fix the route ordered by ``ORDER BY id ASC``: insertion order, not
relevance, and the items carried no ``score`` at all.  The live symptom was
``q=pasian`` answering with ``abba`` first (its ``translations`` field merely
*contains* "Pasian") while the exact headword sat further down the page, and a
caller had no score to rank on even if it wanted to.

The rule now is :func:`zolai.api.lexicon_router._score_sql`'s tiers — exact on
the identity column ``1.0`` > exact on a secondary column ``0.9`` > prefix
``0.8``/``0.7`` > contains ``0.5`` — with ties broken by ``(id, table)``, so
the documented merge order survives for equally-good matches.

Everything runs on a throwaway DB in ``tmp_path``; no canonical DB, no network.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zolai.api import auth
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.config import config

#: Mirrors the live defect: a *low-id* row whose secondary field merely
#: contains the query, versus the exact headword at a higher id.  Insertion
#: order answers ``abba``; relevance must answer ``pasian``.
_RANK_SQL = """
CREATE TABLE dictionary (
    id INTEGER PRIMARY KEY, zolai TEXT NOT NULL, english TEXT, pos TEXT, source TEXT
);
INSERT INTO dictionary VALUES
    (1, 'abba', 'father', 'n', 'test'),
    (2, 'gam', 'earth, land', 'n', 'test'),
    (7, 'pasian', 'God', 'n', 'test'),
    (8, 'pasian-a', 'God of', 'n', 'test'),
    (9, 'tapa', 'son, life', 'n', 'test');
CREATE TABLE dictionary_en_zo (
    id INTEGER PRIMARY KEY, headword TEXT NOT NULL, translations TEXT, pos TEXT, source TEXT
);
INSERT INTO dictionary_en_zo VALUES
    (1, 'abba', 'Pasian muhna leh mawhna', 'n', 'test'),
    (2, 'god', 'pasian', 'n', 'test'),
    (3, 'father', 'abba', 'n', 'test');
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
def rank_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "zolai.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_RANK_SQL)
    conn.commit()
    conn.close()
    monkeypatch.setattr(config.paths, "db", db_path)
    return db_path


@pytest.fixture()
def client(rank_db: Path) -> TestClient:
    return TestClient(create_app())


def _search(client: TestClient, **params) -> list[dict]:
    resp = client.get("/api/v1/lexicon/search", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


class TestLexiconSearchRanking:
    def test_exact_match_is_first_for_q_pasian(self, client: TestClient) -> None:
        """core#5: ``abba`` (secondary contains) must not beat the exact headword."""
        items = _search(client, q="pasian")
        assert items, "the seed guarantees matches"
        top = items[0]
        assert top["table"] == "dictionary"
        assert top["zolai"] == "pasian"
        assert top["score"] == 1.0

    def test_scores_are_graded_not_all_one(self, client: TestClient) -> None:
        items = _search(client, q="pasian")
        scores = [item["score"] for item in items]
        assert len(set(scores)) > 1, f"flat scores: {scores}"
        assert max(scores) == 1.0
        assert min(scores) < 1.0

    def test_scores_are_strictly_descending(self, client: TestClient) -> None:
        for query in ("pasian", "tapa", "god", "a"):
            scores = [item["score"] for item in _search(client, q=query)]
            assert scores == sorted(scores, reverse=True), (query, scores)

    def test_every_hit_carries_a_score(self, client: TestClient) -> None:
        for item in _search(client, q="a"):
            assert isinstance(item["score"], float)
            assert 0.0 < item["score"] <= 1.0

    def test_exact_beats_prefix_beats_contains(self, client: TestClient) -> None:
        """One query, one tier per row — no two match qualities collapse together."""
        items = _search(client, q="pasian")
        by_word = {
            (item["table"], item.get("zolai") or item.get("headword")): item["score"]
            for item in items
        }
        assert by_word[("dictionary", "pasian")] == 1.0        # exact primary
        assert by_word[("dictionary", "pasian-a")] == 0.8      # prefix primary
        assert by_word[("dictionary_en_zo", "god")] == 0.9      # exact secondary (translations)
        # ``abba``'s translations merely *start with* "Pasian …" (the live defect row).
        assert by_word[("dictionary_en_zo", "abba")] == 0.7     # prefix secondary
        # The floor: a row that only contains the query.
        contains = [i["score"] for i in _search(client, q="a") if i.get("zolai") == "gam"]
        assert contains == [0.5]

    def test_ties_keep_the_documented_id_table_order(self, client: TestClient) -> None:
        """Relevance ordering must not shuffle equally-good matches."""
        for query in ("a", "pasian", "god"):
            items = _search(client, q=query)
            per_score: dict[float, list[tuple[int, str]]] = {}
            for item in items:
                per_score.setdefault(item["score"], []).append((item["id"], item["table"]))
            for score, pairs in per_score.items():
                assert pairs == sorted(pairs), (query, score, pairs)

    def test_pagination_walks_relevance_order_without_duplicates(
        self, client: TestClient
    ) -> None:
        seen: list[tuple[int, str]] = []
        cursor: str | None = None
        for _ in range(10):
            params: dict = {"q": "pasian", "limit": 1}
            if cursor is not None:
                params["cursor"] = cursor
            resp = client.get("/api/v1/lexicon/search", params=params)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            seen.extend((i["id"], i["table"]) for i in data["items"])
            if not data["has_more"]:
                assert data["next_cursor"] is None
                break
            cursor = data["next_cursor"]
        assert len(seen) == len(set(seen)), "cursor walk repeated a row"
        # The exact headword first, the containing-but-not-exact row last.
        assert seen[0] == (7, "dictionary")
        assert seen[-1] == (1, "dictionary_en_zo")

    def test_cursor_score_must_be_resolvable(self, client: TestClient) -> None:
        """A well-formed cursor pointing at no row is 400, not a silent resume."""
        resp = client.get(
            "/api/v1/lexicon/search", params={"q": "pasian", "cursor": "999:dictionary"}
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_cursor"

    def test_malformed_cursors_are_still_400(self, client: TestClient) -> None:
        for bad in ("5", "abc", "1:", "1:nope"):
            resp = client.get("/api/v1/lexicon/search", params={"q": "a", "cursor": bad})
            assert resp.status_code == 400, bad
            assert resp.json()["detail"]["error"] == "invalid_cursor"

    def test_no_match_is_still_an_empty_page(self, client: TestClient) -> None:
        resp = client.get("/api/v1/lexicon/search", params={"q": "zzzz"})
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "next_cursor": None, "has_more": False}
