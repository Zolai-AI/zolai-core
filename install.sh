#!/usr/bin/env bash
# ============================================================================
# Zolai Core - Server Installer (native systemd, NO Docker)
# ============================================================================
# One-command install & deploy for a fresh server. There are NO containers: the
# API runs as a systemd service on 127.0.0.1 and nginx terminates TLS in front.
#
# Usage: ./install.sh [options]
#
# Options:
#   --dir DIR        Install directory            (default: /home/ubuntu/zolai-core)
#   --domain DOMAIN  Public domain                (default: api.zolai.space)
#   --email EMAIL    Admin email for Let's Encrypt
#   --user NAME      User that owns the service   (default: sudo caller, else ubuntu/root)
#   --service NAME   systemd unit name            (default: zolai-core-dev)
#   --port PORT      API port behind nginx        (default: 8001)
#   --branch BRANCH  Git branch to deploy         (default: main)
#   --no-ssl         Skip SSL/TLS (dev only)
#   --skip-install   Reuse the existing venv, skip dependency install
#   --help           Show help
#
# Prerequisites:
#   - Ubuntu 22.04+ / Debian 12+ (systemd)
#   - Python 3.14+ available (scripts/install.sh chooses CPU-only vs CUDA torch)
#   - Ports 80/443 open for Let's Encrypt
#   - DNS A record pointing at this server
#
# Runtime topology provisioned here — this mirrors the live pcore-server unit:
#   checkout   <dir>
#   venv       <dir>/.venv-dev            (scripts/install.sh, VENV_DIR override)
#   env file   <dir>/.env.dev             (the unit's EnvironmentFile)
#   unit       zolai-core-dev.service
#   listen     127.0.0.1:8001
#   startup    ~13s to become ready, so readiness is POLLED, never `sleep`ed
#
# The Docker-era steps are gone on purpose: `--skip-build`/`docker compose build`
# had nothing native to skip, and there is no image to build. The CPU/GPU torch
# detection lives in scripts/install.sh (the same path local setup uses) and is
# invoked unchanged, so local development is unaffected.
# ============================================================================

set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────
REPO="https://github.com/Zolai-AI/zolai-core.git"
BRANCH="main"
INSTALL_DIR="/home/ubuntu/zolai-core"
DOMAIN="api.zolai.space"
EMAIL=""
SERVICE="zolai-core-dev"
API_PORT="8001"
VENV_NAME=".venv-dev"
ENV_FILE_NAME=".env.dev"
SERVICE_USER=""
SKIP_SSL=false
SKIP_INSTALL=false
HEALTH_TIMEOUT=30

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log() { echo -e "${BLUE}[$(date +'%H:%M:%S')]${NC} $*"; }
ok() { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err() { echo -e "${RED}[ERR]${NC} $*"; }

usage() { grep '^#' "$0" | cut -c3-; exit 0; }

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --dir) INSTALL_DIR="$2"; shift 2 ;;
        --domain) DOMAIN="$2"; shift 2 ;;
        --email) EMAIL="$2"; shift 2 ;;
        --user) SERVICE_USER="$2"; shift 2 ;;
        --service) SERVICE="$2"; shift 2 ;;
        --port) API_PORT="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        --no-ssl) SKIP_SSL=true; shift ;;
        --skip-install) SKIP_INSTALL=true; shift ;;
        --help) usage ;;
        *) err "Unknown: $1"; usage ;;
    esac
done

# ── Checks ──────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || { err "Run as root (sudo)"; exit 1; }
command -v git >/dev/null || { err "git is required"; exit 1; }
command -v systemctl >/dev/null || { err "systemd is required — there is no container fallback"; exit 1; }
if [[ "$SKIP_SSL" == "false" && "$DOMAIN" == "api.zolai.space" && -z "$EMAIL" ]]; then
    err "Set --email for Let's Encrypt (or pass --domain/--no-ssl)"; exit 1
fi

# Owner of the checkout and of the systemd service.
if [[ -z "$SERVICE_USER" ]]; then
    if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
        SERVICE_USER="$SUDO_USER"
    elif id -u ubuntu >/dev/null 2>&1; then
        SERVICE_USER="ubuntu"
    else
        SERVICE_USER="root"
    fi
fi
id -u "$SERVICE_USER" >/dev/null 2>&1 || { err "user '$SERVICE_USER' does not exist"; exit 1; }

# Run a command as the service owner (git clone / venv must not be root-owned
# when the unit runs as a normal user, or the service cannot write data/).
as_user() {
    if [[ "$SERVICE_USER" == "root" ]]; then "$@"; else sudo -u "$SERVICE_USER" -H "$@"; fi
}

log "Installing Zolai Core to $INSTALL_DIR (branch: $BRANCH, unit: $SERVICE, user: $SERVICE_USER)"

# ── System deps ─────────────────────────────────────────────────────────
log "Installing system packages..."
apt-get update -qq
apt-get install -y -qq git curl nginx certbot python3-certbot-nginx ufw python3-venv rsync >/dev/null
ok "System packages installed"

# ── Checkout ────────────────────────────────────────────────────────────
# Update in place when it is already a git checkout: `git merge origin/<branch>`
# is additive and preserves any server-only commits. Never wipe a checkout.
if [[ -d "$INSTALL_DIR/.git" ]]; then
    log "Updating existing checkout..."
    git -C "$INSTALL_DIR" fetch --prune origin
    git -C "$INSTALL_DIR" checkout "$BRANCH"
    git -C "$INSTALL_DIR" merge --no-edit "origin/$BRANCH"
    ok "Checkout at $(git -C "$INSTALL_DIR" rev-parse --short HEAD)"
else
    if [[ -e "$INSTALL_DIR" ]]; then
        err "$INSTALL_DIR exists but is not a git checkout — move it aside first"
        exit 1
    fi
    log "Cloning $REPO @ $BRANCH..."
    mkdir -p "$(dirname "$INSTALL_DIR")"
    as_user git clone --branch "$BRANCH" "$REPO" "$INSTALL_DIR"
    ok "Repo cloned"
fi
cd "$INSTALL_DIR"

# ── venv + dependencies (CPU/GPU torch detection -> scripts/install.sh) ──
if [[ "$SKIP_INSTALL" == "false" ]]; then
    log "Creating $VENV_NAME and installing dependencies (auto CPU/GPU)..."
    if [[ "$SERVICE_USER" == "root" ]]; then
        VENV_DIR="$VENV_NAME" bash scripts/install.sh --no-dev
    else
        sudo -u "$SERVICE_USER" -H env VENV_DIR="$VENV_NAME" bash scripts/install.sh --no-dev
    fi
    ok "Dependencies installed into $VENV_NAME"
else
    log "Skipping dependency install (--skip-install)"
fi

if [[ "$SERVICE_USER" != "root" ]]; then
    chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"
fi

# ── Environment file (never clobber a live one) ─────────────────────────
ENV_FILE="$INSTALL_DIR/$ENV_FILE_NAME"
if [[ -f "$ENV_FILE" ]]; then
    warn "$ENV_FILE already exists — leaving it untouched"
else
    log "Generating $ENV_FILE..."
    cat > "$ENV_FILE" <<ENVEOF
# Generated by install.sh on $(date -u)
# Native runtime: systemd unit '$SERVICE' (uvicorn, no container).
ZOLAI_API_HOST=127.0.0.1
ZOLAI_API_PORT=$API_PORT
ZOLAI_DATA_ROOT=$INSTALL_DIR/data
ZOLAI_DB_PATH=$INSTALL_DIR/data/zolai.db
ZOLAI_ENGINE_MODE=hybrid
ZOLAI_API_AUTH=enforce
ZOLAI_AUTH_SESSIONS=on
ZOLAI_SESSION_TTL_HOURS=12
ZOLAI_LOG_LEVEL=INFO
ZOLAI_CORS_ORIGINS=*
ZOLAI_CB_ENABLED=true
ZOLAI_CB_FAILURE_THRESHOLD=5
ZOLAI_CB_TIMEOUT=30
ZOLAI_CB_SUCCESS_THRESHOLD=2
ZOLAI_NOTIFICATIONS_ENABLED=true

# Brain API (update these)
AI_BRAIN_URL=https://brain.pcore-system.site/v1
AI_BRAIN_API_KEY=CHANGE_ME

# SMTP (update these)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_TLS=true
SMTP_SSL=true
SMTP_USER=your-email@gmail.com
SMTP_PASS=your-app-password
SMTP_FROM=pcore.system@gmail.com
ADMIN_EMAILS=your-email@gmail.com

# Postgres (optional - leave empty for SQLite)
ZOLAI_PG_URL=
ENVEOF
    chown "$SERVICE_USER":"$SERVICE_USER" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    ok "$ENV_FILE created (EDIT IT: vim $ENV_FILE)"
fi

# ── systemd unit (replaces `docker compose up`) ─────────────────────────
UNIT="/etc/systemd/system/$SERVICE.service"
log "Writing $UNIT..."
cat > "$UNIT" <<UNITEOF
[Unit]
Description=Zolai Core API (native, uvicorn)
Documentation=https://github.com/Zolai-AI/zolai-core
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/$ENV_FILE_NAME
ExecStart=$INSTALL_DIR/$VENV_NAME/bin/uvicorn zolai.api.server:app --host 127.0.0.1 --port $API_PORT
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNITEOF
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
ok "Unit installed: $UNIT"

# ── Start + poll /health (startup is ~13s — never a fixed sleep) ────────
log "Starting $SERVICE..."
systemctl restart "$SERVICE"

log "Waiting for http://127.0.0.1:$API_PORT/health (timeout ${HEALTH_TIMEOUT}s; ~13s is normal)..."
HEALTHY=false
DEADLINE=$(( SECONDS + HEALTH_TIMEOUT ))
while (( SECONDS < DEADLINE )); do
    if curl -sf --max-time 3 "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1; then
        HEALTHY=true
        break
    fi
    sleep 1
done
if [[ "$HEALTHY" != true ]]; then
    err "API failed to become healthy within ${HEALTH_TIMEOUT}s"
    systemctl status "$SERVICE" --no-pager -l | tail -30 || true
    journalctl -u "$SERVICE" -n 50 --no-pager || true
    exit 1
fi
ok "API healthy"

# ── Nginx + SSL ─────────────────────────────────────────────────────────
if [[ "$SKIP_SSL" == "false" ]]; then
    log "Configuring Nginx + SSL for $DOMAIN..."
    cat > /etc/nginx/sites-available/zolai-api <<NGINXEOF
server {
    listen 80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://\$host\$request_uri; }
}
NGINXEOF
    ln -sf /etc/nginx/sites-available/zolai-api /etc/nginx/sites-enabled/
    nginx -t && systemctl reload nginx

    if [[ -n "$EMAIL" ]]; then
        certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect
        ok "SSL certificate installed"
    else
        warn "Skipping certbot (no email provided)"
    fi
else
    warn "SSL skipped (--no-ssl)"
    cat > /etc/nginx/sites-available/zolai-api <<NGINXEOF
server {
    listen 80;
    server_name $DOMAIN;
    location / { proxy_pass http://127.0.0.1:$API_PORT; proxy_set_header Host \$host; proxy_set_header X-Real-IP \$remote_addr; }
}
NGINXEOF
    ln -sf /etc/nginx/sites-available/zolai-api /etc/nginx/sites-enabled/
    nginx -t && systemctl reload nginx
fi

# ── Firewall ────────────────────────────────────────────────────────────
ufw allow 22/tcp; ufw allow 80/tcp; ufw allow 443/tcp; ufw --force enable >/dev/null
ok "Firewall configured"

# ── Verify (poll HTTPS; the origin already answered /health above) ──────
log "Running verification..."
HTTPS_OK=false
for _ in 1 2 3 4 5; do
    if curl -sf --max-time 5 "https://$DOMAIN/api/v1/health" >/dev/null 2>&1; then
        HTTPS_OK=true
        break
    fi
    sleep 2
done
$HTTPS_OK && ok "HTTPS health OK" || warn "HTTPS check failed (check DNS/SSL)"

# ── Summary ─────────────────────────────────────────────────────────────
echo
ok "Install complete!"
echo
echo "  API:        https://$DOMAIN/api/v1"
echo "  Health:     https://$DOMAIN/api/v1/health"
echo "  Assistant:  POST https://$DOMAIN/api/v1/assistant/chat"
echo "  Admin:      https://$DOMAIN/api/v1/admin"
echo
echo "  Checkout:   $INSTALL_DIR"
echo "  Unit:       $SERVICE        (EnvironmentFile: $ENV_FILE)"
echo "  Config:     $ENV_FILE       (EDIT ME)"
echo "  Logs:       journalctl -u $SERVICE -f"
echo "  Restart:    sudo systemctl restart $SERVICE"
echo "  Deploy:     $INSTALL_DIR/scripts/deploy.sh   (git fetch + merge origin/$BRANCH)"
echo
warn "IMPORTANT: Edit $ENV_FILE with real API keys!"
warn "Then: sudo systemctl restart $SERVICE"
