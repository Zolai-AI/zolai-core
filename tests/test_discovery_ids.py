"""Discovery ids must be stable across processes.

Both writers key idempotency on the generated id, and both used to end it with
``abs(hash(text)) % 100000``. ``hash()`` of a string is salted per process, so
a second run never found the first row: the live DB accumulated 22 copies of
each ``disc_g_*`` pattern and 500 ``disc_sp_*`` ``sov`` rows.
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zolai.learning.discovery.grammar import (  # noqa: E402
    _extract_phenomena_from_text,
    _phenomenon_id,
)
from zolai.learning.discovery.sentence_patterns import _pattern_id  # noqa: E402


class _StubLearner:
    def _extract_components(self, text: str) -> dict:
        return {}


def test_grammar_id_is_per_marker_not_per_sentence():
    assert _phenomenon_id("ergative", "in") == "disc_g_ergative_in"
    assert _phenomenon_id("tense_aspect", "ta") == "disc_g_tense_aspect_ta"


def test_extracted_ids_do_not_depend_on_the_sentence():
    learner = _StubLearner()
    first = _extract_phenomena_from_text("Pasian in leitung a bawl hi.", "JHN 1:1", "", learner)
    second = _extract_phenomena_from_text("Topa in kei hong huh hi.", "JHN 1:2", "", learner)

    first_ids = [p.phenomenon_id for p in first if p.phenomenon_type == "ergative"]
    second_ids = [p.phenomenon_id for p in second if p.phenomenon_type == "ergative"]
    assert first_ids == second_ids == ["disc_g_ergative_in"]


def test_extracted_ids_carry_no_hash_suffix():
    phenomena = _extract_phenomena_from_text("Pasian in leitung a bawl hi.", "JHN 1:1", "", _StubLearner())
    assert phenomena
    for p in phenomena:
        tail = p.phenomenon_id.rsplit("_", 1)[-1]
        assert not tail.isdigit(), p.phenomenon_id


def test_sentence_id_depends_on_structure_not_example_or_ref():
    digest = hashlib.sha1(b"S O V").hexdigest()[:10]
    same_a = _pattern_id("sov", "S O V")
    same_b = _pattern_id("sov", "S O V")
    other = _pattern_id("sov", "S V")

    assert same_a == same_b == f"disc_sp_sov_{digest}"
    assert same_a != other
    assert same_a != _pattern_id("ergative", "S O V")
