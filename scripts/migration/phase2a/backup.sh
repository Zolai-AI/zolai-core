#!/usr/bin/env bash
# Phase 2A: Full database backup
set -euo pipefail

# Resolve DB path relative to this script (4 levels up to workspace root)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB="$SCRIPT_DIR/../../../../data/zolai.db"
BACKUP="$SCRIPT_DIR/../../../../data/zolai_backup_$(date +%Y%m%d_%H%M%S).db"

echo "Backing up $DB to $BACKUP..."
sqlite3 "$DB" "VACUUM INTO '$BACKUP';"

# Verify
ORIG_TABLES=$(sqlite3 "$DB" "SELECT COUNT(*) FROM sqlite_master WHERE type='table';")
BACKUP_TABLES=$(sqlite3 "$BACKUP" "SELECT COUNT(*) FROM sqlite_master WHERE type='table';")
ORIG_SIZE=$(stat -f%z "$DB" 2>/dev/null || stat -c%s "$DB")
BACKUP_SIZE=$(stat -f%z "$BACKUP" 2>/dev/null || stat -c%s "$BACKUP")

echo "Original: $ORIG_TABLES tables, $ORIG_SIZE bytes"
echo "Backup:   $BACKUP_TABLES tables, $BACKUP_SIZE bytes"

if [ "$ORIG_TABLES" != "$BACKUP_TABLES" ]; then
    echo "ERROR: Table count mismatch!"
    exit 1
fi

echo "✅ Backup verified successfully"
echo "Backup location: $BACKUP"