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

# Deploy the **committed** tree, not the working tree.
#
# Production runs `git archive HEAD`, so an untracked file — a half-written
# module, an experiment someone left behind — can never reach the image. This
# replaces a guard that merely *refused* to deploy a dirty tree: that guard is
# correct but it also blocks deploying an already-committed fix while unrelated
# work sits uncommitted in the same checkout, which is the common case when two
# people share a repo. Anything you meant to ship must be committed first.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
git -C "$LOCAL_PATH" archive HEAD | tar -x -C "$STAGE"

if ! git -C "$LOCAL_PATH" diff --quiet HEAD -- zolai Dockerfile.prod docker-compose.prod.yml pyproject.toml; then
    err "WARNING: tracked files differ from HEAD — deploying the committed version."
    git -C "$LOCAL_PATH" status --short -- zolai Dockerfile.prod docker-compose.prod.yml pyproject.toml
fi
if [[ -n "$(git -C "$LOCAL_PATH" ls-files --others --exclude-standard -- zolai)" ]]; then
    err "WARNING: untracked files under zolai/ are NOT being deployed."
    git -C "$LOCAL_PATH" ls-files --others --exclude-standard -- zolai
fi
ok "Staged committed tree: $(git -C "$LOCAL_PATH" rev-parse --short HEAD)"

log "Syncing code to $REMOTE:$REMOTE_PATH..."
rsync -av --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    "$STAGE/zolai/" "$REMOTE:$REMOTE_PATH/zolai/"

# Dockerfile / compose / pyproject only. `.env.production` is deliberately NOT
# synced: it holds server-side secrets, exists only on the host, and copying a
# developer copy over it would either fail or overwrite real credentials.
log "Syncing build files..."
rsync -av \
    "$STAGE/Dockerfile.prod" \
    "$STAGE/docker-compose.prod.yml" \
    "$STAGE/pyproject.toml" \
    "$REMOTE:$REMOTE_PATH/"

log "Deploying on $REMOTE..."
ssh "$REMOTE" "cd $REMOTE_PATH && ./scripts/deploy.sh --no-cache --verify"

ok "Deploy complete!"
