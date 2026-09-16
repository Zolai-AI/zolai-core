"""Cost tracking service for LLM API usage."""

from __future__ import annotations

import uuid
from typing import Any

from zolai.data.repositories.foundation import FoundationCostTrackingRepository


class CostTracker:
    """Service for tracking and budgeting LLM API costs.

    Wraps FoundationCostTrackingRepository with pricing logic and
    token estimation helpers.
    """

    # Default Gemini pricing (USD per 1M tokens)
    DEFAULT_MODEL_PRICING: dict[str, tuple[float, float]] = {
        "gemini-2.0-flash": (0.10, 0.40),
        "gemini-2.0-pro": (1.25, 10.00),
        "gemini-2.0-pro-plus": (2.50, 15.00),
        "approx": (0.0, 0.0),
    }

    def __init__(
        self,
        repo: FoundationCostTrackingRepository,
        model_pricing: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        """Initialize CostTracker.

        Args:
            repo: Repository for persisting cost records.
            model_pricing: Dict mapping model name to (input_per_m, output_per_m)
                pricing in USD per 1M tokens. Falls back to DEFAULT_MODEL_PRICING.
        """
        self.repo = repo
        self.model_pricing = model_pricing or self.DEFAULT_MODEL_PRICING

    def log(
        self,
        task_type: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        request_id: str | None = None,
        extra_info: str | None = None,
    ) -> float:
        """Log an LLM API call and return the computed cost.

        Args:
            task_type: Type of task ('word', 'sentence', 'paragraph', 'grammar', 'batch').
            model: Model name used.
            input_tokens: Number of input tokens.
            output_tokens: Number of output tokens.
            request_id: Optional UUID (auto-generated if not provided).
            extra_info: Optional JSON metadata.

        Returns:
            Cost in USD for this request.
        """
        if request_id is None:
            request_id = str(uuid.uuid4())

        cost_usd = self._compute_cost(model, input_tokens, output_tokens)

        self.repo.log_request(
            request_id=request_id,
            task_type=task_type,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            extra_info=extra_info,
        )

        return cost_usd

    def _compute_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Compute cost in USD for a given model and token counts.

        Args:
            model: Model name.
            input_tokens: Number of input tokens.
            output_tokens: Number of output tokens.

        Returns:
            Cost in USD.
        """
        if input_tokens == 0 and output_tokens == 0:
            return 0.0

        pricing = self.model_pricing.get(model, (0.0, 0.0))
        input_per_m, output_per_m = pricing
        cost = (input_tokens * input_per_m + output_tokens * output_per_m) / 1_000_000
        return round(cost, 8)

    def get_summary(
        self, start_date: str | None = None, end_date: str | None = None
    ) -> dict[str, Any]:
        """Get cost summary for a date range.

        Args:
            start_date: ISO date string for start of range (inclusive).
            end_date: ISO date string for end of range (inclusive).

        Returns:
            Dict with total_cost, by_task, by_model, daily breakdown.
        """
        return self.repo.get_summary(start_date=start_date, end_date=end_date)

    def check_budget(self, budget_usd: float) -> tuple[bool, float]:
        """Check if current month's spend is within budget.

        Args:
            budget_usd: Monthly budget in USD.

        Returns:
            Tuple of (within_budget, current_spend).
        """
        return self.repo.check_budget(budget_usd)

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estimate token count from text using character-based approximation.

        Approximation: ~4 characters per token for English, ~2 for CJK.
        Uses a blended estimate of ~4 chars per token for mixed content.

        Args:
            text: Input text.

        Returns:
            Estimated token count.
        """
        if not text:
            return 0
        return max(1, len(text) // 4)
