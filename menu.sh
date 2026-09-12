#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════
#  Zolai Core — Quick Menu
# ═══════════════════════════════════════════════════════
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE="$(cd "$DIR/.." && pwd)"

R='\033[0;31m' G='\033[0;32m' Y='\033[1;33m' C='\033[0;36m' M='\033[0;35m' NC='\033[0m'

while true; do
  clear
  echo -e "${C}╔══════════════════════════════════════════╗${NC}"
  echo -e "${C}║${NC}  ${M}🦜 Zolai Core — Quick Menu${NC}             ${C}║${NC}"
  echo -e "${C}╚══════════════════════════════════════════╝${NC}"
  echo ""
  echo -e "  ${G}1${NC}) 🌐 Start API server (port 8000)"
  echo -e "  ${G}2${NC}) 🖥️  Launch desktop app"
  echo -e "  ${G}3${NC}) 🌐+🖥️  Start server + desktop"
  echo -e "  ${G}4${NC}) 🧪 Run tests"
  echo -e "  ${G}5${NC}) 📊 DB health check"
  echo -e "  ${G}6${NC}) 🔍 CLI info"
  echo -e "  ${G}7${NC}) 📖 Bible study menu (datasets)"
  echo -e "  ${G}8${NC}) 🔧 Install dev dependencies"
  echo -e "  ${G}9${NC}) 🔨 Build desktop app"
  echo -e "  ${G}0${NC}) Exit"
  echo ""
  read -p "  Select: " choice
  case "$choice" in
    1)
      echo -e "${G}Starting API server...${NC}"
      source "$DIR/.venv/bin/activate" 2>/dev/null || true
      cd "$DIR"
      python -m uvicorn zolai.api.server:app --host 0.0.0.0 --port 8000
      ;;
    2)
      echo -e "${G}Launching desktop app...${NC}"
      source "$DIR/.venv/bin/activate" 2>/dev/null || true
      cd "$DIR" && python -m zolai desktop
      ;;
    3)
      echo -e "${G}Starting server + desktop...${NC}"
      source "$DIR/.venv/bin/activate" 2>/dev/null || true
      cd "$DIR"
      nohup python -m uvicorn zolai.api.server:app --host 0.0.0.0 --port 8000 > /tmp/zolai-api.log 2>&1 &
      echo "$!" > /tmp/zolai-api.pid
      sleep 2
      if curl -s http://localhost:8000/health >/dev/null 2>&1; then
        echo -e "  ${G}✅ API running${NC}"
        cd "$DIR" && python -m zolai desktop
      else
        echo -e "  ${R}❌ API failed${NC}"
      fi
      ;;
    4)
      echo -e "${G}Running tests...${NC}"
      source "$DIR/.venv/bin/activate" 2>/dev/null || true
      cd "$DIR" && python -m pytest tests/ -q --tb=short
      read -p "Press Enter..."
      ;;
    5)
      echo -e "${G}DB Health Check...${NC}"
      source "$DIR/.venv/bin/activate" 2>/dev/null || true
      cd "$DIR" && python -c "
import sqlite3, os
p = os.path.join('$(dirname $DIR)/data', 'zolai.db')
sz = os.path.getsize(p)/1024/1024
conn = sqlite3.connect(p, timeout=5)
c = conn.cursor()
print(f'  DB: {sz:.1f} MB')
c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")
for r in c.fetchall():
    t = r[0]
    if t == 'sqlite_sequence': continue
    c.execute(f'SELECT COUNT(*) FROM [{t}]')
    print(f'  {t:25s}: {c.fetchone()[0]:>10,}')
conn.close()
"
      read -p "Press Enter..."
      ;;
    6)
      source "$DIR/.venv/bin/activate" 2>/dev/null || true
      cd "$DIR" && python -m zolai info
      read -p "Press Enter..."
      ;;
    7)
      bash "$WORKSPACE/zolai-datasets/scripts/bible/menu.sh"
      ;;
    8)
      echo -e "${G}Installing dependencies...${NC}"
      source "$DIR/.venv/bin/activate" 2>/dev/null || true
      cd "$DIR" && pip install -e ".[dev]"
      read -p "Press Enter..."
      ;;
    9)
      echo -e "${G}Building desktop app...${NC}"
      cd "$WORKSPACE/zolai-tauri/src-tauri" && cargo build --release
      read -p "Press Enter..."
      ;;
    0)
      echo -e "${G}Goodbye!${NC}"; exit 0 ;;
    *) echo -e "${R}Invalid${NC}"; sleep 1 ;;
  esac
done
