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
    ADMIN_TOKEN=$(docker exec zolai-core-api-1 python3 -c "
from zolai.data.database import get_manager
from zolai.api.auth import create_session_token
mgr = get_manager()
with mgr.engine.connect() as conn:
    from sqlalchemy import text
    row = conn.execute(text(\"SELECT id FROM users WHERE username='peterlianpi'\")).fetchone()
    if row:
        token = create_session_token(row[0], 'admin', ['user:manage'])
        print(token)
")
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
