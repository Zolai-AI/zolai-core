"""Report data structures for the ZVS 2018 validator.

``Violation`` describes a single rule break; ``Report`` aggregates the
violations for one piece of text and supports JSON serialisation so the results
are machine-readable for CI and downstream tooling.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Violation:
    """A single ZVS 2018 rule violation."""

    rule_id: str
    category: str
    forbidden: str
    preferred: str | None
    message: str
    start: int | None = None
    end: int | None = None
    context: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Report:
    """Validation result for a single source snippet."""

    source: str = "<text>"
    text: str = ""
    violations: list[Violation] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """True when no violations were found."""
        return not self.violations

    @property
    def count(self) -> int:
        return len(self.violations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "valid": self.is_valid,
            "violation_count": self.count,
            "text": self.text,
            "violations": [v.to_dict() for v in self.violations],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        """Machine-readable JSON encoding of the report."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def __str__(self) -> str:
        """Human-readable rendering used by ``print(report)``."""
        if self.is_valid:
            return f"[PASS] {self.source} — no ZVS violations"
        lines = [f"[FAIL] {self.source} — {self.count} ZVS violation(s)"]
        for i, v in enumerate(self.violations, 1):
            suggestion = f" → use '{v.preferred}'" if v.preferred else ""
            lines.append(
                f"  {i}. ({v.rule_id}/{v.category}) {v.message} "
                f"'{v.forbidden}'{suggestion}"
            )
        return "\n".join(lines)
