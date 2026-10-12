#!/usr/bin/env bash
# ============================================================================
# Zolai Core - Production Deployment Script
# ============================================================================
# Usage: ./scripts/deploy.sh [options]
#
# Options:
#   --no-cache       Force clean rebuild (no Docker cache)
#   --pull           Pull latest base images before build
#   --skip-build     Skip build, only restart containers
#   --verify         Run health checks after deploy
#   --help           Show this help
#
# Environment (from .env.production):
#   ZOLAI_ENGINE_MODE      rule|hybrid|ai (default: hybrid)
#   AI_BRAIN_URL           Brain API endpoint
#   AI_BRAIN_API_KEY       Brain API key
#   ZOLAI_API_AUTH         warn|enforce|off
#   ZOLAI_DATA_ROOT        Data directory (default: /app/data)
# ============================================================================

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Config
COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env.production"
HEALTH_ENDPOINT="http://127.0.0.1:8001/api/v1/health"
MAX_WAIT=120

# Flags
NO_CACHE=false
PULL=false
SKIP_BUILD=false
VERIFY=true

log() { echo -e "${BLUE}[$(date +'%H:%M:%S')]${NC} $*"; }
ok() { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err() { echo -e "${RED}[ERR]${NC} $*"; }

# Resolve our own directory so the guard below compares real paths.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPECTED_DEPLOY_DIR="${EXPECTED_DEPLOY_DIR:-/home/ubuntu/zolai-core}"

# Refuse to deploy from a checkout that is not the one serving production.
#
# The server had two copies of this repo - /home/ubuntu/zolai-core and
# /opt/zolai-core - and BOTH are named "zolai-core". `docker compose` derives the
# project name from the directory basename, so deploying from either one targets
# the same Docker project while the env file actually in force comes from
# whichever copy last recreated the container. The trees were 40 files apart and
# a deploy from the wrong copy silently reverted a shipped fix with no error.
#
# Override with EXPECTED_DEPLOY_DIR=... if the authoritative path ever moves.
if [[ "$SCRIPT_DIR" != "$EXPECTED_DEPLOY_DIR" ]]; then
    err "Refusing to deploy from $SCRIPT_DIR"
    err "Production is served from $EXPECTED_DEPLOY_DIR"
    err "Two checkouts both named zolai-core target the same Docker project, so a"
    err "deploy from the wrong one applies the wrong tree and the wrong env file."
    err "Run this from $EXPECTED_DEPLOY_DIR, or set EXPECTED_DEPLOY_DIR if that moved."
    exit 1
fi

usage() {
    grep '^#' "$0" | cut -c4-
    exit 0
}

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --no-cache) NO_CACHE=true; shift ;;
        --pull) PULL=true; shift ;;
        --skip-build) SKIP_BUILD=true; shift ;;
        --no-verify) VERIFY=false; shift ;;
        --help) usage ;;
        *) err "Unknown option: $1"; usage ;;
    esac
done

# Pre-flight checks
log "Pre-flight checks..."
[[ -f "$ENV_FILE" ]] || { err "Missing $ENV_FILE"; exit 1; }
[[ -f "$COMPOSE_FILE" ]] || { err "Missing $COMPOSE_FILE"; exit 1; }
command -v docker >/dev/null || { err "Docker not installed"; exit 1; }
docker compose -f "$COMPOSE_FILE" version >/dev/null || { err "Docker Compose v2 required"; exit 1; }

# CRITICAL: Verify we're running from the authoritative checkout
# The server has two zolai-core checkouts; only /home/ubuntu/zolai-core is authoritative
AUTHORITATIVE_DIR="/home/ubuntu/zolai-core"
CURRENT_DIR=$(pwd -P)
if [[ "$CURRENT_DIR" != "$AUTHORITATIVE_DIR" ]]; then
    err "DEPLOY BLOCKED: This script must run from $AUTHORITATIVE_DIR"
    err "Current directory: $CURRENT_DIR"
    err "The running container was built from $AUTHORITATIVE_DIR"
    err "Running from anywhere else will silently roll back fixes (see OPEN-ISSUES.md S1)"
    exit 1
fi
ok "Directory check passed: $CURRENT_DIR"

ok "Pre-flight passed"

# Source env for verification
set -a; source "$ENV_FILE"; set +a

# Build
if [[ "$SKIP_BUILD" == "false" ]]; then
    log "Building API image..."
    BUILD_ARGS=()
    [[ "$NO_CACHE" == "true" ]] && BUILD_ARGS+=(--no-cache)
    [[ "$PULL" == "true" ]] && BUILD_ARGS+=(--pull)
    docker compose -f "$COMPOSE_FILE" build "${BUILD_ARGS[@]}" api
    ok "Build complete"
else
    log "Skipping build (--skip-build)"
fi

# Deploy
log "Deploying containers..."
docker compose -f "$COMPOSE_FILE" up -d --force-recreate api

# Wait for health
log "Waiting for health check..."
for i in $(seq 1 $MAX_WAIT); do
    if curl -sf "$HEALTH_ENDPOINT" >/dev/null 2>&1; then
        ok "Health endpoint responding"
        break
    fi
    [[ $i -eq $MAX_WAIT ]] && { err "Health check timeout after ${MAX_WAIT}s"; exit 1; }
    sleep 1
done

# Verify
if [[ "$VERIFY" == "true" ]]; then
    log "Running verification..."
    
    # Check engine config
    log "Verifying engine configuration..."
    docker exec zolai-core-api-1 python3 -c "
from zolai.engines import AI_KEY_ENV_VARS, ai_key_present, llm_allowed, engine_mode
print('engine_mode:', engine_mode())
print('AI_KEY_ENV_VARS:', AI_KEY_ENV_VARS)
print('ai_key_present:', ai_key_present())
print('llm_allowed:', llm_allowed())
assert llm_allowed() == True, 'llm_allowed should be True in hybrid mode with key'
" || { err "Engine config verification failed"; exit 1; }
    ok "Engine config verified"

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
    # `create_session_token` no longer exists, so this step raised ImportError on
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

# Summary
log "Deployment complete!"
echo
echo "  Health:      $HEALTH_ENDPOINT"
echo "  API Base:    http://127.0.0.1:8001/api/v1"
echo "  Assistant:   POST /api/v1/assistant/chat"
echo "  Admin:       GET /api/v1/admin/ai-providers"
echo
echo "  Logs:        docker logs -f zolai-core-api-1"
echo "  Shell:       docker exec -it zolai-core-api-1 bash"
ok "All done!"
