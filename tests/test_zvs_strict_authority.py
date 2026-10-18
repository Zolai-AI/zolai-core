"""P0 defect 1 — the API's answer paths use the STRICT ZVS authority.

``zolai/api/zvs_checker.py`` (the lenient checker) lists the ZVS 2018
forbidden forms in its own ``HISTORICAL_EXCEPTIONS``, so
``check_response("Pathian in Pathian")`` returned ``is_compliant: True`` and
user-facing answers could be rubber-stamped with deprecated orthography.

Both user-facing call paths are pinned here:

1. ``server.py``'s ``/chat/zolai`` + ``/chat/gemini`` endpoints call
   ``check_zvs_compliance`` — they must import it from ``zvs_strict`` (a
   source guard, because the endpoints live inside ``create_app`` and cannot
   be called without booting the app), and
2. ``AnswerValidator.validate`` (used by ``/chat/gemini``) is exercised
   directly for behaviour.

The non-vacuity anchor: the lenient checker is *asserted to still rubber-stamp
``Pathian``* — proving these tests are not accidentally re-testing the old
behaviour, and pinning why the routing had to change.

No database and no FastAPI app: ``zvs_strict`` is database-free (sentinel
``bible_words``) and the source guard reads the file instead of importing
``server``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from zolai.api import zvs_strict
from zolai.api.answer_validator import AnswerValidator
from zolai.api.zvs_checker import check_zvs_compliance as lenient_check

# Every org-forbidden form (context/code-standards.md) -> its ZVS 2018 form.
FORBIDDEN_TO_PREFERRED = {
    "pathian": "pasian",
    "ram": "gam",
    "fapa": "tapa",
    "bawipa": "topa",
    "siangpahrang": "kumpipa",
    "cu": "tua",
    "cun": "tua",
}

# One sentence per forbidden form — every one of them is non-compliant.
BAD_SENTENCES = {
    "pathian": "Pathian in Pathian hi.",
    "ram": "Ka ram a tam hi.",
    "fapa": "A fapa om hi.",
    "bawipa": "Bawipa a pai hi.",
    "siangpahrang": "Siangpahrang in a lak hi.",
    "cu": "Ka cu a om hi.",
    "cun": "A cun pai hi.",
}

CLEAN_SENTENCE = "Pasian in gam a piangsak hi."


class TestStrictChecker:
    def test_every_forbidden_form_is_non_compliant(self) -> None:
        for word, preferred in FORBIDDEN_TO_PREFERRED.items():
            result = zvs_strict.check_response(BAD_SENTENCES[word])
            assert result["is_compliant"] is False, word
            flagged = {v[0] for v in result["violations"]}
            assert word in flagged, (word, result["violations"])
            suggested = {v[1] for v in result["violations"] if v[0] == word}
            assert preferred in suggested, (word, suggested)
            assert preferred in result["corrected_text"], (word, result["corrected_text"])

    def test_clean_zvs_2018_sentence_is_compliant(self) -> None:
        result = zvs_strict.check_response(CLEAN_SENTENCE)
        assert result["is_compliant"] is True, result["violations"]
        assert result["violations"] == []
        assert result["corrected_text"] == CLEAN_SENTENCE

    def test_bible_attestation_cannot_suppress_a_forbidden_form(self) -> None:
        """``pathian`` IS a Bible word (TDB77 old orthography); still flagged.

        The lenient checker and ``zolai.zvs.validate``'s *default* registry
        both whitelist it for historical/scripture text — the strict answer
        adapter deliberately carries no token whitelist at all.
        """
        assert zvs_strict._ANSWER_EXCEPTIONS.tokens == set()
        assert zvs_strict._ANSWER_EXCEPTIONS.rule_ids == set()
        assert zvs_strict._ANSWER_EXCEPTIONS.phrases == set()
        assert zvs_strict.check_response("Pathian in Pathian hi.")["is_compliant"] is False

    def test_check_zvs_compliance_is_the_strict_entry_point(self) -> None:
        assert zvs_strict.check_zvs_compliance is zvs_strict.check_response
        assert zvs_strict.check_zvs_compliance("Ka ram a tam hi.")["is_compliant"] is False


class TestAnswerValidatorPath:
    def _validate(self, text: str) -> dict:
        return asyncio.run(AnswerValidator(":memory:").validate(text))

    def test_validator_reports_each_forbidden_form(self) -> None:
        for word, preferred in FORBIDDEN_TO_PREFERRED.items():
            out = self._validate(BAD_SENTENCES[word])
            assert out["valid"] is False, word
            assert out["zvs_violations"], word
            assert word in {v[0] for v in out["zvs_violations"]}, word
            assert any(
                f"ZVS: '{word}' → '{preferred}'" in c for c in out["corrections"]
            ), (word, out["corrections"])
            assert out["confidence_impact"] < 0.0

    def test_validator_accepts_a_clean_sentence(self) -> None:
        out = self._validate(CLEAN_SENTENCE)
        assert out["valid"] is True, out
        assert out["zvs_violations"] == []
        assert out["confidence_impact"] == 0.0


class TestServerRouting:
    def test_server_chat_paths_import_the_strict_authority(self) -> None:
        """Source guard: both chat endpoints call ``zvs_strict``.

        The endpoints are closures inside ``create_app``; importing the
        module is safe but calling them would boot the app (forbidden — its
        lifespan migrates the live 2.4 GB DB), so the routing itself is
        pinned at the source level.  Pre-change this fails: ``server.py``
        imported ``.zvs_checker`` at both sites.
        """
        src = (Path(__file__).resolve().parents[1] / "zolai" / "api" / "server.py").read_text()
        assert src.count("from .zvs_strict import check_zvs_compliance") == 2
        assert "zvs_checker" not in src

    def test_answer_validator_no_longer_imports_the_lenient_checker(self) -> None:
        src = (
            Path(__file__).resolve().parents[1] / "zolai" / "api" / "answer_validator.py"
        ).read_text()
        assert "zvs_strict" in src
        assert "zvs_checker" not in src

    def test_the_lenient_checker_still_rubber_stamps_pathian(self) -> None:
        """Non-vacuity anchor: the defect this batch routes around is real.

        ``zvs_checker`` is kept (deletion candidate, another batch owns it) —
        if this ever starts failing, the strict-routing tests above need to be
        re-evaluated rather than quietly credited.
        """
        assert lenient_check("Pathian in Pathian hi.")["is_compliant"] is True
