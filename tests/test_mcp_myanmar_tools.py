"""The three Myanmar/Burmese tools — public, DB-first, honest on a miss.

The names are frozen to the ones the retired Cloudflare Worker published
(``search_myanmar_dictionary``, ``translate_zo_to_myanmar``,
``translate_myanmar_to_zo``) so a client configuration written against that
surface keeps working.  Each wraps one in-process
:class:`~zolai.data.database.DatabaseManager` call — the same the legacy
``/dictionary/*`` routes make — and each reports a **miss honestly**
(``found: false`` with a note, or an empty result list) rather than inventing a
translation.  Nothing here touches the network.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

import zolai.data.database as database_mod
from zolai.agent.tools import executor as exec_mod
from zolai.agent.tools.registry import SPECS, allow_list
from zolai.agent.tools.types import ToolCall, ToolErrorCode
from zolai.data.database import DatabaseManager

_BURMESE_GOD = "\u1018\u102f\u101b\u102c\u1038\u101e\u1001\u103b\u1004\u103a"  # ဘုရားသခင်
_BURMESE_EARTH = "\u1019\u103c\u1031"  # မြေ
_BURMESE_BEGINNING = "\u1021\u1005\u1026\u1038\u1010\u103d\u1004\u103a"  # အစဦးတွင်

_MY_SEED = f"""
CREATE TABLE dictionary (
    id INTEGER PRIMARY KEY,
    zolai TEXT NOT NULL,
    english TEXT,
    myanmar TEXT
);
CREATE TABLE bible_verses (
    id INTEGER PRIMARY KEY,
    ref TEXT NOT NULL,
    zo_tdb77 TEXT,
    en_kjv TEXT,
    myanmar TEXT
);
INSERT INTO dictionary VALUES (1, 'pasian', 'God', '{_BURMESE_GOD}');
INSERT INTO dictionary VALUES (2, 'gam', 'earth, land', '{_BURMESE_EARTH}');
INSERT INTO dictionary VALUES (3, 'tui', 'water', NULL);
INSERT INTO bible_verses VALUES (
    1, 'GEN 1:1',
    'Pasian in vantung leh leitung a piangsak hi.',
    'In the beginning God created the heaven and the earth.',
    '{_BURMESE_BEGINNING}'
);
"""


@pytest.fixture()
def myanmar_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    """Throwaway store carrying the ``myanmar`` columns the lookups reflect."""
    db_path = tmp_path / "zolai.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_MY_SEED)
    conn.commit()
    conn.close()
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    mgr.init_db()
    monkeypatch.setattr(database_mod, "get_manager", lambda *a, **k: mgr)
    yield mgr
    mgr.dispose()


def _call(name: str, inp: dict | None = None, allow=None):
    return exec_mod.execute_tool(
        ToolCall(name=name, input=inp or {}), allow if allow is not None else allow_list("public")
    )


class TestRegistry:
    def test_the_three_names_are_public_reads(self) -> None:
        for name in (
            "search_myanmar_dictionary",
            "translate_zo_to_myanmar",
            "translate_myanmar_to_zo",
        ):
            assert name in SPECS
            assert name in allow_list("public")
            spec = SPECS[name]
            assert spec.admin_only is False
            assert spec.writes is False
            assert spec.requires_network is False

    def test_titles_and_argument_shapes(self) -> None:
        search = SPECS["search_myanmar_dictionary"]
        assert search.title == "Search the Myanmar dictionary"
        assert search.parameters["required"] == ["query"]
        assert set(search.parameters["properties"]) == {"query", "limit"}
        for name in ("translate_zo_to_myanmar", "translate_myanmar_to_zo"):
            assert SPECS[name].parameters["properties"] == {"word": {"type": "string", "minLength": 1}}


class TestSearchMyanmarDictionary:
    def test_finds_a_dictionary_row(self, myanmar_db) -> None:
        result = _call("search_myanmar_dictionary", {"query": _BURMESE_EARTH})
        assert result.ok is True
        assert result.data["count"] >= 1
        assert any(
            row["type"] == "dictionary" and row["zolai"] == "gam" for row in result.data["results"]
        )

    def test_finds_a_bible_verse_row(self, myanmar_db) -> None:
        result = _call("search_myanmar_dictionary", {"query": _BURMESE_BEGINNING})
        assert result.ok is True
        assert any(row["type"] == "bible" and row["ref"] == "GEN 1:1" for row in result.data["results"])

    def test_a_miss_is_an_empty_list_not_a_guess(self, myanmar_db) -> None:
        result = _call("search_myanmar_dictionary", {"query": "\u1019\u102c\u101b\u103e\u102c"})
        assert result.ok is True
        assert result.data["results"] == [] and result.data["count"] == 0

    def test_missing_query_is_an_honest_error(self, myanmar_db) -> None:
        result = _call("search_myanmar_dictionary", {})
        assert result.ok is False
        assert "query is required" in (result.error or "")

    def test_a_db_failure_is_surfaced_not_swallowed(self, myanmar_db, monkeypatch) -> None:
        monkeypatch.setattr(
            myanmar_db, "lookup_myanmar", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        result = _call("search_myanmar_dictionary", {"query": "x"})
        assert result.ok is False
        # Surfaced, not swallowed — and said in words a user can act on.  The
        # raw ``RuntimeError`` detail belongs in the server log, never on the
        # wire: this is the contract ``zolai.agent.tools.errors`` enforces.
        assert result.error == (
            "The Myanmar dictionary is unavailable right now. Please try again."
        )
        assert result.error_code is ToolErrorCode.UNAVAILABLE
        assert "boom" not in result.error
        assert "RuntimeError" not in result.error


class TestTranslateZoToMyanmar:
    def test_found_translation(self, myanmar_db) -> None:
        result = _call("translate_zo_to_myanmar", {"word": "pasian"})
        assert result.ok is True
        assert result.data["found"] is True
        assert result.data["myanmar"] == _BURMESE_GOD

    def test_word_absent_is_found_false_with_a_note(self, myanmar_db) -> None:
        result = _call("translate_zo_to_myanmar", {"word": "khanggui"})
        assert result.ok is True
        assert result.data == {
            "word": "khanggui",
            "found": False,
            "note": "No Myanmar translation for 'khanggui' in the dictionary.",
        }

    def test_word_present_without_myanmar_is_found_false(self, myanmar_db) -> None:
        result = _call("translate_zo_to_myanmar", {"word": "tui"})
        assert result.ok is True
        assert result.data["found"] is False

    def test_missing_word_is_an_honest_error(self, myanmar_db) -> None:
        result = _call("translate_zo_to_myanmar", {})
        assert result.ok is False
        assert "word is required" in (result.error or "")


class TestTranslateMyanmarToZo:
    def test_found_translation(self, myanmar_db) -> None:
        result = _call("translate_myanmar_to_zo", {"word": _BURMESE_GOD})
        assert result.ok is True
        assert result.data["found"] is True
        assert result.data["zolai"] == "pasian"

    def test_a_miss_is_found_false_with_a_note(self, myanmar_db) -> None:
        result = _call("translate_myanmar_to_zo", {"word": "\u1019\u102c\u101b\u103e\u102c"})
        assert result.ok is True
        assert result.data["found"] is False
        assert result.data["word"] == "\u1019\u102c\u101b\u103e\u102c"
        assert "No Zolai entry" in result.data["note"]

    def test_missing_word_is_an_honest_error(self, myanmar_db) -> None:
        result = _call("translate_myanmar_to_zo", {})
        assert result.ok is False
        assert "word is required" in (result.error or "")


class TestNotAllowed:
    def test_an_empty_allow_list_refuses_the_myanmar_tools(self, myanmar_db) -> None:
        for name in (
            "search_myanmar_dictionary",
            "translate_zo_to_myanmar",
            "translate_myanmar_to_zo",
        ):
            result = _call(name, {"query": "x", "word": "x"}, allow=frozenset())
            assert result.ok is False
            assert "not allowed" in (result.error or "")
