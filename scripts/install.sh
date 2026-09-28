#!/usr/bin/env bash
# Zolai-AI installer — detects Python 3.14+ and NVIDIA GPU, then installs the
# right torch build (CPU-only vs CUDA) with the correct extras.
#
# Usage:
#   ./scripts/install.sh              # auto-detect GPU/CPU
#   ./scripts/install.sh --cpu        # force CPU-only
#   ./scripts/install.sh --gpu        # force CUDA (fails if no NVIDIA card)
#   ./scripts/install.sh --no-dev     # skip dev extras (pytest/ruff)
#   ./scripts/install.sh --base       # base only, no torch at all
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="auto"
EXTRAS="ml"
WITH_DEV=1
for arg in "$@"; do
  case "$arg" in
    --cpu) MODE="cpu" ;;
    --gpu) MODE="gpu" ;;
    --base) MODE="base" ;;
    --no-dev) WITH_DEV=0 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (see --help)"; exit 2 ;;
  esac
done

# --- 1. Python 3.14+ -------------------------------------------------------
PY=""
for cand in python3.14 python3.15 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    ver=$("$cand" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
    case "$ver" in
      3.1[4-9]|3.[2-9]*) PY="$cand"; break ;;
    esac
  fi
done
if [ -z "$PY" ]; then
  echo "ERROR: Python 3.14+ not found. Install it first, e.g.:" >&2
  echo "  brew install python@3.14   # macOS" >&2
  echo "  apt install python3.14     # Debian/Ubuntu (or deadsnakes PPA)" >&2
  exit 1
fi
echo "==> Python: $($PY --version)"

# --- 2. GPU detection ------------------------------------------------------
detect_gpu() {
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    return 0
  fi
  # PCI check fallback (nvidia-smi missing but driver present)
  if command -v lspci >/dev/null 2>&1 && lspci 2>/dev/null | grep -qi 'nvidia\|geforce\|rtx\|quadro'; then
    return 0
  fi
  return 1
}

if [ "$MODE" = "auto" ]; then
  if detect_gpu; then MODE="gpu"; else MODE="cpu"; fi
fi
echo "==> Install mode: $MODE"

case "$MODE" in
  gpu)
    if ! detect_gpu; then
      echo "ERROR: --gpu requested but no NVIDIA GPU detected." >&2
      exit 1
    fi
    TORCH_INDEX="https://download.pytorch.org/whl/cu130"
    EXTRAS="gpu"   # gpu extra includes ml
    LABEL="CUDA 13.0 (GPU)"
    ;;
  cpu)
    TORCH_INDEX="https://download.pytorch.org/whl/cpu"
    EXTRAS="ml"
    LABEL="CPU-only"
    ;;
  base)
    TORCH_INDEX=""
    EXTRAS=""
    LABEL="base (no torch)"
    ;;
esac
[ "$WITH_DEV" = "1" ] && EXTRAS="${EXTRAS:+$EXTRAS,}dev"
echo "==> Torch build: $LABEL"
echo "==> Extras: ${EXTRAS:-none}"

# --- 3. venv ---------------------------------------------------------------
if [ ! -x .venv/bin/python ]; then
  echo "==> Creating .venv"
  if command -v uv >/dev/null 2>&1; then
    uv venv --python "$PY" .venv
  else
    "$PY" -m venv .venv
  fi
fi
VPY=.venv/bin/python

# --- 4. Install ------------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
  if [ -n "$EXTRAS" ]; then
    if [ -n "$TORCH_INDEX" ]; then
      uv pip install --python "$VPY" --extra-index-url "$TORCH_INDEX" \
        --index-strategy unsafe-best-match -e ".[$EXTRAS]"
    else
      uv pip install --python "$VPY" -e ".[$EXTRAS]"
    fi
  else
    uv pip install --python "$VPY" -e .
  fi
else
  # pip fallback
  if [ -n "$EXTRAS" ]; then
    if [ -n "$TORCH_INDEX" ]; then
      "$VPY" -m pip install --extra-index-url "$TORCH_INDEX" -e ".[$EXTRAS]"
    else
      "$VPY" -m pip install -e ".[$EXTRAS]"
    fi
  else
    "$VPY" -m pip install -e .
  fi
fi

# --- 5. Verify -------------------------------------------------------------
echo "==> Verifying"
"$VPY" - <<'PYEOF'
import importlib.util
spec = importlib.util.find_spec("torch")
if spec is None:
    print("    torch: not installed (base mode) OK")
else:
    import torch
    print(f"    torch {torch.__version__} | cuda available: {torch.cuda.is_available()}")
PYEOF
"$VPY" -c "import fastapi, pydantic, sqlalchemy, sklearn; print('    core imports OK')"
echo "==> Done. Activate with: source .venv/bin/activate"
