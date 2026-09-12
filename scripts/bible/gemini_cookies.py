#!/usr/bin/env python3
"""
gemini_cookies.py — Auto-import Gemini cookies from Chrome browser.

Provides get_gemini_client() for Gemini Web API access.
Used by translation_validator.py, gemini_webapi_setup.py, and learning_orchestrator.py.

Requirements:
    pip install gemini-webapi browser-cookie3

Usage:
    from gemini_cookies import get_gemini_client
    client = get_gemini_client()
    await client.init(timeout=30, auto_close=True, close_delay=60)
    response = await client.generate_content("Hello")
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def get_gemini_client() -> Any:
    """Get a GeminiClient with auto-imported Chrome cookies.

    Returns:
        GeminiClient instance ready for init()

    Raises:
        ImportError: if gemini_webapi or browser_cookie3 not installed
    """
    try:
        from gemini_webapi import GeminiClient
    except ImportError:
        raise ImportError(
            "gemini_webapi not installed. Run: pip install gemini-webapi"
        )

    try:
        import browser_cookie3
    except ImportError:
        raise ImportError(
            "browser_cookie3 not installed. Run: pip install browser-cookie3"
        )

    # Auto-import cookies from Chrome
    try:
        cj = browser_cookie3.chrome(domain_name=".google.com")
        client = GeminiClient(cookies=cj)
        log.info("Gemini client created with Chrome cookies")
        return client
    except Exception as e:
        log.warning(f"Failed to load Chrome cookies: {e}")
        # Fallback: create client without cookies (may require manual auth)
        client = GeminiClient()
        log.info("Gemini client created without cookies (manual auth required)")
        return client
