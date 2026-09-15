"""Model configurations and routing for Gemini models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FactType = Literal["word", "sentence", "grammar", "translation"]
Complexity = Literal["low", "medium", "high"]
EvidenceTier = Literal["dictionary", "bible", "corpus", "none"]


@dataclass(frozen=True)
class ModelConfig:
    """Configuration for a Gemini model variant.

    Attributes:
        name: Gemini model identifier (e.g. ``"gemini-2.5-flash"``).
        temperature: Sampling temperature.
        max_tokens: Maximum output tokens.
        use_case: Short description of intended usage.
    """

    name: str
    temperature: float = 0.2
    max_tokens: int = 4096
    use_case: str = ""


# ---------------------------------------------------------------------------
# Model constants
# ---------------------------------------------------------------------------

FLASH = ModelConfig(
    name="gemini-2.5-flash",
    temperature=0.1,
    max_tokens=2048,
    use_case="Fast verification — word-level checks, simple sentences",
)

PRO = ModelConfig(
    name="gemini-2.5-pro",
    temperature=0.2,
    max_tokens=4096,
    use_case="Balanced — grammar analysis, sentence verification",
)

PRO_PLUS = ModelConfig(
    name="gemini-2.5-pro",
    temperature=0.3,
    max_tokens=8192,
    use_case="Deep analysis — complex sentences, multi-evidence reasoning",
)


class ModelRouter:
    """Select the cheapest capable model for a given verification task.

    Routing logic:
        - Word-level with dictionary evidence → FLASH
        - Sentence with Bible evidence → PRO
        - Grammar or high-complexity → PRO_PLUS
        - Default fallback → PRO
    """

    def __init__(
        self,
        flash: ModelConfig = FLASH,
        pro: ModelConfig = PRO,
        pro_plus: ModelConfig = PRO_PLUS,
    ) -> None:
        self._flash = flash
        self._pro = pro
        self._pro_plus = pro_plus

    def route(
        self,
        fact_type: FactType,
        complexity: Complexity = "medium",
        evidence_tiers: tuple[EvidenceTier, ...] = (),
    ) -> ModelConfig:
        """Route to the cheapest model that can handle the task.

        Args:
            fact_type: Type of fact being verified.
            complexity: Estimated complexity of the input.
            evidence_tiers: Which evidence sources are available.

        Returns:
            The selected :class:`ModelConfig`.
        """
        # High complexity always uses pro-plus
        if complexity == "high":
            return self._pro_plus

        # Grammar analysis needs pro-plus for rule tracking
        if fact_type == "grammar":
            return self._pro_plus

        # Word-level with dictionary evidence can use flash
        if fact_type == "word" and complexity != "high":
            if "dictionary" in evidence_tiers or "bible" in evidence_tiers:
                return self._flash

        # Default: pro for balanced work
        if fact_type == "sentence":
            return self._pro

        return self._pro
