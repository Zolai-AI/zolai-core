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

# 2. Start the API on the sidecar port (:8000, see zolai-tauri/src-tauri/src/sidecar.rs)
#    through the shared control script, so stop/restart/logs stay consistent.
STACK="$SCRIPT_DIR/scripts/dev/local-stack.sh"
export ZOLAI_API_PORT="${ZOLAI_DESKTOP_API_PORT:-8000}"
export ZOLAI_DEV_RUN_DIR="${ZOLAI_DESKTOP_RUN_DIR:-/tmp/zolai-desktop}"
bash "$STACK" start api || exit 1

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
echo -e "  Logs: ${ZOLAI_DEV_RUN_DIR}/api.log"
echo ""
echo -e "  To stop API later:"
echo -e "    ZOLAI_API_PORT=8000 ZOLAI_DEV_RUN_DIR=${ZOLAI_DEV_RUN_DIR} bash $STACK stop api"
echo ""
