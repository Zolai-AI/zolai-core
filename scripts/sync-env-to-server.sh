#!/usr/bin/env bash
# sync-env-to-server.sh — push local env values to pcore-server, leak-safe.
#
# Two targets, both backed up first and left mode 600:
#   .env      → replaced wholesale (the local file is the authority; its
#               non-secret settings already match the server copy)
#   .env.dev  → the systemd unit's EnvironmentFile: only the keys in
#               SYNC_KEYS are upserted. Prod-only settings (SMTP, Brain,
#               Redis, …) are never overwritten by a local value.
#
# Leak safety:
#   * values travel over ssh stdin — never argv (no `ps` exposure);
#   * remote temp files are created under `umask 077` and renamed atomically;
#   * nothing is ever echoed: output is key names, presence and file sizes.
#
# Usage:
#   scripts/sync-env-to-server.sh                 # dry-run (default)
#   scripts/sync-env-to-server.sh --apply         # write, with backups
#   scripts/sync-env-to-server.sh --keys "OPENROUTER_API_KEY HF_TOKEN"
#
# Env overrides:
#   ZOLAI_SYNC_HOST       SSH alias/host      (default: pcore-server)
#   ZOLAI_SYNC_PATH       remote checkout     (default: /home/ubuntu/zolai-core)
#   ZOLAI_SYNC_SSH_KEY    identity file       (default: ~/.ssh/LightsailDefaultKey-ap-southeast-1.pem)
#   ZOLAI_SYNC_KEYS       keys for .env.dev   (default: OPENROUTER_API_KEY)
set -euo pipefail
umask 077

HOST="${ZOLAI_SYNC_HOST:-pcore-server}"
REMOTE_PATH="${ZOLAI_SYNC_PATH:-/home/ubuntu/zolai-core}"
SSH_KEY="${ZOLAI_SYNC_SSH_KEY:-$HOME/.ssh/LightsailDefaultKey-ap-southeast-1.pem}"
SYNC_KEYS="${ZOLAI_SYNC_KEYS:-OPENROUTER_API_KEY}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOCAL_ENV="${REPO_ROOT}/.env"

APPLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --dry-run|-n) APPLY=0; shift ;;
    --keys) SYNC_KEYS="${2:-}"; shift 2 ;;
    --help|-h) sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "ERROR: unknown argument '$1' (try --help)" >&2; exit 2 ;;
  esac
done

SSH_OPTS=()
if [[ -f "${SSH_KEY}" ]]; then SSH_OPTS=(-i "${SSH_KEY}" -o IdentitiesOnly=yes)
else SSH_OPTS=(-o IdentitiesOnly=no); fi

sshx() { ssh "${SSH_OPTS[@]}" "${HOST}" "$@"; }

# ── preflight ───────────────────────────────────────────────────────────────
[[ -f "${LOCAL_ENV}" ]] || { echo "ERROR: local env file not found: ${LOCAL_ENV}" >&2; exit 1; }

KEYS=()
read -r -a KEYS <<< "${SYNC_KEYS}"
[[ ${#KEYS[@]} -gt 0 ]] || { echo "ERROR: no keys to sync" >&2; exit 1; }

missing=()
for key in "${KEYS[@]}"; do
  [[ "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || {
    echo "ERROR: invalid env key name '${key}'" >&2; exit 1;
  }
  if ! grep -qE "^${key}=.+" "${LOCAL_ENV}"; then missing+=("${key}"); fi
done
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "ERROR: missing/empty in ${LOCAL_ENV}: ${missing[*]}" >&2
  exit 1
fi

echo "== sync-env-to-server =="
echo "  local env : ${LOCAL_ENV}"
echo "  server    : ${HOST}:${REMOTE_PATH}"
echo "  mode      : $([[ ${APPLY} -eq 1 ]] && echo 'APPLY' || echo 'DRY-RUN (no changes)')"

for f in .env .env.dev; do
  exists="$(sshx "test -f '${REMOTE_PATH}/${f}' && echo yes || echo no")"
  echo "  ${f}  : remote ${exists}"
done
for key in "${KEYS[@]}"; do
  present="$(sshx "grep -qE '^${key}=' '${REMOTE_PATH}/.env.dev' && echo present || echo absent")"
  echo "  ${key}: ${present} in .env.dev"
done
if [[ ${APPLY} -eq 0 ]]; then
  cat <<'EOF'
  would, in order:
    1. back up .env and .env.dev on the server (mode 600)
    2. replace .env from the local file (stdin transport)
    3. upsert the listed keys into .env.dev, preserving every other line
    4. chmod 600 all env files and verify key presence (names only)
  Nothing was changed.
EOF
  exit 0
fi

# ── apply ───────────────────────────────────────────────────────────────────
TS="$(date +%Y%m%dT%H%M%SZ)"
chmod 600 "${LOCAL_ENV}"

echo "[1/5] backup remote env files"
sshx "set -e; umask 077; cd '${REMOTE_PATH}'
  for f in .env .env.dev; do
    if [ -f \"\$f\" ]; then cp -p \"\$f\" \"\$f.bak-${TS}\"; chmod 600 \"\$f.bak-${TS}\"; fi
  done"

echo "[2/5] push .env (stdin)"
sshx "set -e; umask 077; cat > '${REMOTE_PATH}/.env.push'" < "${LOCAL_ENV}"
sshx "set -e; umask 077; cd '${REMOTE_PATH}'; chmod 600 .env.push; mv -f .env.push .env"
echo "  .env replaced"

echo "[3/5] merge keys into .env.dev"
TMP_DIR="$(mktemp -d /tmp/zolai-env-XXXXXX)"
trap 'rm -rf "${TMP_DIR}"' EXIT
sshx "cat '${REMOTE_PATH}/.env.dev'" > "${TMP_DIR}/.env.dev.remote"

python3 - "${TMP_DIR}/.env.dev.remote" "${LOCAL_ENV}" "${TMP_DIR}/.env.dev.merged" "${KEYS[@]}" <<'PY'
import sys

remote_path, local_path, out_path = sys.argv[1:4]
keys = sys.argv[4:]

values: dict[str, str] = {}
with open(local_path, encoding="utf-8") as fh:
    for raw in fh:
        line = raw.rstrip("\n")
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            values[name.strip()] = value

with open(remote_path, encoding="utf-8") as fh:
    lines = fh.read().splitlines()

seen: set[str] = set()
merged: list[str] = []
for line in lines:
    stripped = line.strip()
    if stripped and not stripped.startswith("#") and "=" in stripped:
        name = stripped.split("=", 1)[0].strip()
        if name in keys:
            merged.append(f"{name}={values[name]}")
            seen.add(name)
            continue
    merged.append(line)
for name in keys:
    if name not in seen:
        merged.append(f"{name}={values[name]}")

with open(out_path, "w", encoding="utf-8") as fh:
    fh.write("\n".join(merged) + "\n")
PY

sshx "set -e; umask 077; cat > '${REMOTE_PATH}/.env.dev.push'" < "${TMP_DIR}/.env.dev.merged"
sshx "set -e; umask 077; cd '${REMOTE_PATH}'; chmod 600 .env.dev.push; mv -f .env.dev.push .env.dev"
echo "  .env.dev updated (other lines untouched)"

echo "[4/5] tighten permissions"
sshx "set -e; cd '${REMOTE_PATH}'
  chmod 600 .env .env.dev
  chmod 600 .env.bak-${TS} .env.dev.bak-${TS} 2>/dev/null || true"

echo "[5/5] verify (names only — values are never printed)"
sshx "cd '${REMOTE_PATH}'
  for f in .env .env.dev; do
    echo \"  \$f \$(stat -c '%a, %s bytes' \"\$f\")\"
  done
  for k in ${KEYS[*]}; do
    for f in .env .env.dev; do
      if grep -qE \"^\$k=\" \"\$f\"; then echo \"  \$k present in \$f\"; fi
    done
  done"

echo "DONE — env synced to ${HOST}:${REMOTE_PATH} (backups: *.bak-${TS})."
