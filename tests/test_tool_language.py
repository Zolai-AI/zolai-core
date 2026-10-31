"""B1a — the five language tools: reachability, honesty, scope and growth.

Done-when asserted here:

- **the published surface only grew.**  Every pre-B1a tier payload is a strict
  byte-identical *prefix* of the published one, SHA-256 checked against the digests
  captured before this batch — so nothing was removed, renamed or reordered, and
  the anonymous tier did not move at all;
- **each handler reaches the capability it names.**  Sentinel substitution: the
  exact symbol from the spec's backing-service table is replaced with a unique
  value and that value — not "something truthy" — must come back, with a negative
  control proving one tool's sentinel cannot appear in another's payload;
- **the strict ZVS validator is the one wired up**, and it is *not* the API helper
  that skips ``pathian``: ``validate("Pathian in Pathian")`` reports violations
  while ``zolai.api.zvs_checker`` reports that same string as compliant;
- **empty results are honest** — a query miss and an unassembled evidence pack say
  so with a ``state``/``reason`` envelope instead of a bare ``[]`` / all-zeros;
- **no spec offers a caller-supplied credential field**, every language tool is a
  read, and each ``scope`` comes from the frozen vocabulary;
- **the sixth tool of the batch was correctly skipped** — a grammar/SOV structure
  check already exists under two names, and a third must not appear.

Pure: stubs and fakes for the DB-backed services; the segmenter, the strict rule
set and argument validation run for real.  No FastAPI lifespan, no migrations.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from typing import Any

import pytest

from zolai.agent.tools import language as lang
from zolai.agent.tools.executor import HANDLERS, execute_tool
from zolai.agent.tools.learning import LEARNING_READ_TOOLS, LEARNING_WRITE_TOOLS
from zolai.agent.tools.learning_runs import LEARNING_RUN_TOOL_NAMES
from zolai.agent.tools.registry import (
    ANONYMOUS_TOOLS,
    LANGUAGE_SPECS,
    LANGUAGE_TOOL_NAMES,
    SPECS,
    allow_list,
    names_for,
)
from zolai.agent.tools.render import render_mcp, render_openai
from zolai.agent.tools.types import TOOL_NAME_RE, ToolCall, ToolError, ToolErrorCode
from zolai.rag.evidence import EvidencePack

MEMBER = allow_list("member")

# ── Frozen before the B1a batch, re-frozen on deliberate changes ─────────────
#
# Captured with the same canonicalisation the assertions below use
# (``json.dumps(..., sort_keys=True, separators=(",", ":"))`` over the rendered
# ``tools/list`` entries), so a payload that changed at all has a new digest.

_PRE_B1A_NAMES: dict[str, list[str]] = {
    "public": [
        "rag_search",
        "word_evidence",
        "grammar_check",
        "dictionary_lookup",
        "verse_lookup",
        "search_myanmar_dictionary",
        "translate_zo_to_myanmar",
        "translate_myanmar_to_zo",
    ],
    "member": [
        "rag_search",
        "word_evidence",
        "word_related",
        "analyze_text",
        "grammar_check",
        "dictionary_lookup",
        "verse_lookup",
        "search_myanmar_dictionary",
        "translate_zo_to_myanmar",
        "translate_myanmar_to_zo",
        "foundation_analyze_corpus",
        "foundation_analyze_phonology",
        "foundation_analyze_morphology",
        "foundation_translate_enhanced",
        "foundation_get_adaptive_difficulty",
        "foundation_search_ranked",
        "foundation_search_cross_lingual",
        "foundation_search_topical",
        "foundation_validate_structure",
        "foundation_disambiguate_translation",
        "foundation_categorize_error",
    ],
    "admin": [
        "rag_search",
        "word_evidence",
        "word_related",
        "analyze_text",
        "grammar_check",
        "dictionary_lookup",
        "verse_lookup",
        "knowledge_stats",
        "kb_research",
        "review_queue_submit",
        "provider_status",
        "web_search",
        "search_myanmar_dictionary",
        "translate_zo_to_myanmar",
        "translate_myanmar_to_zo",
        "foundation_analyze_corpus",
        "foundation_analyze_phonology",
        "foundation_analyze_morphology",
        "foundation_translate_enhanced",
        "foundation_get_adaptive_difficulty",
        "foundation_search_ranked",
        "foundation_search_cross_lingual",
        "foundation_search_topical",
        "foundation_validate_structure",
        "foundation_disambiguate_translation",
        "foundation_categorize_error",
    ],
}

_FROZEN_MCP_SHA256: dict[str, str] = {
    # Frozen before the B1a batch and re-frozen on deliberate schema changes:
    # 2026-10-31 marked ``query``/``word``/``text``/``ref`` required, and the
    # same day gave ``query`` a ``minLength`` (the handlers always rejected an
    # empty call).  The tool *set* is unchanged, so the prefix test still pins
    # name/order; this dict pins the rendered entry shape itself.
    "public": "2481bbab2b981aa1bfd6d7a866d954f39d089265787b1515609cca8959b943e0",
    "member": "af4ae584fda3943e020ac0cbbd151e5a8dfa06b6662a493846ad9d26487740ed",
    "admin": "8fbb4f148bcd50b3117f2962737f24dc1d7f7a37633710815b00401472384f16",
}

#: The three tiers this batch is allowed to touch, and how.
_GROWN = {"member", "admin"}


def _digest(entries: list[dict[str, Any]]) -> str:
    blob = json.dumps(entries, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class TestSurfaceOnlyGrew:
    def test_each_tier_payload_is_a_byte_identical_prefix_of_the_published_one(self) -> None:
        for tier, before_names in _PRE_B1A_NAMES.items():
            after_names = names_for(tier)
            # nothing removed, nothing reordered
            assert after_names[: len(before_names)] == before_names, tier
            prefix = _digest(render_mcp_for(before_names))
            assert prefix == _FROZEN_MCP_SHA256[tier], (
                f"{tier}: the frozen payload changed shape — {prefix} != "
                f"{_FROZEN_MCP_SHA256[tier]}"
            )

    def test_member_and_admin_are_a_strict_superset_and_the_public_tier_is_untouched(self) -> None:
        # B1a added the language family; later batches added the learning reads to
        # member and (admin only) the learning write-back + ledger reads — so the
        # expected delta is the batch plus everything layered on top of it.
        added = {
            "member": set(LANGUAGE_TOOL_NAMES) | set(LEARNING_READ_TOOLS),
            "admin": set(LANGUAGE_TOOL_NAMES) | set(LEARNING_READ_TOOLS)
            | set(LEARNING_WRITE_TOOLS) | set(LEARNING_RUN_TOOL_NAMES),
        }
        for tier, before_names in _PRE_B1A_NAMES.items():
            before, after = set(before_names), set(names_for(tier))
            if tier in _GROWN:
                assert before < after, f"{tier} must strictly grow"
                assert after - before == added[tier], tier
            else:
                assert before == after, "the anonymous set is a founder decision — unchanged"
                assert after == set(ANONYMOUS_TOOLS)

    def test_the_post_batch_digest_differs_exactly_where_the_batch_added_tools(self) -> None:
        for tier in _PRE_B1A_NAMES:
            entries = render_mcp_for(names_for(tier))
            if tier in _GROWN:
                assert _digest(entries) != _FROZEN_MCP_SHA256[tier], tier
            else:
                assert _digest(entries) == _FROZEN_MCP_SHA256[tier], tier

    def test_public_is_a_proper_subset_of_member_which_is_a_proper_subset_of_admin(self) -> None:
        public, member, admin = (allow_list(t) for t in ("public", "member", "admin"))
        assert public < member < admin
        assert member - public == set(LANGUAGE_TOOL_NAMES) | {
            "word_related",
            "analyze_text",
        } | set(foundation_names()) | set(LEARNING_READ_TOOLS)


def render_mcp_for(names: list[str]) -> list[dict[str, Any]]:
    from zolai.agent.tools.registry import mcp_tools

    return mcp_tools(names)


def foundation_names() -> tuple[str, ...]:
    from zolai.agent.tools.foundation import FOUNDATION_TOOL_NAMES

    return FOUNDATION_TOOL_NAMES


# ── Sentinel substitution: does the handler reach the service it names? ──────


def _run(name: str, payload: dict[str, Any]):
    return execute_tool(ToolCall(name=name, input=payload), allow=MEMBER)


class _FakeRetriever:
    def __init__(self, pack: EvidencePack) -> None:
        self._pack = pack
        self.calls: list[tuple[str, int]] = []

    def query_word(self, word: str, limit: int = 20) -> EvidencePack:
        self.calls.append((word, limit))
        return self._pack


class _FakeTagger:
    def __init__(self, tagged: list[tuple[str, str]]) -> None:
        self._tagged = tagged
        self.calls: list[str] = []

    def tag(self, sentence: str) -> list[tuple[str, str]]:
        self.calls.append(sentence)
        return self._tagged


class TestSentinelSubstitution:
    """Each tool must return the *exact* sentinel of the service it calls."""

    def test_lex_search_calls_the_lexicon_route_function(self, monkeypatch) -> None:
        from zolai.api import lexicon_router

        def _sentinel(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "_SENTINEL": "lex_search",
                "items": [{"zolai": "pasian"}],
                "next_cursor": None,
                "has_more": False,
                "q": kwargs.get("q"),
                "limit": kwargs.get("limit"),
                "cursor": kwargs.get("cursor"),
            }

        monkeypatch.setattr(lexicon_router, "lexicon_search", _sentinel)
        result = _run("lex_search", {"q": "pasi", "limit": 7})
        assert result.ok, result.error
        assert result.data["_SENTINEL"] == "lex_search"  # exact value, not truthy
        # the handler forwards the caller's arguments to the route's own function
        assert result.data["q"] == "pasi"
        assert result.data["limit"] == 7
        assert result.data["cursor"] is None

    def test_word_detail_calls_the_retriever_through_the_route_model(self, monkeypatch) -> None:
        from zolai.api import rag_router

        fake = _FakeRetriever(EvidencePack(word="SENTINEL_WORD", sources=["dictionary"]))
        monkeypatch.setattr(rag_router, "get_retriever", lambda: fake)
        result = _run("word_detail", {"word": "pasian", "limit": 5})
        assert result.ok, result.error
        assert result.data["word"] == "SENTINEL_WORD"
        assert fake.calls == [("pasian", 5)]
        # reshaped through the route's own response model — same key set as REST
        from zolai.api.rag_router import WordDetailResponse

        assert set(result.data) == set(WordDetailResponse.model_fields)

    def test_syllable_segment_calls_the_rule_segmenter(self, monkeypatch) -> None:
        from zolai import syllable as syllable_mod

        monkeypatch.setattr(syllable_mod, "segment", lambda w: ["SEN", "TIMEL"])
        result = _run("syllable_segment", {"word": "Pasian"})
        assert result.ok, result.error
        assert result.data["syllables"] == ["SEN", "TIMEL"]  # exact list
        # …and the input is handed over stripped/lowercased, as the route does
        assert result.data["word"] == "Pasian"  # echoed unchanged, like the REST twin

    def test_pos_tag_calls_the_rule_tagger(self, monkeypatch) -> None:
        from zolai import pos_tagger as pos_mod

        fake = _FakeTagger([("Pasian", "SENTINEL_POS")])
        monkeypatch.setattr(pos_mod, "get_pos_tagger", lambda: fake)
        result = _run("pos_tag", {"text": "Pasian"})
        assert result.ok, result.error
        assert result.data["tokens"] == [{"word": "Pasian", "pos": "SENTINEL_POS"}]
        assert fake.calls == ["Pasian"]

    def test_zvs_validate_calls_the_strict_validator(self, monkeypatch) -> None:
        import zolai.zvs as zvs_mod

        def _fake_validate(text: str, **kwargs: Any) -> Any:
            violation = zvs_mod.Violation(
                rule_id="SENTINEL_RULE",
                category="dialect",
                forbidden="sentinel_forbidden",
                preferred="sentinel_preferred",
                message="sentinel message",
                start=0,
                end=1,
            )
            report = zvs_mod.Report(source=kwargs.get("source", "<text>"), text=text)
            report.violations.append(violation)
            return report

        monkeypatch.setattr(zvs_mod, "validate", _fake_validate)
        result = _run("zvs_validate", {"text": "anything", "context": "modern"})
        assert result.ok, result.error
        assert result.data["is_valid"] is False
        assert result.data["violations"][0]["rule_id"] == "SENTINEL_RULE"
        assert result.data["violations"][0]["preferred"] == "sentinel_preferred"
        assert result.data["context"] == "modern"

    def test_a_sentinel_never_leaks_from_one_tool_into_another(self, monkeypatch) -> None:
        """Negative control — the substitution proves *which* service was called."""
        from zolai import pos_tagger as pos_mod
        from zolai import syllable as syllable_mod

        monkeypatch.setattr(syllable_mod, "segment", lambda w: ["NEG_SYLLABLE"])
        monkeypatch.setattr(pos_mod, "get_pos_tagger", lambda: _FakeTagger([("w", "NEG_POS")]))

        syll = _run("syllable_segment", {"word": "pasian"})
        pos = _run("pos_tag", {"text": "pasian"})
        assert syll.ok and pos.ok
        assert syll.data["syllables"] == ["NEG_SYLLABLE"]
        assert "NEG_POS" not in json.dumps(syll.data)
        assert pos.data["tokens"][0]["pos"] == "NEG_POS"
        assert "NEG_SYLLABLE" not in json.dumps(pos.data)

    def test_the_unpatched_segmenter_really_segments_a_known_word(self) -> None:
        # Live non-vacuity with no DB at all: the real rule segmenter, the real
        # handler, an exact expected value.
        result = _run("syllable_segment", {"word": "pasian"})
        assert result.ok, result.error
        assert result.data["syllables"] == ["pa", "sian"]
        assert result.data["count"] == 2

    @pytest.mark.parametrize(
        ("tool", "symbol"), [
            ("lex_search", "zolai.api.lexicon_router.lexicon_search"),
            ("word_detail", "zolai.api.rag_router.get_retriever"),
            ("syllable_segment", "zolai.syllable.segment"),
            ("pos_tag", "zolai.pos_tagger.get_pos_tagger"),
            ("zvs_validate", "zolai.zvs.validate"),
        ]
    )
    def test_every_backing_capability_is_importable_in_process(self, tool: str, symbol: str) -> None:
        module, _, attr = symbol.rpartition(".")
        import importlib

        assert hasattr(importlib.import_module(module), attr), symbol
        # …and the handler that uses it names it in its own source
        assert module.rsplit(".", 1)[-1] in inspect.getsource(HANDLERS[tool]), (tool, symbol)


class TestArgumentValidationIsPure:
    """Missing/ill-typed arguments fail before any service is touched."""

    @pytest.mark.parametrize("tool", list(LANGUAGE_TOOL_NAMES))
    def test_a_missing_required_argument_is_a_typed_caller_error(self, tool: str) -> None:
        result = _run(tool, {})
        assert result.ok is False
        assert result.error_code is ToolErrorCode.INVALID_ARGUMENT
        assert result.error

    def test_an_ill_typed_argument_is_refused_without_reaching_the_validator(self, monkeypatch) -> None:
        import zolai.zvs as zvs_mod

        def _boom(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - must never run
            raise AssertionError("validate() must not be reached")

        monkeypatch.setattr(zvs_mod, "validate", _boom)
        for bad in (
            {"text": "x", "context": "whenever"},
            {"text": "x", "categories": "dialect"},
            {"text": "x", "disabled_rules": "DIALECT_02"},
        ):
            result = _run("zvs_validate", bad)
            assert result.ok is False, bad
            assert result.error_code is ToolErrorCode.INVALID_ARGUMENT

    def test_a_forged_cursor_is_a_typed_caller_error_not_a_crash(self) -> None:
        # ``_parse_cursor`` runs before the DB is opened, so this is pure: the
        # route's HTTPException must arrive as a ToolError, never on the wire.
        result = _run("lex_search", {"q": "pasi", "cursor": "1:not_a_table"})
        assert result.ok is False
        assert result.error_code is ToolErrorCode.INVALID_ARGUMENT
        assert "cursor is opaque" in (result.error or "")

    def test_an_empty_query_is_refused_before_the_route_runs(self, monkeypatch) -> None:
        from zolai.api import lexicon_router

        def _boom(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover
            raise AssertionError("lexicon_search must not be reached")

        monkeypatch.setattr(lexicon_router, "lexicon_search", _boom)
        for payload in ({"q": "   "}, {"q": ""}, {}):
            assert _run("lex_search", payload).ok is False


# ── ZVS: the strict validator, not the API helper ────────────────────────────


@pytest.fixture
def zvs_off_db(monkeypatch: pytest.MonkeyPatch):
    """Run ``zolai.zvs.validate`` for real, but without opening the live DB.

    ``validate`` seeds ``DEFAULT_EXCEPTIONS.bible_words`` from the corpus the
    first time it runs.  Both the module-global cache and the registry field are
    patched and restored so this file neither reads the 2.4 GB store nor leaves
    a mutated registry behind for the rest of the suite.
    """
    import zolai.zvs as zvs_mod

    monkeypatch.setattr(zvs_mod, "get_bible_words", lambda: set())
    monkeypatch.setattr(zvs_mod.DEFAULT_EXCEPTIONS, "bible_words", set())
    yield zvs_mod


class TestStrictZvsWiring:
    def test_a_forbidden_dialect_form_is_reported(self, zvs_off_db) -> None:
        result = _run("zvs_validate", {"text": "Ka ram a tam."})
        assert result.ok, result.error
        assert result.data["is_valid"] is False
        ids = [v["rule_id"] for v in result.data["violations"]]
        assert "DIALECT_02" in ids
        hit = next(v for v in result.data["violations"] if v["rule_id"] == "DIALECT_02")
        assert hit["forbidden"] == "ram"
        assert hit["preferred"] == "gam"

    @pytest.mark.parametrize(
        ("bad", "good"),
        [
            ("pathian", "pasian"),
            ("ram", "gam"),
            ("fapa", "tapa"),
            ("bawipa", "topa"),
            ("siangpahrang", "kumpipa"),
            ("cu", "tua"),
            ("cun", "tua"),
        ],
    )
    def test_every_forbidden_form_is_non_compliant(self, zvs_off_db, bad, good) -> None:
        """The whole AGENTS.md forbidden list, under the strict authority.

        ``zolai/api/zvs_checker.py`` answers compliant for several of these
        (its ``HISTORICAL_EXCEPTIONS`` skip-list), which is exactly why the
        tool must never call it.  ``context="modern"`` refuses the historical
        registry, so a hit here can only come from the rules themselves.
        """
        result = _run("zvs_validate", {"text": f"Ka {bad} hi.", "context": "modern"})
        assert result.ok, result.error
        assert result.data["is_valid"] is False, f"{bad!r} was reported compliant"
        hits = [v for v in result.data["violations"] if v["forbidden"] == bad]
        assert hits, (
            f"no violation named {bad!r}; saw "
            f"{[v['forbidden'] for v in result.data['violations']]}"
        )
        assert hits[0]["preferred"] == good

    def test_pathian_is_not_reported_as_compliant(self, zvs_off_db) -> None:
        # The whole reason this batch refuses ``zolai/api/zvs_checker.py``: its
        # exception list skips pathian/fapa/bawipa/cu/cun outright, so
        # ``check_zvs_compliance("Pathian in Pathian")`` answers
        # ``is_compliant: True`` (it is the module-level wrapper around
        # ``ZVSComplianceChecker().check_response``).
        from zolai.api.zvs_checker import check_zvs_compliance

        assert check_zvs_compliance("Pathian in Pathian")["is_compliant"] is True, (
            "the API helper's skip-list behaviour changed — this test documents "
            "why the tool must not use it"
        )
        result = _run("zvs_validate", {"text": "Pathian in Pathian", "context": "modern"})
        assert result.ok, result.error
        assert result.data["is_valid"] is False
        assert [v["forbidden"] for v in result.data["violations"]] == ["pathian", "pathian"]
        assert {v["preferred"] for v in result.data["violations"]} == {"pasian"}

    def test_a_clean_sentence_passes(self, zvs_off_db) -> None:
        result = _run("zvs_validate", {"text": "Pasian in vantung leh leitung a piangsak hi."})
        assert result.ok, result.error
        assert result.data["is_valid"] is True
        assert result.data["violations"] == []
        assert result.data["count"] == 0

    def test_the_tool_never_rewrites_the_text(self, zvs_off_db) -> None:
        result = _run("zvs_validate", {"text": "Ka ram a tam."})
        assert result.ok
        payload = json.dumps(result.data)
        assert "Ka gam a tam." not in payload  # it finds, it never repairs
        assert set(result.data) == {"is_valid", "count", "context", "violations"}

    def test_the_strict_validator_is_the_one_named_in_the_source(self) -> None:
        src = inspect.getsource(lang)
        assert "from ...zvs import validate" in src
        assert "zvs_checker" not in src  # the helper with the skip-list is never imported


class TestHonestyEnvelopes:
    def test_a_dictionary_query_miss_says_so_instead_of_a_bare_empty_list(self, monkeypatch) -> None:
        from zolai.api import lexicon_router

        monkeypatch.setattr(
            lexicon_router,
            "lexicon_search",
            lambda *a, **k: {"items": [], "next_cursor": None, "has_more": False},
        )
        result = _run("lex_search", {"q": "zzzznotaword"})
        assert result.ok, result.error
        assert result.data["items"] == []  # the REST keys are all still there
        assert result.data["state"] == "no_match"
        assert "not an empty or unavailable dictionary" in result.data["reason"]

    def test_a_hit_carries_no_state_key(self, monkeypatch) -> None:
        from zolai.api import lexicon_router

        monkeypatch.setattr(
            lexicon_router,
            "lexicon_search",
            lambda *a, **k: {"items": [{"zolai": "pasian"}], "next_cursor": None, "has_more": False},
        )
        result = _run("lex_search", {"q": "pasi"})
        assert result.ok
        assert "state" not in result.data

    def test_a_word_with_no_assembled_evidence_says_so(self, monkeypatch) -> None:
        from zolai.api import rag_router

        fake = _FakeRetriever(EvidencePack(word="zzzznotaword"))
        monkeypatch.setattr(rag_router, "get_retriever", lambda: fake)
        result = _run("word_detail", {"word": "zzzznotaword"})
        assert result.ok, result.error
        assert result.data["frequency"] == 0
        assert result.data["sources"] == []
        assert result.data["state"] == "no_evidence"
        assert "nothing recorded" in result.data["reason"]

    def test_an_all_zero_result_is_never_presented_as_a_measurement(self, monkeypatch) -> None:
        from zolai.api import rag_router

        fake = _FakeRetriever(EvidencePack(word="pasian", frequency=5223, sources=["dictionary"]))
        monkeypatch.setattr(rag_router, "get_retriever", lambda: fake)
        result = _run("word_detail", {"word": "pasian"})
        assert result.ok
        assert result.data["frequency"] == 5223
        assert "state" not in result.data


# ── Spec hygiene ─────────────────────────────────────────────────────────────

#: Anything a caller could try to hand us to make the server authenticate
#: somewhere on their behalf — the open-proxy risk (catalogue M3 / ledger S-1).
_CREDENTIAL_FIELDS = frozenset(
    {
        "api_key",
        "user_api_key",
        "user_provider",
        "provider_key",
        "token",
        "access_token",
        "refresh_token",
        "oauth_token",
        "authorization",
        "bearer",
        "credentials",
        "credential",
        "secret",
        "password",
        "x-api-key",
    }
)


class TestSpecHygiene:
    def test_no_spec_offers_a_caller_supplied_credential_field(self) -> None:
        for name, spec in SPECS.items():
            props = set((spec.parameters or {}).get("properties", {}))
            assert not (props & _CREDENTIAL_FIELDS), f"{name} offers {props & _CREDENTIAL_FIELDS}"
            required = set((spec.parameters or {}).get("required", []))
            assert not (required & _CREDENTIAL_FIELDS), name

    def test_every_language_tool_is_a_read_and_declared_non_destructive(self) -> None:
        for name in LANGUAGE_TOOL_NAMES:
            spec = SPECS[name]
            assert spec.writes is False, name
            assert spec.destructive is False, name
            assert spec.is_destructive is False, name
            assert spec.admin_only is False, name
            assert spec.requires_network is False, name
            annotations = render_mcp(spec)["annotations"]
            assert annotations["readOnlyHint"] is True, name
            assert annotations["destructiveHint"] is False, name
            assert annotations["openWorldHint"] is False, name

    def test_every_language_name_satisfies_both_wires_grammar(self) -> None:
        for name in LANGUAGE_TOOL_NAMES:
            assert TOOL_NAME_RE.match(name), name
            assert render_openai(SPECS[name])["function"]["name"] == name
            assert render_mcp(SPECS[name])["name"] == name

    def test_scopes_come_from_the_frozen_vocabulary_only_where_a_route_gates(self) -> None:
        from zolai.api.auth import VALID_ACTIONS

        # verified in source this batch: which backing routes declare require_scope
        gated = {
            "lex_search": "dataset:read",      # lexicon_router.py:40
            "word_detail": "dataset:read",     # rag_router.py:166
            "syllable_segment": "dataset:read",  # linguistics_router.py:73
            "pos_tag": "pos:read",             # linguistics_router.py:51
        }
        for name, scope in gated.items():
            assert SPECS[name].scope == scope, name
            assert scope in VALID_ACTIONS, scope
        # tool-only: no route backs it, so no permission is invented for it
        assert SPECS["zvs_validate"].scope is None

    def test_a_language_tool_is_offered_to_member_and_admin_but_never_anonymous(self) -> None:
        for name in LANGUAGE_TOOL_NAMES:
            assert name in allow_list("member"), name
            assert name in allow_list("admin"), name
            assert name not in allow_list("public"), name
            assert name not in ANONYMOUS_TOOLS, name

    def test_the_sixth_tool_of_the_batch_was_skipped_because_it_already_exists(self) -> None:
        """A grammar/SOV structure check must not get a third name."""
        from zolai.agent.tools import executor as exec_mod
        from zolai.agent.tools import foundation as fnd
        from zolai.learning.grammar_editor import GrammarEditor

        # L25's capability — GrammarEditor().validate_sentence_structure — is
        # already published as foundation_validate_structure.
        assert "validate_sentence_structure" in inspect.getsource(
            fnd._OPERATION_HANDLERS["validate_structure"]
        )
        # L26's capability — GrammarEditor().validate_text — is already
        # published as grammar_check, and validate_text runs that same
        # structure validator over every sentence (returning its tokens,
        # verb_position and zvs_compliant).
        assert "validate_text" in inspect.getsource(exec_mod.HANDLERS["grammar_check"])
        assert "validate_sentence_structure" in inspect.getsource(GrammarEditor.validate_text)

        # …so the batch shipped five tools, not six, and no third name appeared.
        assert set(LANGUAGE_TOOL_NAMES) == {
            "lex_search",
            "word_detail",
            "syllable_segment",
            "pos_tag",
            "zvs_validate",
        }
        for ghost in ("grammar_structure", "sov_check", "grammar_sov", "sentence_structure"):
            assert ghost not in SPECS, ghost

    def test_the_family_declares_exactly_what_it_publishes_and_handles(self) -> None:
        assert LANGUAGE_TOOL_NAMES == tuple(LANGUAGE_SPECS)
        assert set(LANGUAGE_SPECS) <= set(SPECS)
        assert set(lang.LANGUAGE_HANDLERS) == set(LANGUAGE_SPECS)
        for name in LANGUAGE_SPECS:
            assert HANDLERS[name] is lang.LANGUAGE_HANDLERS[name], name

    def test_an_unknown_language_tool_is_refused_before_it_runs(self) -> None:
        result = execute_tool(
            ToolCall(name="grammar_structure", input={"text": "x"}), allow=MEMBER
        )
        assert result.ok is False
        assert "unknown tool" in (result.error or "")

    def test_a_language_tool_is_refused_outside_its_tier(self) -> None:
        result = execute_tool(ToolCall(name="zvs_validate", input={"text": "x"}), allow=allow_list("public"))
        assert result.ok is False
        assert result.error_code is ToolErrorCode.FORBIDDEN
        assert "not allowed" in (result.error or "")


class TestHandlerErrorsAreTyped:
    def test_a_dead_dependency_is_reported_as_unavailable_not_as_the_callers_fault(self) -> None:
        from zolai.api import lexicon_router

        def _dead(*args: Any, **kwargs: Any) -> Any:
            raise OSError("no such file: zolai.db")

        original = lexicon_router.lexicon_search
        lexicon_router.lexicon_search = _dead
        try:
            result = _run("lex_search", {"q": "pasi"})
        finally:
            lexicon_router.lexicon_search = original
        assert result.ok is False
        assert result.error_code is ToolErrorCode.UNAVAILABLE

    def test_a_handler_never_raises_out_of_the_executor(self) -> None:
        # The executor turns any unhandled exception into a typed failure with a
        # human sentence; nothing here may escape as a traceback.
        for name in LANGUAGE_TOOL_NAMES:
            result = execute_tool(ToolCall(name=name, input={"q": "x", "word": "x", "text": "x"}), allow=MEMBER)
            assert result.tool_name == name
            assert isinstance(result.error, (str, type(None)))


def test_tool_error_defaults_to_a_caller_fault() -> None:
    # Guards the convention the language handlers rely on: an explicit
    # ToolErrorCode.UNAVAILABLE is required to call a server-side failure the
    # caller's problem.
    assert ToolError("nope").code is ToolErrorCode.INVALID_ARGUMENT
    assert ToolError("nope", ToolErrorCode.UNAVAILABLE).code is ToolErrorCode.UNAVAILABLE
