"""Integration test: validate ZVS 2018 compliance in core library source.

This ensures new code added to zolai/ is ZVS-compliant — no forbidden forms
in comments, docstrings, or string literals within the core library.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from zolai.zvs import ExceptionRegistry, validate

# Root of the zolai package (relative to this test file's repo root)
ZOLAI_ROOT = Path(__file__).resolve().parent.parent / "zolai"

# Strict empty registry — no historical exceptions for core library code.
_STRICT = ExceptionRegistry()


def _extract_python_text(path: Path) -> list[tuple[str, str]]:
    """Extract docstrings and comments from a Python file.

    Returns a list of (label, text) tuples where label identifies the source
    (e.g. 'module docstring', 'function foo docstring', 'line 42 comment').
    """
    source = path.read_text(encoding="utf-8")
    results: list[tuple[str, str]] = []

    # --- docstrings via AST ---
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        # Skip files that can't be parsed
        return results

    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            docstring = ast.get_docstring(node, clean=True)
            if docstring:
                label = f"{type(node).__name__} {getattr(node, 'name', '<module>')} docstring"
                results.append((label, docstring))

    # --- inline comments ---
    for i, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            comment = stripped[1:].strip()
            if comment:
                results.append((f"line {i} comment", comment))

    return results


def _collect_violations(path: Path) -> list[dict]:
    """Return ZVS violations found in docstrings/comments of *path*."""
    violations: list[dict] = []
    for label, text in _extract_python_text(path):
        report = validate(text, source=str(path), exceptions=_STRICT)
        for v in report.violations:
            violations.append({
                "file": str(path),
                "location": label,
                "forbidden": v.forbidden,
                "preferred": v.preferred,
                "message": v.message,
            })
    return violations


# Collect all .py files under zolai/ once (parameterize over them).
# Exclude zolai/zvs/ — it is the rule source-of-truth and must reference
# the forbidden forms as part of its documentation and rule definitions.
_EXCLUDE_DIRS = {"zvs", "__pycache__"}


def _is_excluded(path: Path) -> bool:
    """Return True if *path* lives under any excluded subdirectory."""
    return any(part in _EXCLUDE_DIRS for part in path.relative_to(ZOLAI_ROOT).parts)


_all_py_files = sorted(
    f for f in ZOLAI_ROOT.rglob("*.py") if not _is_excluded(f)
)


@pytest.mark.parametrize("pyfile", _all_py_files, ids=lambda p: str(p.relative_to(ZOLAI_ROOT)))
def test_core_library_zvs_compliance(pyfile: Path) -> None:
    """Each Python file in zolai/ must have no ZVS 2018 violations."""
    violations = _collect_violations(pyfile)
    if violations:
        details = "\n".join(
            f"  {v['location']}: '{v['forbidden']}' -> '{v['preferred']}' ({v['message']})"
            for v in violations
        )
        pytest.fail(
            f"ZVS 2018 violations in {pyfile.relative_to(ZOLAI_ROOT)}:\n{details}"
        )
