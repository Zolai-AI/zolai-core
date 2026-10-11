#!/usr/bin/env bash
# ============================================================================
# Zolai Core - Quick Sync & Deploy
# ============================================================================
# Usage: ./scripts/sync-and-deploy.sh [remote-host]
# 
# Syncs local zolai/ to remote, rebuilds, and deploys.
# Run from repo root: ./scripts/sync-and-deploy.sh pcore-server
# ============================================================================

set -euo pipefail

REMOTE="${1:-pcore-server}"
REMOTE_PATH="/opt/zolai-core"
LOCAL_PATH="$(cd "$(dirname "$0")/.." && pwd)"

log() { echo -e "\033[0;34m[$(date +'%H:%M:%S')]\033[0m $*"; }
ok() { echo -e "\033[0;32m[OK]\033[0m $*"; }
err() { echo -e "\033[0;31m[ERR]\033[0m $*"; }

log "Syncing code to $REMOTE:$REMOTE_PATH..."
rsync -av --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.venv' \
    --exclude 'data' \
    --exclude 'models' \
    --exclude '.git' \
    --exclude '*.egg-info' \
    "$LOCAL_PATH/zolai/" "$REMOTE:$REMOTE_PATH/zolai/"

log "Syncing config files..."
rsync -av \
    "$LOCAL_PATH/Dockerfile.prod" \
    "$LOCAL_PATH/docker-compose.prod.yml" \
    "$LOCAL_PATH/pyproject.toml" \
    "$LOCAL_PATH/.env.production" \
    "$REMOTE:$REMOTE_PATH/"

log "Deploying on $REMOTE..."
ssh "$REMOTE" "cd $REMOTE_PATH && ./scripts/deploy.sh --no-cache --verify"

ok "Deploy complete!"
