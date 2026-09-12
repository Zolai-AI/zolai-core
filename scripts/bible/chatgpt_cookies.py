#!/usr/bin/env python3
"""
chatgpt_cookies.py — Auto-import ChatGPT cookies from Chrome browser.

Provides get_chatgpt_client() for ChatGPT API access.
Uses browser-cookie3 to extract session cookies from Chrome.

Requirements:
    pip install browser-cookie3 httpx

Usage:
    from chatgpt_cookies import get_chatgpt_client, chatgpt_query

    # Direct query
    response = await chatgpt_query("Fix this Zolai sentence: Ka pathian in ram a piangsak hi.")

    # Or use client directly
    client = get_chatgpt_client()
    response = await client.ask("What is Tedim Zolai?")
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

log = logging.getLogger(__name__)

# ChatGPT API endpoints
CHATGPT_API = "https://chatgpt.com/backend-api"
CHATGPT_CONVERSATION = f"{CHATGPT_API}/conversation"
CHATGPT_MODELS = f"{CHATGPT_API}/models"


class ChatGPTClient:
    """ChatGPT client using browser cookies for authentication."""

    def __init__(self, cookies: Any = None):
        self.cookies = cookies
        self.session = None
        self.access_token = None
        self.expires_at = 0

    async def init(self, timeout: int = 30) -> None:
        """Initialize the client and refresh access token."""
        import httpx

        self.session = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
        )

        if self.cookies:
            # Convert cookie jar to dict
            cookie_dict = {}
            for cookie in self.cookies:
                cookie_dict[cookie.name] = cookie.value
            self.session.cookies.update(cookie_dict)

        # Get fresh access token
        await self._refresh_token()

    async def _refresh_token(self) -> None:
        """Refresh the ChatGPT access token."""
        try:
            resp = await self.session.get(
                "https://chatgpt.com/api/auth/session",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if resp.status_code == 200:
                data = resp.json()
                self.access_token = data.get("accessToken")
                self.expires_at = data.get("expires", 0)
                log.info("ChatGPT access token refreshed")
            else:
                log.warning(f"Failed to refresh token: {resp.status_code}")
        except Exception as e:
            log.warning(f"Token refresh failed: {e}")

    async def generate_content(
        self,
        prompt: str,
        model: str = "auto",
        system_prompt: str | None = None,
    ) -> str:
        """Send a prompt to ChatGPT and return the response.

        Args:
            prompt: User message
            model: Model to use (auto, gpt-4o, gpt-4o-mini, etc.)
            system_prompt: Optional system prompt

        Returns:
            Response text
        """
        import httpx  # noqa: F811

        if not self.access_token or time.time() > self.expires_at:
            await self._refresh_token()

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/event-stream",
        }

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "action": "next",
            "messages": messages,
            "model": model,
            "parent_message_id": None,
        }

        try:
            resp = await self.session.post(
                CHATGPT_CONVERSATION,
                json=payload,
                headers=headers,
            )

            if resp.status_code != 200:
                log.error(f"ChatGPT API error: {resp.status_code}")
                return f"Error: {resp.status_code}"

            # Parse SSE response
            text = ""
            for line in resp.text.split("\n"):
                if line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                        if data.get("type") == "message_delta":
                            text += data.get("delta", "")
                    except json.JSONDecodeError:
                        continue

            return text.strip()

        except Exception as e:
            log.error(f"ChatGPT query failed: {e}")
            return f"Error: {e}"

    async def close(self) -> None:
        """Close the HTTP session."""
        if self.session:
            await self.session.aclose()


def get_chatgpt_client(cookies: Any = None) -> ChatGPTClient:
    """Get a ChatGPTClient with auto-imported Chrome cookies.

    Args:
        cookies: Optional cookie jar. If None, auto-imports from Chrome.

    Returns:
        ChatGPTClient instance ready for init()
    """
    if cookies is None:
        try:
            import browser_cookie3
            cookies = browser_cookie3.chrome(domain_name=".chatgpt.com")
            log.info("ChatGPT cookies loaded from Chrome")
        except Exception as e:
            log.warning(f"Failed to load ChatGPT cookies: {e}")
            cookies = None

    return ChatGPTClient(cookies=cookies)


async def chatgpt_query(
    prompt: str,
    system_prompt: str | None = None,
    model: str = "auto",
) -> str:
    """Quick query to ChatGPT.

    Args:
        prompt: User message
        system_prompt: Optional system prompt
        model: Model to use

    Returns:
        Response text
    """
    client = get_chatgpt_client()
    try:
        await client.init()
        return await client.generate_content(
            prompt, model=model, system_prompt=system_prompt
        )
    finally:
        await client.close()
