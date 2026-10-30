#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "== KG ingest wiki =="
python "$ROOT_DIR/scripts/kg/ingest_wiki.py"

echo "== KG embed chunks =="
python "$ROOT_DIR/scripts/kg/embed.py"

# The RDF/TTL and embedded JSONL land in "$ROOT_DIR/artifacts/kg"; the web app
# imports them from there. The old Prisma step pointed at
# "$ROOT_DIR/website/zolai-project", a directory that no longer exists in this
# repo (the web app is the sibling zolai-web), so it is not run from here.

echo "Done: artifacts/kg"

