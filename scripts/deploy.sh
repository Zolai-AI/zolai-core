#!/usr/bin/env bash
# ============================================================================
# Zolai Core - Native Deployment (systemd, NO Docker)
# ============================================================================
# Runs ON the production host. Deploys the committed tree from `origin/main`
# into the authoritative checkout, restarts the native systemd unit, and polls
# `/health` until the API answers — then optionally verifies it end to end.
#
# Usage: ./scripts/deploy.sh [options]
#
# Options:
#   --skip-merge     Skip `git fetch` + `git merge` (restart + verify only)
#   --no-verify      Skip post-restart verification
#   --help           Show this help
#
# Native topology (pcore-server):
#   checkout    /home/ubuntu/zolai-core                 (authoritative)
#   unit        zolai-core-dev.service
#   venv        /home/ubuntu/zolai-core/.venv-dev
#   env file    /home/ubuntu/zolai-core/.env.dev        (the unit's EnvironmentFile)
#   listen      127.0.0.1:8001                          (nginx terminates TLS)
#   startup     ~13s to become ready after a restart, so readiness is POLLED,
#               never assumed with a fixed sleep.
#
# There is no Docker here. `docker ps -a` on this host returns nothing; the
# container files (Dockerfile*, docker-compose*) were deleted on 2026-10-18.
# (The workspace-root repo owns a `native-deploy.sh`; this is the in-repo one.)
#
# Environment (read by the service from its EnvironmentFile):
#   ZOLAI_ENGINE_MODE      rule|hybrid|ai (default: hybrid)
#   AI_BRAIN_URL           Brain API endpoint
#   AI_BRAIN_API_KEY       Brain API key
#   ZOLAI_API_AUTH         warn|enforce|off
#   ZOLAI_DATA_ROOT        Data directory (default: <checkout>/data)
# ============================================================================

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Config (all overridable from the environment)
SERVICE="${ZOLAI_DEPLOY_SERVICE:-zolai-core-dev}"
ENV_FILE_NAME="${ZOLAI_DEPLOY_ENV_FILE:-.env.dev}"
BRANCH="${ZOLAI_DEPLOY_BRANCH:-main}"
HEALTH_ENDPOINT="${ZOLAI_DEPLOY_HEALTH_URL:-http://127.0.0.1:8001/health}"
HEALTH_TIMEOUT="${ZOLAI_DEPLOY_HEALTH_TIMEOUT:-30}"   # seconds (~13s is normal)

# Flags
SKIP_MERGE=false
VERIFY=true

log() { echo -e "${BLUE}[$(date +'%H:%M:%S')]${NC} $*"; }
ok() { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err() { echo -e "${RED}[ERR]${NC} $*"; }

# Resolve our own directory so the guard below compares real paths.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPECTED_DEPLOY_DIR="${EXPECTED_DEPLOY_DIR:-/home/ubuntu/zolai-core}"

usage() {
    grep '^#' "$0" | cut -c4-
    exit 0
}

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-merge) SKIP_MERGE=true; shift ;;
        --no-verify) VERIFY=false; shift ;;
        --help|-h) usage ;;
        *) err "Unknown option: $1"; usage ;;
    esac
done

# systemctl, with sudo only when we are not already root.
systemctl_do() {
    if [[ "${EUID}" -eq 0 ]]; then systemctl "$@"; else sudo systemctl "$@"; fi
}

# Refuse to deploy from a checkout that is not the one serving production.
#
# The server had two copies of this repo - /home/ubuntu/zolai-core and
# /opt/zolai-core - and BOTH are named "zolai-core". Deploying from the wrong
# copy lands the code in a tree nothing serves and silently rolls back shipped
# fixes with no error. Override with EXPECTED_DEPLOY_DIR=... if that ever moves.
if [[ "$SCRIPT_DIR" != "$EXPECTED_DEPLOY_DIR" ]]; then
    err "Refusing to deploy from $SCRIPT_DIR"
    err "Production is served from $EXPECTED_DEPLOY_DIR"
    err "Run this from $EXPECTED_DEPLOY_DIR, or set EXPECTED_DEPLOY_DIR if that moved."
    exit 1
fi

# ── Pre-flight ───────────────────────────────────────────────────────────────
log "Pre-flight checks..."
git -C "$SCRIPT_DIR" rev-parse --git-dir >/dev/null 2>&1 || {
    err "$SCRIPT_DIR is not a git checkout"; exit 1;
}
command -v systemctl >/dev/null || { err "systemctl not found (is this a systemd host?)"; exit 1; }
systemctl cat "$SERVICE" >/dev/null 2>&1 || {
    err "systemd unit '$SERVICE' not found"
    err "Expected the native unit; there is no container to fall back to."
    exit 1
}
ENV_FILE="$SCRIPT_DIR/$ENV_FILE_NAME"
if [[ -f "$ENV_FILE" ]]; then
    # Source for the verification step below; never echoed.
    set -a; # shellcheck disable=SC1090
    source "$ENV_FILE"; set +a
else
    warn "EnvironmentFile $ENV_FILE not found — the unit may be missing its config"
fi
ok "Pre-flight passed (unit: $SERVICE)"

# ── Merge the committed tree from origin ─────────────────────────────────────
if [[ "$SKIP_MERGE" == "false" ]]; then
    log "Fetching origin..."
    git -C "$SCRIPT_DIR" fetch --prune origin

    if [[ -n "$(git -C "$SCRIPT_DIR" status --porcelain)" ]]; then
        warn "Working tree has uncommitted changes; the merge below will keep them"
        warn "unless it needs to touch the same files (in which case git will abort)."
        git -C "$SCRIPT_DIR" status --short | head -20
    fi

    BEFORE="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD)"
    log "Merging origin/$BRANCH (--no-edit)..."
    git -C "$SCRIPT_DIR" merge --no-edit "origin/$BRANCH"
    AFTER="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD)"
    ok "HEAD: $BEFORE -> $AFTER"
else
    log "Skipping git merge (--skip-merge)"
fi

# ── Restart the native unit ──────────────────────────────────────────────────
log "Restarting $SERVICE..."
systemctl_do restart "$SERVICE"

# ── Poll /health (never a fixed sleep — startup is ~13s) ─────────────────────
log "Waiting for $HEALTH_ENDPOINT (timeout ${HEALTH_TIMEOUT}s; ~13s is normal)..."
HEALTHY=false
DEADLINE=$(( SECONDS + HEALTH_TIMEOUT ))
while (( SECONDS < DEADLINE )); do
    if curl -sf --max-time 3 "$HEALTH_ENDPOINT" >/dev/null 2>&1; then
        HEALTHY=true
        break
    fi
    sleep 1
done
if [[ "$HEALTHY" != true ]]; then
    err "API did not answer $HEALTH_ENDPOINT within ${HEALTH_TIMEOUT}s"
    err "--- systemctl status $SERVICE ---"
    systemctl_do status "$SERVICE" --no-pager -l | tail -30 || true
    err "--- journalctl -u $SERVICE (last 50) ---"
    journalctl -u "$SERVICE" -n 50 --no-pager || true
    exit 1
fi
ok "Health endpoint responding"

# ── Verify ───────────────────────────────────────────────────────────────────
if [[ "$VERIFY" == "true" ]]; then
    log "Running verification..."

    # Pick the venv the unit actually uses, then the default one.
    VENV_PY=""
    for cand in "$SCRIPT_DIR/.venv-dev/bin/python" "$SCRIPT_DIR/.venv/bin/python"; do
        if [[ -x "$cand" ]]; then VENV_PY="$cand"; break; fi
    done

    if [[ -n "$VENV_PY" ]]; then
        log "Verifying engine configuration..."
        "$VENV_PY" -c "
from zolai.engines import AI_KEY_ENV_VARS, ai_key_present, llm_allowed, engine_mode
print('engine_mode:', engine_mode())
print('AI_KEY_ENV_VARS:', AI_KEY_ENV_VARS)
print('ai_key_present:', ai_key_present())
print('llm_allowed:', llm_allowed())
" || { err "Engine config verification failed"; exit 1; }
        ok "Engine config verified"
    else
        warn "No venv python found under $SCRIPT_DIR (.venv-dev/.venv) — skipping engine check"
    fi

    # Test assistant chat
    log "Testing assistant chat..."
    RESPONSE=$(curl -sf -X POST -H 'Content-Type: application/json' \
        -d '{"message":"Say hello in Zolai"}' \
        http://127.0.0.1:8001/api/v1/assistant/chat)
    echo "$RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
assert 'retrieval_only' in data, 'Missing retrieval_only field'
if data.get('retrieval_only'):
    print('WARN: Still retrieval_only - check provider config')
else:
    print('OK: Generated response received')
    print('Provider:', data.get('provider'))
    print('Model:', data.get('model'))
"
    ok "Assistant chat tested"

    # Test provider connection
    log "Testing provider connection..."
    # Sign in through the real endpoint rather than minting a token in-process:
    # `create_session_token` no longer exists, so that step raised ImportError on
    # every deploy and took the whole script down *after* the app was already
    # verified and serving. A credential from the environment is used so no
    # password is committed.
    ADMIN_USER=${ADMIN_USER:-${ZOLAI_ADMIN_USER:-}}
    ADMIN_PASS=${ADMIN_PASS:-${ZOLAI_ADMIN_PASS:-}}
    if [[ -z "$ADMIN_USER" || -z "$ADMIN_PASS" ]]; then
        warn "ADMIN_USER/ADMIN_PASS not set - skipping the provider connection test"
        warn "(set them, or export ZOLAI_ADMIN_USER / ZOLAI_ADMIN_PASS)"
    else
        ADMIN_TOKEN=$(curl -sf -X POST -H 'Content-Type: application/json' \
            -d "$(python3 -c "import json,sys; print(json.dumps({'username': sys.argv[1], 'password': sys.argv[2]}))" "$ADMIN_USER" "$ADMIN_PASS")" \
            http://127.0.0.1:8001/api/v1/auth/login | \
            python3 -c "import sys, json; print(json.load(sys.stdin).get('token', ''))")
        if [[ -z "$ADMIN_TOKEN" ]]; then
            err "Could not obtain an admin token - provider connection test failed"
            exit 1
        fi
        curl -sf -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
            http://127.0.0.1:8001/api/v1/admin/ai-providers/pcore-brain/test | \
            python3 -c "
import sys, json
data = json.load(sys.stdin)
assert data.get('ok') == True, f'Provider test failed: {data.get(\"error\")}'
print('OK: Provider connection successful')
print('Model:', data.get('model'))
print('Latency:', data.get('latency_ms'), 'ms')
"
        ok "Provider connection verified"
    fi
fi

# ── Summary ──────────────────────────────────────────────────────────────────
log "Deployment complete!"
echo
echo "  Health:      $HEALTH_ENDPOINT"
echo "  API Base:    http://127.0.0.1:8001/api/v1"
echo "  Assistant:   POST /api/v1/assistant/chat"
echo "  Admin:       GET /api/v1/admin/ai-providers"
echo
echo "  Unit:        $SERVICE  (EnvironmentFile: $ENV_FILE)"
echo "  Logs:        journalctl -u $SERVICE -f"
echo "  Restart:     sudo systemctl restart $SERVICE"
ok "All done!"
