#!/usr/bin/env bash
# Phase 2A: Rollback — drop all _v2 tables
set -euo pipefail

# Resolve DB path relative to this script (4 levels up to workspace root)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB="$SCRIPT_DIR/../../../../data/zolai.db"

echo "⚠️  This will DROP all _v2 tables from $DB"
read -p "Continue? (y/N): " confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    echo "Cancelled."
    exit 0
fi

TABLES=$(sqlite3 "$DB" "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_v2';")

for t in $TABLES; do
    echo "Dropping $t..."
    sqlite3 "$DB" "DROP TABLE IF EXISTS [$t];"
done

echo "✅ Rollback complete. _v2 tables removed."