"""Async Gemini client with rate limiting, retry, and cost tracking."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_ENV_KEY_NAMES = (
    "GEMINI_API_KEY",
    "GEMINI_API_KEY_2",
    "GEMINI_API_KEY_3",
)


@dataclass(frozen=True)
class CallRecord:
    """Record of a single Gemini API call.

    Attributes:
        model: Model name used.
        input_tokens: Token count for the prompt.
        output_tokens: Token count for the completion.
        latency_ms: Wall-clock latency in milliseconds.
        success: Whether the call succeeded.
        error: Error message if the call failed.
        key_index: Index of the API key used (0-based).
    """

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    success: bool = True
    error: str = ""
    key_index: int = 0


class GeminiClient:
    """Async Gemini client with rate limiting, retry, and cost tracking.

    Usage::

        async with GeminiClient() as client:
            result = await client.generate_text("What is SOV word order?")
            print(result)

    The client automatically:
        - Rotates through up to 3 API keys from environment variables.
        - Applies exponential backoff on transient failures.
        - Enforces concurrency limits via semaphore.
        - Tracks token usage in ``call_history``.
    """

    def __init__(
        self,
        *,
        max_concurrent: int = 3,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        api_keys: list[str] | None = None,
    ) -> None:
        """Initialise the client.

        Args:
            max_concurrent: Maximum parallel API calls.
            max_retries: Maximum retry attempts per call.
            base_delay: Base delay (seconds) for exponential backoff.
            max_delay: Maximum delay (seconds) between retries.
            api_keys: Explicit API keys; ``None`` reads from env vars.
        """
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._call_history: list[CallRecord] = []
        self._key_index = 0
        self._client: Any = None  # lazy google.genai client
        self._api_keys = api_keys or self._load_keys_from_env()

    # ------------------------------------------------------------------
    # Key management
    # ------------------------------------------------------------------

    @staticmethod
    def _load_keys_from_env() -> list[str]:
        """Load API keys from environment variables."""
        keys: list[str] = []
        for name in _ENV_KEY_NAMES:
            val = os.environ.get(name, "").strip()
            if val:
                keys.append(val)
        return keys

    def _next_key(self) -> str | None:
        """Return the next API key in rotation, or ``None`` if none available."""
        if not self._api_keys:
            return None
        key = self._api_keys[self._key_index % len(self._api_keys)]
        self._key_index += 1
        return key

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------

    def _ensure_client(self) -> Any:
        """Lazily create the ``google.genai`` client."""
        if self._client is not None:
            return self._client

        try:
            from google import genai  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "google-genai is required: pip install 'zolai[gemini]'"
            ) from exc

        key = self._next_key()
        if key is None:
            raise RuntimeError(
                "No Gemini API keys found. Set GEMINI_API_KEY env var."
            )

        self._client = genai.Client(api_key=key)
        return self._client

    async def close(self) -> None:
        """Close the underlying client and release resources."""
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None

    # ------------------------------------------------------------------
    # Core generation methods
    # ------------------------------------------------------------------

    async def generate_text(self, prompt: str, *, model: str = "gemini-2.5-flash") -> str:
        """Generate plain text from a prompt.

        Args:
            prompt: The user prompt.
            model: Model name to use.

        Returns:
            The generated text.
        """
        async with self._semaphore:
            return await self._call_with_retry(
                model=model,
                prompt=prompt,
                response_schema=None,
            )

    async def generate_structured(
        self,
        prompt: str,
        schema: type,
        *,
        model: str = "gemini-2.5-flash",
    ) -> Any:
        """Generate a structured response conforming to *schema*.

        Args:
            prompt: The user prompt.
            schema: A Pydantic model class for output validation.
            model: Model name to use.

        Returns:
            An instance of *schema* parsed from the model response.
        """
        async with self._semaphore:
            raw = await self._call_with_retry(
                model=model,
                prompt=prompt,
                response_schema=schema,
            )
            return raw

    # ------------------------------------------------------------------
    # Retry logic
    # ------------------------------------------------------------------

    async def _call_with_retry(
        self,
        *,
        model: str,
        prompt: str,
        response_schema: type | None,
    ) -> Any:
        """Execute a Gemini call with exponential backoff.

        Args:
            model: Model identifier.
            prompt: The user prompt.
            response_schema: Optional Pydantic schema for structured output.

        Returns:
            The model response (str or parsed schema instance).

        Raises:
            RuntimeError: After exhausting all retries.
        """
        last_error: Exception | None = None

        for attempt in range(self._max_retries):
            start = time.monotonic()
            try:
                client = self._ensure_client()
                kwargs: dict[str, Any] = {"model": model, "contents": prompt}
                if response_schema is not None:
                    kwargs["response_schema"] = response_schema
                    kwargs["config"] = {"response_mime_type": "application/json"}

                response = await client.aio.models.generate_content(**kwargs)

                latency = (time.monotonic() - start) * 1000

                # Extract token counts from usage metadata
                usage = getattr(response, "usage_metadata", None) or {}
                input_tokens = getattr(usage, "prompt_token_count", 0) or 0
                output_tokens = getattr(usage, "candidates_token_count", 0) or 0

                record = CallRecord(
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency,
                    success=True,
                    key_index=(self._key_index - 1) % max(len(self._api_keys), 1),
                )
                self._call_history.append(record)

                # Return parsed schema or plain text
                if response_schema is not None:
                    return response.parsed
                return response.text

            except Exception as exc:
                last_error = exc
                latency = (time.monotonic() - start) * 1000
                record = CallRecord(
                    model=model,
                    latency_ms=latency,
                    success=False,
                    error=str(exc),
                    key_index=(self._key_index - 1) % max(len(self._api_keys), 1),
                )
                self._call_history.append(record)

                # Rotate key on auth errors
                if "401" in str(exc) or "403" in str(exc):
                    self._rotate_key()

                if attempt < self._max_retries - 1:
                    delay = min(self._base_delay * (2**attempt), self._max_delay)
                    logger.warning(
                        "Gemini call failed (attempt %d/%d): %s — retrying in %.1fs",
                        attempt + 1,
                        self._max_retries,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)

        raise RuntimeError(
            f"Gemini call failed after {self._max_retries} retries: {last_error}"
        )

    def _rotate_key(self) -> None:
        """Force rotation to the next API key."""
        if self._api_keys:
            self._key_index = (self._key_index + 1) % len(self._api_keys)
            self._client = None  # Force re-creation with new key

    # ------------------------------------------------------------------
    # Cost tracking
    # ------------------------------------------------------------------

    @property
    def call_history(self) -> list[CallRecord]:
        """Return the list of all call records."""
        return list(self._call_history)

    @property
    def total_input_tokens(self) -> int:
        """Sum of input tokens across all calls."""
        return sum(r.input_tokens for r in self._call_history if r.success)

    @property
    def total_output_tokens(self) -> int:
        """Sum of output tokens across all calls."""
        return sum(r.output_tokens for r in self._call_history if r.success)

    @property
    def total_calls(self) -> int:
        """Total number of API calls attempted."""
        return len(self._call_history)

    @property
    def failed_calls(self) -> int:
        """Number of failed API calls."""
        return sum(1 for r in self._call_history if not r.success)
