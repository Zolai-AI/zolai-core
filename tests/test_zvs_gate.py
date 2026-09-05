"""Regression-gate tests for the ZVS content scanner.

These cover the ``--gate`` behaviour of ``scripts/zvs/scan_content.py``: the
gate must exit 1 *only* when a ``(source, rule_id, forbidden)`` combo is NEW or
its count exceeds the committed baseline. Existing counts never fail, so
reference content (vocabulary/, generated, stem tables) is auto-excluded simply
by having been captured in the baseline.

A small fake wiki + a temp baseline are used so the real 1,545-file wiki is
never scanned during tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import scripts.zvs.scan_content as scan_content

# A reliable, unseeded forbidden form (ram -> gam, DIALECT_02).
_FORBIDDEN = "ram"
_VIOLATION = f"{_FORBIDDEN} a tam hi.\n"
_CLEAN = "Pasian gam a tam hi.\n"


def _make_wiki(tmp_path: Path, files: dict[str, str]) -> Path:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    for rel, text in files.items():
        path = wiki / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return wiki


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wiki: Path) -> Path:
    report_dir = tmp_path / "report"
    monkeypatch.setattr(scan_content, "DEFAULT_WIKI", wiki)
    monkeypatch.setattr(scan_content, "REPORT_DIR", report_dir)
    return report_dir


def _baseline_file(tmp_path: Path) -> Path:
    return tmp_path / "baseline.json"


def _write_baseline(path: Path) -> int:
    return scan_content.main(["--wiki", "--write-baseline", str(path)])


def test_gate_passes_when_scan_equals_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean wiki, standardized into a baseline, gates cleanly."""
    wiki = _make_wiki(tmp_path, {"one.md": _CLEAN})
    _setup(tmp_path, monkeypatch, wiki)
    baseline = _baseline_file(tmp_path)

    assert _write_baseline(baseline) == 0
    assert scan_content.main(["--wiki", "--gate", "--baseline", str(baseline)]) == 0


def test_gate_fails_on_new_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A NEW (source, rule, forbidden) combo not in the baseline trips the gate."""
    wiki = _make_wiki(tmp_path, {"ok.md": _CLEAN})
    _setup(tmp_path, monkeypatch, wiki)
    baseline = _baseline_file(tmp_path)
    assert _write_baseline(baseline) == 0

    # Introduce a violation in a file that was clean in the baseline.
    (wiki / "ok.md").write_text(_VIOLATION, encoding="utf-8")
    assert scan_content.main(["--wiki", "--gate", "--baseline", str(baseline)]) == 1


def test_gate_fails_when_count_exceeds_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Increasing the count of an existing combo trips the gate."""
    wiki = _make_wiki(tmp_path, {"one.md": _VIOLATION})
    _setup(tmp_path, monkeypatch, wiki)
    baseline = _baseline_file(tmp_path)
    assert _write_baseline(baseline) == 0

    # Double the violation in the SAME source file (count 1 -> 2).
    (wiki / "one.md").write_text(_VIOLATION + _VIOLATION, encoding="utf-8")
    assert scan_content.main(["--wiki", "--gate", "--baseline", str(baseline)]) == 1


def test_reference_content_never_fails_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reference content (vocabulary/) is passed through via the baseline.

    Once reference content is captured in the baseline, the gate must not fail
    even though violations live under vocabulary/.
    """
    wiki = _make_wiki(
        tmp_path, {"vocabulary/forbidden_stems_auto.md": _VIOLATION}
    )
    _setup(tmp_path, monkeypatch, wiki)
    baseline = _baseline_file(tmp_path)

    # Standardize the reference content into the baseline...
    assert _write_baseline(baseline) == 0
    # ...then the gate passes despite the reference violation.
    assert scan_content.main(["--wiki", "--gate", "--baseline", str(baseline)]) == 0


def test_default_no_gate_exits_zero_always(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without --gate the scan is report-only and exits 0 even with violations."""
    wiki = _make_wiki(tmp_path, {"bad.md": _VIOLATION})
    _setup(tmp_path, monkeypatch, wiki)
    assert scan_content.main(["--wiki"]) == 0


def test_gate_missing_baseline_is_hard_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing baseline under --gate is a hard 1 (the gate cannot validate)."""
    wiki = _make_wiki(tmp_path, {"one.md": _CLEAN})
    _setup(tmp_path, monkeypatch, wiki)
    missing = tmp_path / "nope.json"
    assert not missing.exists()
    assert scan_content.main(["--wiki", "--gate", "--baseline", str(missing)]) == 1
