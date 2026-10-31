#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════
#  Zolai Core — Quick Menu
# ═══════════════════════════════════════════════════════
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE="$(cd "$DIR/.." && pwd)"
STACK="$DIR/scripts/dev/local-stack.sh"

R='\033[0;31m' G='\033[0;32m' Y='\033[1;33m' C='\033[0;36m' M='\033[0;35m' NC='\033[0m'

# Never let a failed start/stop abort the menu loop.
stack() {
  bash "$STACK" "$@" || true
  read -p "Press Enter..."
}

prompt_target() {
  read -p "  Target [all/api/studio] (all): " t
  echo "${t:-all}"
}

while true; do
  clear
  echo -e "${C}╔══════════════════════════════════════════╗${NC}"
  echo -e "${C}║${NC}  ${M}🦜 Zolai Core — Quick Menu${NC}             ${C}║${NC}"
  echo -e "${C}╚══════════════════════════════════════════╝${NC}"
  echo ""
  echo -e "  ${Y}── Local stack (mirrors pcore-server units) ──${NC}"
  echo -e "  ${G}1${NC}) 🚀 Start API + Studio   (:8001 / :5173)"
  echo -e "  ${G}2${NC}) 🌐 Start API only       (:8001)"
  echo -e "  ${G}3${NC}) 🎛️  Start Studio only    (:5173)"
  echo -e "  ${G}4${NC}) 🛑 Stop                 (api|studio|all)"
  echo -e "  ${G}5${NC}) 🔁 Restart              (api|studio|all)"
  echo -e "  ${G}6${NC}) 📊 Stack status"
  echo -e "  ${G}7${NC}) 📜 Stack logs"
  echo ""
  echo -e "  ${Y}── App, data and maintenance ──${NC}"
  echo -e "  ${G}8${NC}) 🖥️  Launch desktop app"
  echo -e "  ${G}9${NC}) 🌐+🖥️  Start API (:8000) + desktop"
  echo -e "  ${G}10${NC}) 🧪 Run tests"
  echo -e "  ${G}11${NC}) 📊 DB health check"
  echo -e "  ${G}12${NC}) 🔍 CLI info"
  echo -e "  ${G}13${NC}) 📖 Bible study menu (datasets)"
  echo -e "  ${G}14${NC}) 🔧 Smart install (auto-detect system)"
  echo -e "  ${G}15${NC}) 🔨 Build desktop app"
  echo -e "  ${G}0${NC}) Exit"
  echo ""
  read -p "  Select: " choice
  case "$choice" in
    1) stack start all ;;
    2) stack start api ;;
    3) stack start studio ;;
    4) stack stop "$(prompt_target)" ;;
    5) stack restart "$(prompt_target)" ;;
    6) stack status ;;
    7) stack logs all 120 ;;
    8)
      echo -e "${G}Launching desktop app...${NC}"
      source "$DIR/.venv/bin/activate" 2>/dev/null || true
      cd "$DIR" && zolai desktop
      ;;
    9)
      bash "$DIR/run_desktop.sh" || true
      read -p "Press Enter..."
      ;;
    10)
      echo -e "${G}Running tests...${NC}"
      source "$DIR/.venv/bin/activate" 2>/dev/null || true
      cd "$DIR" && python -m pytest tests/ -q --tb=short
      read -p "Press Enter..."
      ;;
    11)
      echo -e "${G}DB Health Check...${NC}"
      source "$DIR/.venv/bin/activate" 2>/dev/null || true
      cd "$DIR" && python -c "
from zolai.config import config
import sqlite3
p = config.paths.db
sz = p.stat().st_size/1024/1024
conn = sqlite3.connect(f'file:{p}?mode=ro', uri=True, timeout=5)
c = conn.cursor()
print(f'  DB: {p} ({sz:.1f} MB)')
c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")
for r in c.fetchall():
    t = r[0]
    if t == 'sqlite_sequence': continue
    c.execute(f'SELECT COUNT(*) FROM [{t}]')
    print(f'  {t:25s}: {c.fetchone()[0]:>10,}')
if 'knowledge_vectors' in {r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")}:
    c.execute('SELECT source, COUNT(*) FROM knowledge_vectors GROUP BY source ORDER BY 2 DESC')
    for src, n in c.fetchall():
        print(f'  vectors[{src:20s}]: {n:>10,}')
conn.close()
"
      read -p "Press Enter..."
      ;;
    12)
      echo -e "${G}CLI info...${NC}"
      source "$DIR/.venv/bin/activate" 2>/dev/null || true
      cd "$DIR" && zolai info
      read -p "Press Enter..."
      ;;
    13)
      bash "$WORKSPACE/zolai-datasets/scripts/bible/menu.sh"
      ;;
    14)
      echo -e "${G}Running smart installer...${NC}"
      echo -e "  ${Y}This will detect your system and install only what's needed.${NC}"
      echo -e "  ${Y}No NVIDIA GPU? → CPU-only packages (~300MB).${NC}"
      echo -e "  ${Y}Have NVIDIA GPU? → GPU packages (~5GB).${NC}"
      echo ""
      read -p "  Continue? (y/N): " confirm
      if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
        source "$DIR/.venv/bin/activate" 2>/dev/null || true
        python "$DIR/scripts/smart_install.py"
      fi
      read -p "Press Enter..."
      ;;
    15)
      echo -e "${G}Building desktop app...${NC}"
      cd "$WORKSPACE/zolai-tauri/src-tauri" && cargo build --release
      read -p "Press Enter..."
      ;;
    0)
      echo -e "${G}Goodbye!${NC}"; exit 0 ;;
    *) echo -e "${R}Invalid${NC}"; sleep 1 ;;
  esac
done
