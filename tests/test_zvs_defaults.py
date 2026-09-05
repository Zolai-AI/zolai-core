"""Tests for the seeded historical DEFAULT_EXCEPTIONS + report-only scanner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.zvs.scan_content as scan_content
from zolai.zvs import ExceptionRegistry, validate
from zolai.zvs.cli import main as cli_main
from zolai.zvs.rules_data import DEFAULT_EXCEPTIONS


def test_default_exceptions_suppresses_seeded_token() -> None:
    """A seeded historical token (bawipa) is suppressed by DEFAULT_EXCEPTIONS."""
    assert "bawipa" in DEFAULT_EXCEPTIONS.tokens
    report = validate("Bawipa hia.")
    assert report.is_valid


def test_default_exceptions_suppresses_seeded_phrase() -> None:
    """A seeded historical phrase is suppressed."""
    # 'tedim 1932' is a seeded phrase naming the 1932 Bible translation.
    assert any("tedim 1932" in p for p in DEFAULT_EXCEPTIONS.phrases)
    report = validate("The Tedim 1932 Bible translation documents classic forms.")
    assert report.is_valid


def test_genuine_new_forbidden_form_still_flags() -> None:
    """ram is NOT seeded, so a genuine modern violation must still flag."""
    assert "ram" not in DEFAULT_EXCEPTIONS.tokens
    report = validate("Ka ram a tam.")
    assert not report.is_valid
    assert any(v.forbidden == "ram" for v in report.violations)


def test_rule_id_exception_mechanism_works() -> None:
    """Rule-level suppression still works independently of token seeds."""
    reg = ExceptionRegistry()
    reg.add_rule("DIALECT_08")  # suah -> chuak
    report = validate("Suah chu a hi.", exceptions=reg)
    assert report.is_valid

    # Without the rule exception, suah (unseeded) is flagged.
    unex = validate("Suah chu a hi.")
    assert not unex.is_valid


def test_cli_use_default_exceptions_parity(tmp_path: Path) -> None:
    """CLI --use-default-exceptions matches library defaults for a seeded token."""
    f = tmp_path / "seeded.txt"
    f.write_text("Bawipa hia.\n", encoding="utf-8")

    # Default CLI run has an EMPTY registry, so the seeded token is flagged.
    ret_default = cli_main(["validate", str(f)])
    assert ret_default == 1

    # With --use-default-exceptions, the seeded token is suppressed.
    ret_defaults = cli_main(
        ["validate", "--use-default-exceptions", str(f)]
    )
    assert ret_defaults == 0

    # Library parity: validate() itself uses DEFAULT_EXCEPTIONS by default.
    assert validate("Bawipa hia.").is_valid


def test_scan_report_only_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """scan_content exits 0 always and writes report JSON + MD."""
    fake_wiki = tmp_path / "wiki"
    fake_wiki.mkdir()
    (fake_wiki / "doc.md").write_text("Pasian gam a tam hi.\n", encoding="utf-8")
    report_dir = tmp_path / "report"
    monkeypatch.setattr(scan_content, "DEFAULT_WIKI", fake_wiki)
    monkeypatch.setattr(scan_content, "REPORT_DIR", report_dir)

    ret = scan_content.main(["--wiki"])
    assert ret == 0

    json_files = list(report_dir.glob("zvs-scan-*.json"))
    assert len(json_files) == 1
    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert payload["report_only"] is True
    assert payload["total_sources"] >= 1

    assert (report_dir / "zvs-scan-summary.md").exists()


def test_scan_missing_wiki_warns_but_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing wiki checkout must warn and still exit 0 (no crash)."""
    missing = tmp_path / "does-not-exist"
    monkeypatch.setattr(scan_content, "DEFAULT_WIKI", missing)
    monkeypatch.setattr(scan_content, "REPORT_DIR", tmp_path / "report")

    ret = scan_content.scan(wiki=True, corpus=False)
    assert ret == 0
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert (tmp_path / "report" / "zvs-scan-summary.md").exists()
