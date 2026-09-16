"""
Gemini Ensemble — Parallel dispatch to N models with majority vote.

Uses asyncio.gather for parallel dispatch, staggered by 200ms to avoid
rate limits. Collects responses and runs fuzzy majority voting.
"""

import asyncio
import logging
from difflib import SequenceMatcher

import httpx

logger = logging.getLogger(__name__)


class GeminiEnsemble:
    """Dispatch messages to multiple Gemini models and return majority-vote result."""

    def __init__(self, models: list[str], base_url: str = "http://localhost:8000"):
        """
        Args:
            models: List of model identifiers to dispatch to.
            base_url: URL of the local gemini-webapi proxy (not used directly;
                       kept for future routing).
        """
        self.models = models
        self.base_url = base_url

    async def dispatch(
        self,
        message: str,
        system_prompt: str,
        n_models: int = 3,
    ) -> dict:
        """
        Dispatch to up to n_models in parallel with 200ms stagger.

        Returns:
            {
                "response": str,
                "confidence": float,
                "models_used": list[str],
                "agreement_count": int,
                "all_responses": dict[str, str],
            }
        """
        targets = self.models[:n_models]
        responses: dict[str, str] = {}
        errors: dict[str, str] = {}

        async def _call_model(model: str, delay: float) -> None:
            """Call a single model after a stagger delay."""
            await asyncio.sleep(delay)
            try:
                # Use httpx to call the local gemini-webapi proxy
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        f"{self.base_url}/chat/gemini",
                        json={
                            "message": message,
                            "model": model,
                            "session_id": f"ensemble-{model}",
                        },
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        text = data.get("zolai_response", "").strip()
                        if text:
                            responses[model] = text
                            return
                    errors[model] = f"HTTP {resp.status_code}"
            except asyncio.TimeoutError:
                errors[model] = "timeout"
            except Exception as e:
                errors[model] = str(e)
            logger.warning("Ensemble model %s failed: %s", model, errors.get(model, "unknown"))

        # Dispatch with staggered delays
        tasks = [
            _call_model(model, i * 0.2)
            for i, model in enumerate(targets)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        if not responses:
            return {
                "response": "",
                "confidence": 0.0,
                "models_used": [],
                "agreement_count": 0,
                "all_responses": {},
            }

        winner, confidence, agreement = self._majority_vote(list(responses.values()))

        return {
            "response": winner,
            "confidence": confidence,
            "models_used": list(responses.keys()),
            "agreement_count": agreement,
            "all_responses": responses,
        }

    def _majority_vote(self, responses: list[str]) -> tuple[str, float, int]:
        """
        Group similar responses using fuzzy matching and pick the largest group.

        Args:
            responses: List of response strings from different models.

        Returns:
            (winner_text, confidence, agreement_count)
        """
        if not responses:
            return "", 0.0, 0

        if len(responses) == 1:
            return responses[0], 1.0, 1

        # Group similar responses (SequenceMatcher ratio > 0.7)
        groups: list[list[str]] = []
        for resp in responses:
            placed = False
            for group in groups:
                # Compare against first member of group
                ratio = SequenceMatcher(None, resp, group[0]).ratio()
                if ratio > 0.7:
                    group.append(resp)
                    placed = True
                    break
            if not placed:
                groups.append([resp])

        # Pick largest group
        winner_group = max(groups, key=len)
        winner = winner_group[0]
        confidence = len(winner_group) / len(responses)

        return winner, confidence, len(winner_group)
