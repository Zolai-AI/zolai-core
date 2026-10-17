#!/usr/bin/env bash
# ============================================================================
# Zolai Core - Quick Sync & Deploy (native systemd, NO Docker)
# ============================================================================
# Usage: ./scripts/sync-and-deploy.sh [remote-host]
#
# Deploys the *committed* tree to production the git way and restarts the native
# systemd unit. Run from repo root: ./scripts/sync-and-deploy.sh pcore-server
#
# What it does on the remote host:
#   1. cd /home/ubuntu/zolai-core            # the authoritative checkout
#   2. git fetch --prune origin
#   3. git merge --no-edit origin/main       # ship the pushed commit
#   4. sudo systemctl restart zolai-core-dev # native unit, not a container
#   5. poll http://127.0.0.1:8001/health until it answers 200 (30s timeout,
#      then fail loudly). Startup is ~13s, so the readiness is POLLED — a fixed
#      `sleep` would report a false failure.
#
# It ships the committed tree: only what is pushed to `origin/main` can reach
# production. That is deliberate — see the note below.
#
# WHY THIS IS GIT-BASED (it replaces a dangerous rsync):
#   The previous version rsynced a local tree over the server checkout with
#   `--delete`. The server checkout carries commits that exist ONLY there
#   (server-side RAG grounding / notification / deploy-guard fixes), so an
#   rsync would silently ERASE them. `git merge origin/main` cannot do that: it
#   is additive and fast-forwards or merges. It also makes the deployed
#   revision auditable (`git rev-parse HEAD` on the host).
#
#   The workspace-root repo owns a `native-deploy.sh`; this is the in-repo path.
#
# Env overrides:
#   ZOLAI_DEPLOY_PATH       remote checkout      (default: /home/ubuntu/zolai-core)
#   ZOLAI_DEPLOY_SERVICE    systemd unit         (default: zolai-core-dev)
#   ZOLAI_DEPLOY_BRANCH     branch to merge      (default: main)
#   ZOLAI_DEPLOY_HEALTH_URL remote health URL    (default: http://127.0.0.1:8001/health)
#   ZOLAI_DEPLOY_TIMEOUT    health poll timeout  (default: 30)
#   ZOLAI_DEPLOY_SYSTEMCTL  restart command      (default: "sudo systemctl")
#                           set to "systemctl" when the remote user is root or
#                           has passwordless sudo that does not replay prompts.
# ============================================================================

set -euo pipefail

REMOTE="${1:-pcore-server}"
REMOTE_PATH="${ZOLAI_DEPLOY_PATH:-/home/ubuntu/zolai-core}"
SERVICE="${ZOLAI_DEPLOY_SERVICE:-zolai-core-dev}"
BRANCH="${ZOLAI_DEPLOY_BRANCH:-main}"
HEALTH_URL="${ZOLAI_DEPLOY_HEALTH_URL:-http://127.0.0.1:8001/health}"
HEALTH_TIMEOUT="${ZOLAI_DEPLOY_TIMEOUT:-30}"
SYSTEMCTL="${ZOLAI_DEPLOY_SYSTEMCTL:-sudo systemctl}"
LOCAL_PATH="$(cd "$(dirname "$0")/.." && pwd)"

log() { echo -e "\033[0;34m[$(date +'%H:%M:%S')]\033[0m $*"; }
ok() { echo -e "\033[0;32m[OK]\033[0m $*"; }
warn() { echo -e "\033[1;33m[WARN]\033[0m $*"; }
err() { echo -e "\033[0;31m[ERR]\033[0m $*"; }

# ── Warnings: what does NOT ship ─────────────────────────────────────────────
# Production deploys `origin/main`. Anything uncommitted here never leaves this
# machine, and anything committed-but-unpushed is invisible to the host's fetch.
if [[ -n "$(git -C "$LOCAL_PATH" status --porcelain)" ]]; then
    warn "Working tree has uncommitted changes — NOT deployed (production merges origin/$BRANCH)."
    git -C "$LOCAL_PATH" status --short | head -20
fi
git -C "$LOCAL_PATH" fetch --quiet origin 2>/dev/null || true
if git -C "$LOCAL_PATH" rev-parse --verify --quiet "origin/$BRANCH" >/dev/null; then
    LOCAL_HEAD="$(git -C "$LOCAL_PATH" rev-parse "$BRANCH" 2>/dev/null || git -C "$LOCAL_PATH" rev-parse HEAD)"
    ORIGIN_HEAD="$(git -C "$LOCAL_PATH" rev-parse "origin/$BRANCH")"
    if [[ "$LOCAL_HEAD" != "$ORIGIN_HEAD" ]]; then
        warn "Local $BRANCH ($(git -C "$LOCAL_PATH" rev-parse --short "$LOCAL_HEAD")) differs from origin/$BRANCH ($(git -C "$LOCAL_PATH" rev-parse --short "$ORIGIN_HEAD"))"
        warn "Only origin/$BRANCH is deployed — push first if that is the newer one."
    fi
fi

log "Deploying $REMOTE:$REMOTE_PATH (unit $SERVICE) from origin/$BRANCH..."
ok "Local revision: $(git -C "$LOCAL_PATH" rev-parse --short HEAD)"

# ── Do the whole flow on the host ────────────────────────────────────────────
ssh "$REMOTE" \
    "REMOTE_PATH='$REMOTE_PATH' SERVICE='$SERVICE' BRANCH='$BRANCH' HEALTH_URL='$HEALTH_URL' HEALTH_TIMEOUT='$HEALTH_TIMEOUT' SYSTEMCTL='$SYSTEMCTL' bash -s" <<'REMOTE_EOF'
set -euo pipefail

cd "$REMOTE_PATH"

echo "  [1/5] git fetch --prune origin"
git fetch --prune origin

if [[ -n "$(git status --porcelain)" ]]; then
    echo "  [warn] server checkout has uncommitted changes; merge keeps them unless it must touch them"
    git status --short | head -20
fi

BEFORE="$(git rev-parse --short HEAD)"
echo "  [2/5] git merge --no-edit origin/$BRANCH"
git merge --no-edit "origin/$BRANCH"
AFTER="$(git rev-parse --short HEAD)"
echo "        HEAD: $BEFORE -> $AFTER"

echo "  [3/5] $SYSTEMCTL restart $SERVICE"
$SYSTEMCTL restart "$SERVICE"

echo "  [4/5] poll $HEALTH_URL (timeout ${HEALTH_TIMEOUT}s; ~13s is normal)"
HEALTHY=false
DEADLINE=$(( SECONDS + HEALTH_TIMEOUT ))
while (( SECONDS < DEADLINE )); do
    if curl -sf --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then
        HEALTHY=true
        break
    fi
    sleep 1
done
if [[ "$HEALTHY" != true ]]; then
    echo "  [ERR] API did not answer $HEALTH_URL within ${HEALTH_TIMEOUT}s" >&2
    echo "  --- systemctl status $SERVICE ---" >&2
    $SYSTEMCTL status "$SERVICE" --no-pager -l | tail -30 || true
    echo "  --- journalctl -u $SERVICE (last 50) ---" >&2
    journalctl -u "$SERVICE" -n 50 --no-pager || true
    exit 1
fi

echo "  [5/5] healthy at $(git rev-parse --short HEAD)"
REMOTE_EOF

ok "Deploy complete — $REMOTE:$REMOTE_PATH is serving the merged revision."
echo "  Health:  $REMOTE:$HEALTH_URL"
echo "  Logs:    ssh $REMOTE 'journalctl -u $SERVICE -f'"
