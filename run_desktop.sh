#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════
#  Zolai Desktop Launcher
#  Starts API server + launches Tauri desktop app
# ═══════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE="$(cd "$SCRIPT_DIR/.." && pwd)"
TAURI_DIR="$WORKSPACE/zolai-tauri"

R='\033[0;31m' G='\033[0;32m' Y='\033[1;33m' C='\033[0;36m' NC='\033[0m'

echo -e "${C}╔══════════════════════════════════════════╗${NC}"
echo -e "${C}║${NC}  ${G}🦜 Zolai Desktop Launcher${NC}                ${C}║${NC}"
echo -e "${C}╚══════════════════════════════════════════╝${NC}"
echo ""

# 1. Activate venv
echo -e "${Y}1. Activating Python venv...${NC}"
source "$SCRIPT_DIR/.venv/bin/activate" 2>/dev/null || true

# 2. Check API is already running
if curl -s http://localhost:8000/health >/dev/null 2>&1; then
  echo -e "  ${G}✅ API already running on :8000${NC}"
else
  echo -e "  ${Y}Starting API server on :8000...${NC}"
  cd "$SCRIPT_DIR"
  nohup python -m uvicorn zolai.api.server:app --host 0.0.0.0 --port 8000 > /tmp/zolai-api.log 2>&1 &
  echo "$!" > /tmp/zolai-api.pid
  sleep 2
  if curl -s http://localhost:8000/health >/dev/null 2>&1; then
    echo -e "  ${G}✅ API started (PID: $(cat /tmp/zolai-api.pid))${NC}"
  else
    echo -e "  ${R}❌ API failed to start. Check: /tmp/zolai-api.log${NC}"
    exit 1
  fi
fi

# 3. Launch Tauri app (skip AppImage bundle due to network)
echo ""
echo -e "${Y}2. Launching Desktop App...${NC}"
BIN="$TAURI_DIR/src-tauri/target/release/zolai-desktop"
if [ -x "$BIN" ]; then
  echo -e "  ${G}Running compiled binary...${NC}"
  "$BIN" &
else
  echo -e "  ${R}❌ No compiled binary found${NC}"
  echo -e "  Build first: cd zolai-tauri/src-tauri && cargo build --release"
  exit 1
fi

echo ""
echo -e "${G}Desktop app launched!${NC}"
echo -e "  API: http://localhost:8000"
echo -e "  PID file: /tmp/zolai-api.pid"
echo ""
echo -e "  To stop API later: kill \$(cat /tmp/zolai-api.pid)"
echo ""
