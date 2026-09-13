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
    """A seeded historical token (bawipa) is in DEFAULT_EXCEPTIONS but NOT suppressed unless in Bible."""
    assert "bawipa" in DEFAULT_EXCEPTIONS.tokens
    # "bawipa" is in DEFAULT_EXCEPTIONS but does NOT appear in Bible database (Bible uses "topa"),
    # so it is NOT suppressed and is flagged as a violation.
    report = validate("Bawipa hia.")
    assert not report.is_valid


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
    """CLI --use-default-exceptions with Bible-only check for historical tokens."""
    f = tmp_path / "seeded.txt"
    f.write_text("Bawipa hia.\n", encoding="utf-8")

    # Default CLI run has an EMPTY registry, so the seeded token is flagged.
    ret_default = cli_main(["validate", str(f)])
    assert ret_default == 1

    # With --use-default-exceptions, the seeded token is STILL flagged
    # because "bawipa" does not appear in the Bible database (Bible uses "topa").
    # Historical tokens in DEFAULT_EXCEPTIONS are only suppressed if they appear in Bible.
    ret_defaults = cli_main(
        ["validate", "--use-default-exceptions", str(f)]
    )
    assert ret_defaults == 1

    # Library parity: validate() itself uses DEFAULT_EXCEPTIONS by default.
    # "bawipa" is not in Bible database, so it is flagged even with defaults.
    assert not validate("Bawipa hia.").is_valid

