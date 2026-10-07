#!/usr/bin/env bash
# Nightly backup cron wrapper — KR2.2
# Sources .env for DB path, runs the backup_nightly.py script directly
# Suitable for systemd timer or cron

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"

# Load .env if present (for ZOLAI_DATA_ROOT, etc.)
if [[ -f ".env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

# Run the backup script directly (standalone, no zolai module dependencies)
exec python "${SCRIPT_DIR}/backup_nightly.py"