"""Browser cookie integration for Gemini WebAPI.

Extracts session cookies from Chrome/Chromium to authenticate with
gemini-webapi without requiring a separate API key. Falls back to
GEMINI_API_KEY env var when cookies are unavailable.

Usage::

    from zolai.llm.gemini.cookies import get_gemini_client
    client = await get_gemini_client()
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Cache directory for cookies
_CACHE_DIR = Path.home() / ".cache" / "zolai" / "gemini"
_COOKIE_CACHE_FILE = _CACHE_DIR / "cookies.json"
_COOKIE_TTL_SECONDS = 3600  # Re-extract cookies after 1 hour


def _extract_cookies_from_browser() -> dict[str, str] | None:
    """Try to extract Gemini cookies from installed browsers.

    Checks Chrome, Chromium, and Brave in order. Returns a dict of
    cookie name→value pairs, or None if extraction fails.
    """
    try:
        import browser_cookie3  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            "browser-cookie3 not installed. "
            "Install with: pip install browser-cookie3"
        )
        return None

    # Browsers to try in priority order
    browser_fns = [
        ("chrome", browser_cookie3.chrome),
        ("chromium", browser_cookie3.chromium),
        ("brave", browser_cookie3.brave),
    ]

    gemini_cookies: dict[str, str] = {}
    target_domains = [".google.com", "gemini.google.com"]

    for name, fn in browser_fns:
        try:
            jar = fn(domain_name=".google.com")
            for cookie in jar:
                # Keep only Gemini-relevant cookies
                if any(d in cookie.domain for d in target_domains):
                    gemini_cookies[cookie.name] = cookie.value
            if gemini_cookies:
                logger.info("Extracted %d cookies from %s", len(gemini_cookies), name)
                return gemini_cookies
        except Exception as e:
            logger.debug("Could not extract cookies from %s: %s", name, e)
            continue

    logger.warning("No Gemini cookies found in any browser")
    return None


def _load_cached_cookies() -> dict[str, str] | None:
    """Load cookies from cache if still valid."""
    if not _COOKIE_CACHE_FILE.exists():
        return None

    try:
        data = json.loads(_COOKIE_CACHE_FILE.read_text())
        extracted_at = data.get("extracted_at", 0)
        if time.time() - extracted_at > _COOKIE_TTL_SECONDS:
            logger.debug("Cookie cache expired")
            return None
        cookies = data.get("cookies", {})
        if cookies:
            logger.debug("Loaded %d cached cookies", len(cookies))
        return cookies
    except Exception as e:
        logger.debug("Failed to load cookie cache: %s", e)
        return None


def _save_cookie_cache(cookies: dict[str, str]) -> None:
    """Persist cookies to disk for reuse."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "cookies": cookies,
            "extracted_at": time.time(),
        }
        _COOKIE_CACHE_FILE.write_text(json.dumps(data, indent=2))
        logger.debug("Saved %d cookies to cache", len(cookies))
    except Exception as e:
        logger.debug("Failed to save cookie cache: %s", e)


def get_gemini_cookies() -> dict[str, str] | None:
    """Get Gemini cookies, using cache when available.

    Returns:
        Dict of cookie name→value, or None if no cookies available.
    """
    # Try cache first
    cached = _load_cached_cookies()
    if cached:
        return cached

    # Extract fresh cookies
    cookies = _extract_cookies_from_browser()
    if cookies:
        _save_cookie_cache(cookies)

    return cookies


def get_gemini_api_key() -> str | None:
    """Get Gemini API key from environment as fallback."""
    return os.environ.get("GEMINI_API_KEY")


async def get_gemini_client() -> Any:
    """Get a gemini-webapi client, preferring cookies over API key.

    Returns:
        An initialized gemini-webapi client instance.

    Raises:
        ImportError: If gemini-webapi is not installed.
        RuntimeError: If neither cookies nor API key are available.
    """
    try:
        from gemini_webapi import Client  # type: ignore[import-untyped]
    except ImportError:
        raise ImportError(
            "gemini-webapi not installed. "
            "Install with: pip install gemini-webapi"
        )

    cookies = get_gemini_cookies()
    api_key = get_gemini_api_key()

    if cookies:
        logger.info("Initializing Gemini client with browser cookies")
        client = Client(cookies=cookies)
        return client

    if api_key:
        logger.info("Initializing Gemini client with API key")
        client = Client(api_key=api_key)
        return client

    raise RuntimeError(
        "No Gemini credentials available. "
        "Either:\n"
        "  1. Log in to Gemini in Chrome/Chromium/Brave, or\n"
        "  2. Set GEMINI_API_KEY environment variable."
    )


def clear_cookie_cache() -> bool:
    """Clear the cached cookies. Returns True if cache existed."""
    if _COOKIE_CACHE_FILE.exists():
        _COOKIE_CACHE_FILE.unlink()
        logger.info("Cookie cache cleared")
        return True
    return False
