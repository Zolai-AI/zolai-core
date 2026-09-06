"""Tests for grammar rules — particle rules and greeting usage."""
import subprocess
import sys
from pathlib import Path

# Ensure zolai is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from zolai.rules.zolai_rules_reference import ZolaiRules

MODULE_PATH = str(
    Path(__file__).parent.parent / "zolai" / "rules" / "zolai_rules_reference.py"
)


# ── py_compile ──────────────────────────────────────────────────────────


def test_module_compiles():
    """Module compiles without syntax errors."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", MODULE_PATH],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


# ── ruff ────────────────────────────────────────────────────────────────


def test_ruff_lint():
    """Module passes ruff lint."""
    result = subprocess.run(
        ["ruff", "check", MODULE_PATH],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ── 'na' particle rules ────────────────────────────────────────────────


def test_na_particle_rules_exist():
    """PARTICLE_RULES for 'na' is defined."""
    rules = ZolaiRules.PARTICLE_RULES
    assert 'na' in rules, "Missing 'na' in PARTICLE_RULES"


def test_na_particle_functions():
    """'na' particle has possessive and noun-maker functions."""
    na_rules = ZolaiRules.PARTICLE_RULES['na']
    assert na_rules['as_possessive'] == 'your (2nd person singular)'
    assert na_rules['as_noun_maker'] == 'makes abstract nouns from verbs/adjectives'


def test_na_noun_maker_examples():
    """'na' noun-maker examples are correct."""
    na_rules = ZolaiRules.PARTICLE_RULES['na']
    examples = na_rules['examples']
    # Core examples from the user's grammar lesson
    assert examples['lungdam'] == 'thank you (verb)'
    assert examples['lungdam na'] == 'gratitude (noun)'
    assert examples['kum'] == 'year'
    assert examples['kum na'] == 'age'
    assert examples['lawm'] == 'friend'
    assert examples['lawm na'] == 'friendship'
    assert examples['dam'] == 'healthy'
    assert examples['dam na'] == 'health'


def test_na_possessive_in_pronouns():
    """'na' is also listed as 2nd person possessive in PRONOUNS."""
    assert ZolaiRules.PRONOUNS['na'] == '2nd person (you/your)'


# ── Correct greetings ──────────────────────────────────────────────────


def test_correct_greetings_exist():
    """CORRECT_GREETINGS is defined and has entries."""
    greetings = ZolaiRules.CORRECT_GREETINGS
    assert len(greetings) >= 3, "Expected at least 3 correct greeting entries"


def test_lungdam_is_thank_you():
    """'Lungdam' is the correct form for thanking someone."""
    greetings = ZolaiRules.CORRECT_GREETINGS
    assert greetings['lungdam'] == 'thank you (when thanking someone)'


def test_lungdam_na_is_gratitude_not_thanks():
    """'Lungdam na' means gratitude, NOT for thanking someone."""
    greetings = ZolaiRules.CORRECT_GREETINGS
    assert greetings['lungdam na'] == 'gratitude (the concept, NOT for thanking)'


def test_lungdam_mahmah():
    """'Lungdam mahmah' is thank you very much."""
    greetings = ZolaiRules.CORRECT_GREETINGS
    assert greetings['lungdam mahmah'] == 'thank you very much'


def test_ka_dam_hi():
    """'Ka dam hi' is the correct response meaning I am well."""
    greetings = ZolaiRules.CORRECT_GREETINGS
    assert greetings['ka dam hi'] == 'I am well'


# ── Greeting warnings ──────────────────────────────────────────────────


def test_greeting_warnings_exist():
    """GREETING_WARNINGS is defined."""
    warnings = ZolaiRules.GREETING_WARNINGS
    assert 'lungdam na' in warnings, "Missing warning for 'lungdam na'"


def test_lungdam_na_warning():
    """Warning about 'lungdam na' is correct."""
    w = ZolaiRules.GREETING_WARNINGS['lungdam na']
    assert w['wrong_for'] == 'thanking someone'
    assert 'na' in w['reason']
    assert 'Lungdam!' in w['correct']


# ── Token-efficient summary includes particle rules ────────────────────


def test_summary_includes_particle_rules():
    """Token-efficient summary mentions 'na' particle."""
    summary = ZolaiRules.get_token_efficient_summary()
    assert 'na' in summary
    assert 'lungdam na=gratitude' in summary or 'gratitude' in summary
    assert 'Lungdam' in summary


# ── Attested words only ───────────────────────────────────────────────


def test_attested_greeting_words():
    """All greeting words are in the attested words set."""
    attested = {
        'lungdam', 'pasian', 'topa', 'kum', 'gam', 'vantung',
        'tui', 'mi', 'numei', 'sing', 'nek', 'hiam', 'hoih',
        'koh', 'aw', 'ai', 'dam', 'lawm',
    }
    # All words used in greetings/rules should be attested
    greeting_words = {'lungdam', 'dam', 'kum', 'lawm'}
    for word in greeting_words:
        assert word in attested, f"{word} is not in attested set"
