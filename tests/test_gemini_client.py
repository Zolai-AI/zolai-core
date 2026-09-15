"""Tests for the Gemini client — mock google.genai, verify rate limiting, retry, key rotation, cost tracking."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zolai.llm.gemini.client import CallRecord, GeminiClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_genai():
    """Mock the google.genai module."""
    with patch("zolai.llm.gemini.client.os.environ", {"GEMINI_API_KEY": "test-key-1"}):
        yield


@pytest.fixture
def client(mock_genai):
    """Create a GeminiClient with mocked environment."""
    return GeminiClient(max_concurrent=2, max_retries=3, base_delay=0.01, max_delay=0.1)


def _make_mock_client(response_text: str = "hello", side_effect=None):
    """Build a mock genai.Client whose aio.models.generate_content works."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = response_text
    mock_response.parsed = response_text
    mock_response.usage_metadata = MagicMock(
        prompt_token_count=10, candidates_token_count=5
    )

    mock_aio = MagicMock()
    if side_effect is not None:
        mock_aio.models.generate_content = AsyncMock(side_effect=side_effect)
    else:
        mock_aio.models.generate_content = AsyncMock(return_value=mock_response)
    mock_client.aio = mock_aio
    mock_client.close = AsyncMock()
    return mock_client


# ---------------------------------------------------------------------------
# Tests — Key rotation
# ---------------------------------------------------------------------------

class TestKeyRotation:
    def test_loads_keys_from_env(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "k1", "GEMINI_API_KEY_2": "k2"}):
            c = GeminiClient()
            assert c._api_keys == ["k1", "k2"]

    def test_no_keys_returns_empty(self):
        with patch.dict("os.environ", {}, clear=True):
            c = GeminiClient()
            assert c._api_keys == []

    def test_next_key_cycles(self):
        c = GeminiClient(api_keys=["k1", "k2", "k3"])
        assert c._next_key() == "k1"
        assert c._next_key() == "k2"
        assert c._next_key() == "k3"
        assert c._next_key() == "k1"  # wraps around

    def test_rotate_key_forces_next(self):
        c = GeminiClient(api_keys=["k1", "k2"])
        assert c._next_key() == "k1"
        c._rotate_key()
        # After _next_key: _key_index=1. _rotate_key: _key_index=(1+1)%2=0, client=None
        assert c._client is None  # client reset

    def test_next_key_none_when_empty(self):
        c = GeminiClient(api_keys=[])
        assert c._next_key() is None


# ---------------------------------------------------------------------------
# Tests — Cost tracking
# ---------------------------------------------------------------------------

class TestCostTracking:
    def test_initial_state(self, client):
        assert client.total_calls == 0
        assert client.failed_calls == 0
        assert client.total_input_tokens == 0
        assert client.total_output_tokens == 0
        assert client.call_history == []

    def test_record_tracking(self, client):
        client._call_history.append(
            CallRecord(model="flash", input_tokens=100, output_tokens=50, success=True)
        )
        client._call_history.append(
            CallRecord(model="flash", input_tokens=0, output_tokens=0, success=False, error="timeout")
        )
        assert client.total_calls == 2
        assert client.failed_calls == 1
        assert client.total_input_tokens == 100
        assert client.total_output_tokens == 50

    def test_call_history_returns_copy(self, client):
        client._call_history.append(CallRecord(model="flash"))
        history = client.call_history
        history.clear()
        assert len(client.call_history) == 1  # original unaffected


# ---------------------------------------------------------------------------
# Tests — Semaphore / concurrency
# ---------------------------------------------------------------------------

class TestSemaphore:
    def test_semaphore_created(self, client):
        assert client._semaphore._value == 2  # max_concurrent=2

    def test_custom_concurrency(self):
        c = GeminiClient(max_concurrent=5)
        assert c._semaphore._value == 5


# ---------------------------------------------------------------------------
# Tests — Retry logic (mocked)
# ---------------------------------------------------------------------------

class TestRetry:
    @pytest.mark.asyncio
    async def test_success_on_first_try(self, client):
        client._client = _make_mock_client("hello")
        result = await client.generate_text("test prompt")
        assert result == "hello"
        assert client.total_calls == 1
        assert client.failed_calls == 0

    @pytest.mark.asyncio
    async def test_retry_on_failure(self, client):
        mock_response = MagicMock()
        mock_response.text = "ok"
        mock_response.usage_metadata = MagicMock(
            prompt_token_count=10, candidates_token_count=5
        )
        client._client = _make_mock_client(
            side_effect=[RuntimeError("transient"), RuntimeError("transient"), mock_response]
        )
        result = await client.generate_text("test prompt")
        assert result == "ok"
        assert client.total_calls == 3

    @pytest.mark.asyncio
    async def test_exhausted_retries_raises(self, client):
        client._client = _make_mock_client(
            side_effect=RuntimeError("permanent failure")
        )
        with pytest.raises(RuntimeError, match="failed after"):
            await client.generate_text("test prompt")
        assert client.total_calls == 3  # max_retries=3 (3 attempts)

    @pytest.mark.asyncio
    async def test_auth_error_rotates_key(self, client):
        client._api_keys = ["key1", "key2"]
        mock_client = _make_mock_client(
            side_effect=[
                Exception("401 unauthorized"),
                MagicMock(
                    text="ok",
                    usage_metadata=MagicMock(
                        prompt_token_count=0, candidates_token_count=0
                    ),
                ),
            ]
        )
        client._client = mock_client
        client._key_index = 0

        # Patch _ensure_client so that after rotation (which sets _client=None),
        # the retry still gets our mock client.
        original_ensure = client._ensure_client

        def patched_ensure():
            if client._client is None:
                client._client = mock_client
            return original_ensure()

        client._ensure_client = patched_ensure

        # Should rotate key on auth error — after retry, call succeeds
        result = await client.generate_text("test")
        assert result == "ok"
        assert client.total_calls == 2  # one failure + one success
        assert client.failed_calls == 1  # first call failed with 401


# ---------------------------------------------------------------------------
# Tests — Close
# ---------------------------------------------------------------------------

class TestClose:
    @pytest.mark.asyncio
    async def test_close_when_no_client(self, client):
        await client.close()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_close_with_client(self, client):
        mock_client = AsyncMock()
        mock_client.close = AsyncMock()
        client._client = mock_client
        await client.close()
        mock_client.close.assert_called_once()
        assert client._client is None


# ---------------------------------------------------------------------------
# Tests — Import error
# ---------------------------------------------------------------------------

class TestImportError:
    @pytest.mark.asyncio
    async def test_raises_on_missing_sdk(self):
        c = GeminiClient(api_keys=["dummy"])
        with patch.dict("sys.modules", {"google": None, "google.genai": None}):
            with pytest.raises(ImportError, match="google-genai"):
                c._ensure_client()
