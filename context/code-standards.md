# Code Standards — Zolai Core

- **Languages:** Python (ruff), Docker, YAML.
- Ruff enabled (`ruff check zolai/`); keep Modern/Pythonic style, type hints on public API.
- Commit style: Conventional Commits.
- Keep secrets in `.env` only — never commit keys/tokens.
- Enforce ZVS 2018 orthography on all text output.
- Tests must pass before push (`python -m pytest tests/ -q`).
- Do not commit generated artifacts (`__pycache__`, `.venv`, `node_modules`, `data/`).
