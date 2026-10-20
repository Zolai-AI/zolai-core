"""Authorization-server layer (OAuth 2.1 for the MCP endpoint).

Deliberately separate from :mod:`zolai.api` — this package *issues* and
*resolves* credentials; the existing identity (``users`` / ``sessions`` /
argon2id, :mod:`zolai.api.session_auth`) stays the only identity system.
"""
