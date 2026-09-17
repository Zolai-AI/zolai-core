#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  Build Zolai API sidecar binary via PyInstaller
#
#  Produces a single self-contained binary at dist/zolai-api
#  that the Tauri desktop app launches as a sidecar process.
#
#  Usage:
#    bash scripts/desktop/build_sidecar.sh
#    bash scripts/desktop/build_sidecar.sh --platform linux
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"

# --- Platform detection ---
PLATFORM="${1:---platform}"
if [[ "$PLATFORM" == "--platform" ]]; then
  case "$(uname -s)" in
    Linux*)  PLATFORM="linux"  ;;
    Darwin*) PLATFORM="macos"  ;;
    MINGW*|MSYS*|CYGWIN*) PLATFORM="windows" ;;
    *)       PLATFORM="linux"  ;;
  esac
fi

echo "╔══════════════════════════════════════════╗"
echo "║  Building Zolai API Sidecar ($PLATFORM)    ║"
echo "╚══════════════════════════════════════════╝"

# --- Ensure venv + PyInstaller ---
if [[ ! -d ".venv" ]]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi
source .venv/bin/activate

pip install --quiet pyinstaller

# --- Install desktop dependencies ---
pip install --quiet -e ".[desktop,webapi]"

# --- Build exclusions (keep binary small) ---
EXCLUDES=(
  "torch"
  "transformers"
  "datasets"
  "peft"
  "trl"
  "accelerate"
  "bitsandbytes"
  "sentence_transformers"
  "sentencepiece"
  "sklearn"
  "scipy"
  "matplotlib"
  "pandas"
  "numpy"
  "kaggle"
  "kagglehub"
  "huggingface_hub"
  "mistralai"
  "PIL"
  "cv2"
  "tensorflow"
  "jax"
  "flax"
  "tkinter"
  "PyQt5"
  "PyQt6"
  "gi"  # GTK
)

EXCLUDE_ARGS=()
for pkg in "${EXCLUDES[@]}"; do
  EXCLUDE_ARGS+=("--exclude-module" "$pkg")
done

# --- Build binary ---
echo ""
echo "Building binary..."
pyinstaller \
  --onefile \
  --name zolai-api \
  --distpath dist \
  --workpath build/pyinstaller \
  --specpath build \
  --clean \
  "${EXCLUDE_ARGS[@]}" \
  --hidden-import "zolai.api.desktop_app" \
  --hidden-import "zolai.config" \
  --hidden-import "zolai.data.database" \
  --hidden-import "zolai.data.migrations" \
  --hidden-import "zolai.api.desktop_router" \
  --hidden-import "zolai.api.foundation_router" \
  --hidden-import "zolai.api.jsonl_router" \
  --hidden-import "zolai.plugins" \
  --hidden-import "zolai.plugins.gemini_plugin" \
  --collect-submodules "zolai.api" \
  --collect-submodules "zolai.plugins" \
  zolai/api/desktop_app.py

# --- Verify ---
BINARY="dist/zolai-api"
if [[ -f "$BINARY" ]]; then
  SIZE=$(du -h "$BINARY" | cut -f1)
  echo ""
  echo "✅ Build successful!"
  echo "   Binary: $BINARY ($SIZE)"
  echo "   Platform: $PLATFORM"
  echo ""
  echo "Test: $BINARY --help"
else
  echo ""
  echo "❌ Build failed — binary not found at $BINARY"
  exit 1
fi
