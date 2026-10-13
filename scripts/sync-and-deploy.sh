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
# The server has two checkouts of zolai-core. `/home/ubuntu/zolai-core` is the
# authoritative one — the running container is built from it. `/opt/zolai-core`
# is a stale, divergent copy.
#
# This script used to sync into `/opt`, which is precisely how the June outage
# happened: code landed in the tree nothing serves, and the next
# `docker compose up` from `/home/ubuntu` silently rolled it back. It also
# rsynced `.env.production`, which does not exist in a developer checkout at all
# (the file holds server secrets), so the step aborted the script outright.
REMOTE_PATH="/home/ubuntu/zolai-core"
LOCAL_PATH="$(cd "$(dirname "$0")/.." && pwd)"

log() { echo -e "\033[0;34m[$(date +'%H:%M:%S')]\033[0m $*"; }
ok() { echo -e "\033[0;32m[OK]\033[0m $*"; }
err() { echo -e "\033[0;31m[ERR]\033[0m $*"; }

# Refuse to deploy a dirty tree. Everything here becomes a production image, so
# an uncommitted or untracked file — an experiment, a half-written module — would
# ship silently. Commit or stash first.
if [[ -n "$(git -C "$LOCAL_PATH" status --porcelain)" ]]; then
    err "SYNC BLOCKED: the working tree has uncommitted or untracked changes."
    git -C "$LOCAL_PATH" status --short
    err "Commit them, or stash them, then re-run. Unreviewed code must not reach production."
    exit 1
fi

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

# Dockerfile / compose / pyproject only. `.env.production` is deliberately NOT
# synced: it holds server-side secrets, exists only on the host, and copying a
# developer copy over it would either fail or overwrite real credentials.
log "Syncing build files..."
rsync -av \
    "$LOCAL_PATH/Dockerfile.prod" \
    "$LOCAL_PATH/docker-compose.prod.yml" \
    "$LOCAL_PATH/pyproject.toml" \
    "$REMOTE:$REMOTE_PATH/"

log "Deploying on $REMOTE..."
ssh "$REMOTE" "cd $REMOTE_PATH && ./scripts/deploy.sh --no-cache --verify"

ok "Deploy complete!"
