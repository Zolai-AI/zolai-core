"""Zolai UI Module — FastAPI routes serving HTML templates for review queue.

Provides HTMX-powered interfaces for reviewing foundation pipeline items.
"""

from .routes import router

__all__ = ["router"]
