#!/usr/bin/env python3
"""
zolai_panels.py — Unified Zolai AI panels interface.

Provides access to both Gemini and ChatGPT for Zolai language tasks.
Both use browser-cookie authentication for free access.

Usage:
    from zolai_panels import ZolaiPanels

    panels = ZolaiPanels()

    # Query both AI panels
    results = await panels.query_both("Fix this Zolai: Ka pathian in ram a piangsak hi.")

    # Gemini only
    gemini_result = await panels.query_gemini("Translate 'God created the earth' to Zolai")

    # ChatGPT only
    chatgpt_result = await panels.query_chatgpt("What is the ZVS 2018 orthography?")
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

# ZVS system prompt for both panels
ZVS_SYSTEM_PROMPT = """You are a Tedim Zolai language expert following ZVS 2018 orthography.

Key rules:
- SOV word order (Subject-Object-Verb)
- Agreement particles before verbs (a, ka, na)
- khin = past/experiential aspect
- ta = completive/realized aspect (NOT simple past)
- kei = standard negation (ALL persons)
- lo = literary/formal standalone negation
- hiam = yes/no question marker
- bang hang = content question
- u = elder sibling, nau = younger sibling
- ne = general eating/drinking
- nasep = work/service, kammal = deed/action

Forbidden forms (MUST NOT use):
- pathian → use pasian
- ram → use gam
- fapa → use tapa
- bawipa → use topa
- siangpahrang → use kumpipa
- cu/cun → use tua

Always provide Zolai translations with correct ZVS 2018 orthography."""


class ZolaiPanels:
    """Unified interface for Gemini and ChatGPT AI panels."""

    def __init__(self):
        self._gemini = None
        self._chatgpt = None

    async def _get_gemini(self) -> Any:
        """Lazy-load Gemini client."""
        if self._gemini is None:
            from gemini_cookies import get_gemini_client

            self._gemini = get_gemini_client()
            await self._gemini.init(timeout=30, auto_close=True, close_delay=120)
        return self._gemini

    async def _get_chatgpt(self) -> Any:
        """Lazy-load ChatGPT client."""
        if self._chatgpt is None:
            from chatgpt_cookies import get_chatgpt_client

            self._chatgpt = get_chatgpt_client()
            await self._chatgpt.init()
        return self._chatgpt

    async def query_gemini(
        self, prompt: str, system_prompt: str | None = None
    ) -> str:
        """Query Gemini for Zolai tasks."""
        try:
            client = await self._get_gemini()
            return await client.generate_content(prompt)
        except Exception as e:
            log.error(f"Gemini query failed: {e}")
            return f"Gemini error: {e}"

    async def query_chatgpt(
        self, prompt: str, system_prompt: str | None = None
    ) -> str:
        """Query ChatGPT for Zolai tasks."""
        try:
            client = await self._get_chatgpt()
            return await client.generate_content(
                prompt,
                system_prompt=system_prompt or ZVS_SYSTEM_PROMPT,
            )
        except Exception as e:
            log.error(f"ChatGPT query failed: {e}")
            return f"ChatGPT error: {e}"

    async def query_both(
        self, prompt: str, system_prompt: str | None = None
    ) -> dict[str, str]:
        """Query both Gemini and ChatGPT in parallel.

        Returns:
            {"gemini": "...", "chatgpt": "..."}
        """
        sys_prompt = system_prompt or ZVS_SYSTEM_PROMPT

        # Run both queries in parallel
        gemini_task = asyncio.create_task(
            self.query_gemini(prompt, sys_prompt)
        )
        chatgpt_task = asyncio.create_task(
            self.query_chatgpt(prompt, sys_prompt)
        )

        gemini_result, chatgpt_result = await asyncio.gather(
            gemini_task, chatgpt_task, return_exceptions=True
        )

        return {
            "gemini": (
                str(gemini_result)
                if not isinstance(gemini_result, Exception)
                else f"Error: {gemini_result}"
            ),
            "chatgpt": (
                str(chatgpt_result)
                if not isinstance(chatgpt_result, Exception)
                else f"Error: {chatgpt_result}"
            ),
        }

    async def close(self) -> None:
        """Close both clients."""
        if self._chatgpt:
            await self._chatgpt.close()
