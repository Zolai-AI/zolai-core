"""RED-first suite for the G4 declarative-particle / punctuation / ka rules.

The three recon contract cases are pinned here:
``Ka min Peter hi.`` valid · ``Do ming Peter nga.`` invalid (no sentence-final
particle) · ``Ka min Peter hi`` invalid (missing final punctuation).  Questions
marked with ``hiam`` are exempt from both errors, and ``ka`` agreement is a
WARNING only.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MODULE = "zolai.learning.grammar_editor"

# Bind the real module at collection time — before any fixture can run.  A
# pre-existing suite (tests/test_foundation_contract.py) swaps this module in
# ``sys.modules`` for a stub and its teardown restores the stub, so resolving
# the module at call time would pick up the stub mid-session and fail every
# case here with ``AttributeError: '_Editor' ...``.
_real_module = importlib.import_module(MODULE)


def _editor():
    return _real_module.GrammarEditor()


def _errors(sentence: str) -> list[str]:
    return _editor().validate_sentence_structure(sentence)["errors"]


# ── recon contract ──────────────────────────────────────────────────────────


def test_valid_declarative_with_subject_and_particle() -> None:
    result = _editor().validate_sentence_structure("Ka min Peter hi.")
    assert result["valid"] is True
    assert result["errors"] == []
    assert result["warnings"] == []


def test_missing_sentence_final_particle_is_an_error() -> None:
    result = _editor().validate_sentence_structure("Do ming Peter nga.")
    assert result["valid"] is False
    assert any("declarative sentence requires sentence-final particle" in e for e in result["errors"])


def test_missing_final_punctuation_is_an_error() -> None:
    result = _editor().validate_sentence_structure("Ka min Peter hi")
    assert result["valid"] is False
    assert any("missing final punctuation" in e for e in result["errors"])


# ── question exemption ──────────────────────────────────────────────────────


def test_question_with_mark_is_valid() -> None:
    result = _editor().validate_sentence_structure("Na pai hiam?")
    assert result["valid"] is True
    assert result["errors"] == []


def test_question_without_final_punctuation_is_exempt() -> None:
    """``hiam`` marks a question → both new errors are suppressed."""
    result = _editor().validate_sentence_structure("Na pai hiam")
    assert result["valid"] is True
    assert result["errors"] == []


def test_question_without_declared_particle_is_exempt() -> None:
    result = _editor().validate_sentence_structure("Do ming Peter hiam")
    assert result["valid"] is True
    assert result["errors"] == []


# ── ka agreement (warning only) ─────────────────────────────────────────────


def test_ka_out_of_subject_slot_is_warning_only() -> None:
    result = _editor().validate_sentence_structure("Pasian ka ne hi.")
    assert result["valid"] is True, "ka agreement must never invalidate a sentence"
    assert any("1st-person 'ka' not in subject position" in w for w in result["warnings"])


def test_ka_after_topic_particle_is_accepted() -> None:
    result = _editor().validate_sentence_structure("In ka pai hi.")
    assert result["valid"] is True
    assert not any("ka" in w for w in result["warnings"])


def test_sentence_without_ka_has_no_ka_warning() -> None:
    result = _editor().validate_sentence_structure("Mi in ne hi.")
    assert result["valid"] is True
    assert not any("ka" in w for w in result["warnings"])


# ── bang-finished exclamation: punctuation satisfied, particle rule is '.'-keyed


def test_exclamation_final_sentence_has_no_particle_error() -> None:
    result = _editor().validate_sentence_structure("Do pai!")
    assert not any("declarative sentence requires" in e for e in result["errors"])
    assert not any("missing final punctuation" in e for e in result["errors"])


# ── existing behaviour must not regress ─────────────────────────────────────


def test_negation_error_still_reported() -> None:
    result = _editor().validate_sentence_structure("Ka pai kei hi.")
    assert any("Negation" in e for e in result["errors"])


def test_empty_sentence_still_fails() -> None:
    result = _editor().validate_sentence_structure("   ")
    assert result["valid"] is False


def test_validate_text_keeps_clean_text_clean() -> None:
    """``validate_text`` splits on terminators — they must reach the sentence.

    Otherwise every sentence it feeds to ``validate_sentence_structure`` would
    be flagged ``missing final punctuation`` and the tool could never be clean.
    """
    result = _editor().validate_text("Pasian in gam a piangsak hi. Mi in ne hi.")
    assert result["sentence_count"] == 2
    assert result["error_count"] == 0, result["results"]
    assert result["valid"] is True
