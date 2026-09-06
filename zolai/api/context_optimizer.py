"""
Context Injection Optimizer — token-efficient context for any model size.

Works with zero-knowledge to expert models.
Adapts context depth based on model capability.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class ModelProfile:
    """Model capability profile."""
    name: str
    max_context_tokens: int
    tier: str  # 'tiny', 'small', 'medium', 'large', 'expert'

    @classmethod
    def for_model(cls, model_name: str) -> 'ModelProfile':
        """Auto-detect model profile from name."""
        name_lower = model_name.lower()

        # Tiny models (1B or less)
        if any(x in name_lower for x in ['1b', 'tiny', 'micro']):
            return cls(model_name, 1024, 'tiny')

        # Small models (1-3B)
        if any(x in name_lower for x in ['3b', 'small', 'qwen2.5-3', 'phi-3']):
            return cls(model_name, 2048, 'small')

        # Medium models (3-8B)
        if any(x in name_lower for x in ['7b', '8b', 'medium', 'mistral', 'llama-3']):
            return cls(model_name, 4096, 'medium')

        # Large models (8-13B)
        if any(x in name_lower for x in ['13b', 'large', 'qwen-14', 'mixtral']):
            return cls(model_name, 8192, 'large')

        # Expert models (13B+)
        if any(x in name_lower for x in ['70b', 'expert', 'gpt', 'claude', 'gemini']):
            return cls(model_name, 32768, 'expert')

        # Default: medium
        return cls(model_name, 4096, 'medium')


class ContextOptimizer:
    """Optimize context injection for different model sizes."""

    # Token budgets per tier (% of max context)
    TIER_BUDGETS = {
        'tiny': 0.15,    # 15% for context
        'small': 0.20,   # 20%
        'medium': 0.25,  # 25%
        'large': 0.30,   # 30%
        'expert': 0.40,  # 40%
    }

    # Context priority by tier
    TIER_PRIORITIES = {
        'tiny': ['rules', 'dictionary'],
        'small': ['rules', 'dictionary', 'phrases'],
        'medium': ['rules', 'dictionary', 'phrases', 'grammar'],
        'large': ['rules', 'dictionary', 'phrases', 'grammar', 'bible'],
        'expert': ['rules', 'dictionary', 'phrases', 'grammar', 'bible', 'patterns'],
    }

    def __init__(self, model_name: str = ""):
        self.profile = ModelProfile.for_model(model_name)
        self.token_budget = int(self.profile.max_context_tokens * self.TIER_BUDGETS[self.profile.tier])

    def optimize_context(self, user_input: str, available_context: dict[str, str]) -> str:
        """
        Optimize context for the given model.

        Args:
            user_input: User's input text
            available_context: Dict of context_name -> context_text

        Returns:
            Optimized context string within token budget
        """
        priorities = self.TIER_PRIORITIES[self.profile.tier]

        # Estimate tokens for each context piece
        context_pieces = []
        total_tokens = 0

        for priority in priorities:
            if priority in available_context:
                context_text = available_context[priority]
                estimated_tokens = self._estimate_tokens(context_text)

                if total_tokens + estimated_tokens <= self.token_budget:
                    context_pieces.append(context_text)
                    total_tokens += estimated_tokens
                else:
                    # Truncate to fit
                    remaining = self.token_budget - total_tokens
                    truncated = self._truncate_to_tokens(context_text, remaining)
                    if truncated:
                        context_pieces.append(truncated)
                    break

        return "\n\n".join(context_pieces)

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count (rough: 1 token ≈ 4 chars)."""
        return len(text) // 4

    def _truncate_to_tokens(self, text: str, max_tokens: int) -> str:
        """Truncate text to approximate token limit."""
        max_chars = max_tokens * 4
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n... [truncated]"

    def get_profile(self) -> dict:
        """Get current model profile."""
        return {
            'name': self.profile.name,
            'max_context_tokens': self.profile.max_context_tokens,
            'tier': self.profile.tier,
            'token_budget': self.token_budget,
            'priorities': self.TIER_PRIORITIES[self.profile.tier],
        }


# Global instance
_optimizer: Optional[ContextOptimizer] = None


def get_context_optimizer(model_name: str = "") -> ContextOptimizer:
    """Get or create context optimizer."""
    global _optimizer
    if _optimizer is None or _optimizer.profile.name != model_name:
        _optimizer = ContextOptimizer(model_name)
    return _optimizer
