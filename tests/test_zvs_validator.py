"""Unit tests for the ZVS 2018 compliance validator."""

from __future__ import annotations

import json
from pathlib import Path

from zolai.zvs import ExceptionRegistry, Ruleset, Violation, validate
from zolai.zvs.cli import main as cli_main
from zolai.zvs.rules_data import (
    CATEGORY_DIALECT,
    DIALECT_FORBIDDEN_TO_PREFERRED,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry_with_token(token: str) -> ExceptionRegistry:
    reg = ExceptionRegistry()
    reg.add_token(token)
    return reg


# ---------------------------------------------------------------------------
# Dialect forbidden forms
# ---------------------------------------------------------------------------

class TestDialectForbiddenForms:
    """Each forbidden dialect form must be flagged."""

    def test_pathian_flagged(self) -> None:
        report = validate("Pathian tapa hi.")
        assert not report.is_valid
        assert any(v.forbidden == "pathian" for v in report.violations)

    def test_ram_flagged(self) -> None:
        report = validate("Ka ram a tam.")
        assert not report.is_valid
        assert any(v.forbidden == "ram" for v in report.violations)

    def test_fapa_flagged(self) -> None:
        report = validate("Fapa chu a hi.")
        assert not report.is_valid
        assert any(v.forbidden == "fapa" for v in report.violations)

    def test_bawipa_flagged(self) -> None:
        report = validate("Bawipa hia.")
        assert not report.is_valid
        assert any(v.forbidden == "bawipa" for v in report.violations)

    def test_siangpahrang_flagged(self) -> None:
        report = validate("Siangpahrang a hia.")
        assert not report.is_valid
        assert any(v.forbidden == "siangpahrang" for v in report.violations)

    def test_cu_flagged(self) -> None:
        report = validate("Cu hi a hih.")
        assert not report.is_valid
        assert any(v.forbidden == "cu" for v in report.violations)

    def test_cun_flagged(self) -> None:
        report = validate("Cun ka a hi.")
        assert not report.is_valid
        assert any(v.forbidden == "cun" for v in report.violations)

    def test_suah_flagged(self) -> None:
        report = validate("Suah chu a hi.")
        assert not report.is_valid
        assert any(v.forbidden == "suah" for v in report.violations)

    def test_zalenna_flagged(self) -> None:
        report = validate("Zalenna hia.")
        assert not report.is_valid
        assert any(v.forbidden == "zalenna" for v in report.violations)

    def test_nunnak_flagged(self) -> None:
        report = validate("Nunnak hi.")
        assert not report.is_valid
        assert any(v.forbidden == "nunnak" for v in report.violations)

    def test_all_forbidden_forms_present_in_data(self) -> None:
        """Sanity: every expected forbidden form has an entry in the map."""
        expected = {
            "pathian", "ram", "fapa", "bawipa", "siangpahrang",
            "cu", "cun", "suah", "zalenna", "nunnak",
        }
        assert expected == set(DIALECT_FORBIDDEN_TO_PREFERRED.keys())


# ---------------------------------------------------------------------------
# Clean modern Zolai sentence
# ---------------------------------------------------------------------------

class TestCleanSentence:
    """A sentence with no violations must pass."""

    def test_clean_modern_sentence(self) -> None:
        report = validate("Pasian gam na tapa a hi, topa i hihleh.")
        assert report.is_valid
        assert report.count == 0
        assert report.source == "<text>"

    def test_empty_string(self) -> None:
        report = validate("")
        assert report.is_valid


# ---------------------------------------------------------------------------
# Exception registry
# ---------------------------------------------------------------------------

class TestExceptions:
    """A registered exception must suppress the corresponding violation."""

    def test_token_exception_suppresses(self) -> None:
        reg = _make_registry_with_token("pathian")
        report = validate("Pathian tapa hi.", exceptions=reg)
        assert report.is_valid

    def test_rule_id_exception_suppresses(self) -> None:
        reg = ExceptionRegistry()
        reg.add_rule("DIALECT_01")  # DIALECT_01 = pathian -> pasian
        report = validate("Pathian tapa hi.", exceptions=reg)
        assert report.is_valid

    def test_phrase_exception_suppresses(self) -> None:
        reg = ExceptionRegistry()
        reg.add_phrase("pathian tapa hi.")
        report = validate("Pathian tapa hi.", exceptions=reg)
        assert report.is_valid

    def test_no_exception_flags(self) -> None:
        report = validate("Pathian tapa hi.")
        assert not report.is_valid


# ---------------------------------------------------------------------------
# Report serialisation
# ---------------------------------------------------------------------------

class TestReportSerialisation:
    """to_dict / to_json round-trips must work."""

    def test_to_dict_structure(self) -> None:
        report = validate("Pathian tapa hi.")
        d = report.to_dict()
        assert d["source"] == "<text>"
        assert d["valid"] is False
        assert d["violation_count"] >= 1
        assert isinstance(d["violations"], list)
        assert d["violations"][0]["rule_id"].startswith("DIALECT_")

    def test_to_json_round_trip(self) -> None:
        report = validate("Pathian tapa hi.")
        blob = report.to_json()
        parsed = json.loads(blob)
        assert parsed["valid"] is False
        assert parsed["violation_count"] >= 1
        # Round-trip: to_dict should produce same structure
        assert parsed["source"] == report.to_dict()["source"]
        assert parsed["violations"][0]["forbidden"] == "pathian"

    def test_clean_report_to_dict(self) -> None:
        report = validate("Pasian gam a hi.")
        d = report.to_dict()
        assert d["valid"] is True
        assert d["violation_count"] == 0
        assert d["violations"] == []

    def test_violation_to_dict(self) -> None:
        v = Violation(
            rule_id="DIALECT_01",
            category="dialect",
            forbidden="pathian",
            preferred="pasian",
            message="Use standard Tedim 'pasian' instead of 'pathian'.",
            start=0,
            end=7,
            context="pathian",
        )
        d = v.to_dict()
        assert d["rule_id"] == "DIALECT_01"
        assert d["start"] == 0
        assert d["context"] == "pathian"


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

class TestCLI:
    """The zolai-zvs CLI must work on temp files."""

    def test_validate_clean_file(self, tmp_path: Path) -> None:
        f = tmp_path / "clean.txt"
        f.write_text("Pasian gam a hi.\n", encoding="utf-8")
        ret = cli_main(["validate", str(f)])
        assert ret == 0

    def test_validate_violating_file(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.txt"
        f.write_text("Pathian tapa hi.\n", encoding="utf-8")
        ret = cli_main(["validate", str(f)])
        assert ret == 1

    def test_validate_json_output(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.txt"
        f.write_text("Pathian tapa hi.\n", encoding="utf-8")
        # Capture stdout by running main and checking JSON output via capsys
        import sys
        from io import StringIO

        old_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            ret = cli_main(["validate", "--json", str(f)])
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        assert ret == 1
        parsed = json.loads(output)
        assert parsed["valid"] is False
        assert len(parsed["reports"]) >= 1

    def test_validate_violating_file_return_code(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.txt"
        f.write_text("Fapa chu a hi.\n", encoding="utf-8")
        ret = cli_main(["validate", str(f)])
        assert ret == 1

    def test_validate_combined(self, tmp_path: Path) -> None:
        good = tmp_path / "good.txt"
        bad = tmp_path / "bad.txt"
        good.write_text("Pasian gam a hi.\n", encoding="utf-8")
        bad.write_text("Pathian tapa hi.\n", encoding="utf-8")
        ret = cli_main(["validate", str(good), str(bad)])
        assert ret == 1  # at least one violation


# ---------------------------------------------------------------------------
# Ruleset configuration
# ---------------------------------------------------------------------------

class TestRulesetConfig:
    """Ruleset respects categories and disabled_rules."""

    def test_exclude_dialect(self) -> None:
        rs = Ruleset(categories=["compound", "stem"])
        violations = rs.apply("Pathian tapa hi.")
        # No dialect violations when dialect is excluded
        assert not any(v.category == CATEGORY_DIALECT for v in violations)

    def test_disable_specific_rule(self) -> None:
        rs = Ruleset(disabled_rules=["DIALECT_01"])
        violations = rs.apply("Pathian tapa hi.")
        assert not any(v.rule_id == "DIALECT_01" for v in violations)

    def test_default_rules_count(self) -> None:
        rs = Ruleset()
        # Should have dialect + compound + stem rules, no phonotactic
        assert len(rs.rules) > 0
        categories = {r.category for r in rs.rules}
        assert "phonotactic" not in categories


# ---------------------------------------------------------------------------
# FAILED-CLOSURE / historical form flagging
# ---------------------------------------------------------------------------

class TestHistoricalFlagging:
    """Historical forms (e.g. Bible-era) must still be flagged, not silenced."""

    def test_pathian_in_biblical_context(self) -> None:
        report = validate("Noah ka suak Pathian thei.")
        assert not report.is_valid
        assert any(v.forbidden == "pathian" for v in report.violations)

    def test_fapa_in_historical_context(self) -> None:
        report = validate("Ram pum ah Fapa a hih a hia.")
        assert not report.is_valid
        assert any(v.category == "dialect" for v in report.violations)


# ---------------------------------------------------------------------------
# Preferred form suggestions
# ---------------------------------------------------------------------------

class TestPreferredSuggestions:
    """Each violation should carry the correct preferred form."""

    def test_pathian_suggests_pasian(self) -> None:
        report = validate("Pathian tapa hi.")
        pathian_v = next(v for v in report.violations if v.forbidden == "pathian")
        assert pathian_v.preferred == "pasian"

    def test_fapa_suggests_tapa(self) -> None:
        report = validate("Fapa hi.")
        v = next(v for v in report.violations if v.forbidden == "fapa")
        assert v.preferred == "tapa"
