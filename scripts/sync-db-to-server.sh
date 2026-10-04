#!/usr/bin/env bash
# sync-db-to-server.sh — push local data/zolai.db → pcore-server.
#
# PAIR OF sync-db-from-server.sh — use BOTH around every update cycle:
#   local data work done  → THIS script (push, local → server)
#   server deploy/migrate → sync-db-from-server.sh (pull, server → local)
# Directions never merge automatically — always pick the side that is
# authoritative for that update (conflict policy: one direction per update).
#
# Safety (the server DB is open by the running API container):
#   (a) local  : consistent snapshot via `sqlite3 .backup` (WAL-safe)
#   (b) rsync  : push snapshot to a server temp path
#   (c) server : stop the API container (compose down api) — never swap a
#                hot file the process holds open
#   (d) server : back up existing DB first (data/backups/zolai-<ts>.db.gz)
#   (e) server : `PRAGMA integrity_check` on the pushed file — abort unless ok
#   (f) server : atomic replace, drop stale -wal/-shm, start container,
#                wait for /health to answer 200
#   (g) --dry-run prints the plan and exits without touching anything
#
# Usage:
#   scripts/sync-db-to-server.sh [--dry-run] [--no-restart-health] [--target PATH]
#
# Env overrides:
#   ZOLAI_SYNC_HOST        SSH alias/host      (default: pcore-server)
#   ZOLAI_SYNC_SERVER_DB   remote DB path      (default: /home/ubuntu/zolai-core/data/zolai.db)
#   ZOLAI_SYNC_SSH_KEY     identity file       (default: ~/.ssh/LightsailDefaultKey-ap-southeast-1.pem)
#   ZOLAI_SYNC_COMPOSE     compose file path   (default: /home/ubuntu/zolai-core/docker-compose.prod.yml)
#   ZOLAI_DATA_ROOT        local data dir root (default: <workspace>/data)
set -euo pipefail

SERVER_HOST="${ZOLAI_SYNC_HOST:-pcore-server}"
SERVER_DB="${ZOLAI_SYNC_SERVER_DB:-/home/ubuntu/zolai-core/data/zolai.db}"
SSH_KEY="${ZOLAI_SYNC_SSH_KEY:-$HOME/.ssh/LightsailDefaultKey-ap-southeast-1.pem}"
COMPOSE_FILE="${ZOLAI_SYNC_COMPOSE:-/home/ubuntu/zolai-core/docker-compose.prod.yml}"
REMOTE_TMP="$(dirname "${SERVER_DB}")/.zolai-push.db"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"        # zolai-core
WORKSPACE_ROOT="$(cd "${REPO_ROOT}/.." && pwd)"    # workspace root (canonical data/)

DRY_RUN=0
HEALTH=1
TARGET=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run|-n) DRY_RUN=1; shift ;;
    --no-restart-health) HEALTH=0; shift ;;
    --target) TARGET="${2:-}"; shift 2 ;;
    --help|-h) sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "ERROR: unknown argument '$1' (try --help)" >&2; exit 2 ;;
  esac
done

if [[ -n "${TARGET}" ]]; then
  LOCAL_DB="$TARGET"
elif [[ -n "${ZOLAI_DATA_ROOT:-}" ]]; then
  LOCAL_DB="${ZOLAI_DATA_ROOT%/}/zolai.db"
elif [[ -d "${WORKSPACE_ROOT}/data" ]]; then
  LOCAL_DB="${WORKSPACE_ROOT}/data/zolai.db"
else
  LOCAL_DB="${REPO_ROOT}/data/zolai.db"
fi

SSH_OPTS=()
if [[ -f "${SSH_KEY}" ]]; then SSH_OPTS=(-i "${SSH_KEY}" -o IdentitiesOnly=yes)
else SSH_OPTS=(-o IdentitiesOnly=no); fi

hsize() { if [[ -e "$1" ]]; then du -h "$1" | cut -f1; else echo "-"; fi; }

echo "== sync-db-to-server =="
echo "  local db  : ${LOCAL_DB}  (size: $(hsize "${LOCAL_DB}"))"
echo "  server db : ${SERVER_HOST}:${SERVER_DB}"
echo "  mode      : $([[ ${DRY_RUN} -eq 1 ]] && echo 'DRY-RUN (no changes)' || echo 'APPLY')"

if [[ ${DRY_RUN} -eq 1 ]]; then
  cat <<EOF
  [dry-run] would run, in order:
    1. sqlite3 ${LOCAL_DB} '.backup <tmp>'          # WAL-safe local snapshot
    2. rsync -a -e ssh <tmp> ${SERVER_HOST}:${REMOTE_TMP}
    3. ssh ${SERVER_HOST} docker compose -f ${COMPOSE_FILE} stop api   # never swap a hot file
    4. ssh ${SERVER_HOST} back up ${SERVER_DB} → data/backups/zolai-<ts>.db.gz
    5. ssh ${SERVER_HOST} PRAGMA integrity_check on ${REMOTE_TMP}  # must be 'ok'
    6. mv ${REMOTE_TMP} ${SERVER_DB} (+ drop stale -wal/-shm)
    7. start api container + wait for /health 200
  Nothing was changed.
EOF
  exit 0
fi

command -v sqlite3 >/dev/null 2>&1 || { echo "ERROR: sqlite3 not found in PATH" >&2; exit 1; }
command -v rsync   >/dev/null 2>&1 || { echo "ERROR: rsync not found in PATH" >&2; exit 1; }
[[ -f "${LOCAL_DB}" ]] || { echo "ERROR: local DB not found: ${LOCAL_DB}" >&2; exit 1; }

LOCAL_SNAP="$(mktemp /tmp/zolai-push-XXXXXX.db)"
cleanup() { rm -f "${LOCAL_SNAP}" 2>/dev/null || true; }
trap cleanup EXIT

# --- (a) WAL-safe local snapshot ---------------------------------------------
echo "[1/7] local snapshot"
sqlite3 "${LOCAL_DB}" ".backup '${LOCAL_SNAP}'"
echo "  snapshot: $(hsize "${LOCAL_SNAP}") ($(stat -c %s "${LOCAL_SNAP}") bytes)"
INTEGRITY="$(sqlite3 "${LOCAL_SNAP}" 'PRAGMA integrity_check;' 2>&1 || true)"
[[ "${INTEGRITY}" == "ok" ]] || { echo "ERROR: local snapshot integrity: ${INTEGRITY}" >&2; exit 1; }
echo "  integrity: ok"

# --- (b) push up --------------------------------------------------------------
echo "[2/7] rsync → ${SERVER_HOST}:${REMOTE_TMP}"
rsync -a -e "ssh ${SSH_OPTS[*]}" "${LOCAL_SNAP}" "${SERVER_HOST}:${REMOTE_TMP}"
echo "  pushed: $(ssh "${SSH_OPTS[@]}" "${SERVER_HOST}" "stat -c %s '${REMOTE_TMP}'") bytes"

# --- (c) stop the API container (server DB is hot) ----------------------------
echo "[3/7] stop api container"
ssh "${SSH_OPTS[@]}" "${SERVER_HOST}" "set -e; cd \"\$(dirname '${COMPOSE_FILE}')\" && docker compose -f '${COMPOSE_FILE}' stop api"
echo "  api stopped"

# --- (d) back up the server DB FIRST ------------------------------------------
echo "[4/7] back up server DB"
ssh "${SSH_OPTS[@]}" "${SERVER_HOST}" "set -e
  DB='${SERVER_DB}'
  BDIR=\"\$(dirname \"\$DB\")/backups\"
  mkdir -p \"\$BDIR\"
  TS=\$(date +%Y-%m-%d_%H%M)
  sqlite3 \"\$DB\" \".backup '\$BDIR/zolai-\$TS.db'\"
  gzip -f \"\$BDIR/zolai-\$TS.db\"
  ls -l \"\$BDIR/zolai-\$TS.db.gz\""
echo "  server backup created"

# --- (e) integrity check the pushed file ON THE SERVER ------------------------
echo "[5/7] integrity_check pushed file on server"
REMOTE_INTEGRITY="$(ssh "${SSH_OPTS[@]}" "${SERVER_HOST}" "sqlite3 '${REMOTE_TMP}' 'PRAGMA integrity_check;' 2>&1 || true")"
if [[ "${REMOTE_INTEGRITY}" != "ok" ]]; then
  echo "ERROR: pushed file failed integrity_check: ${REMOTE_INTEGRITY}" >&2
  echo "  server DB untouched (stopped); container will be restarted by cleanup." >&2
  ssh "${SSH_OPTS[@]}" "${SERVER_HOST}" "cd \"\$(dirname '${COMPOSE_FILE}')\" && docker compose -f '${COMPOSE_FILE}' start api" || true
  exit 1
fi
echo "  integrity: ok"

# --- (f) atomic replace + drop stale WAL + restart ----------------------------
echo "[6/7] replace server DB + restart api"
ssh "${SSH_OPTS[@]}" "${SERVER_HOST}" "set -e
  mv -f '${REMOTE_TMP}' '${SERVER_DB}'
  rm -f '${SERVER_DB}-wal' '${SERVER_DB}-shm'
  cd \"\$(dirname '${COMPOSE_FILE}')\" && docker compose -f '${COMPOSE_FILE}' start api
  ls -l '${SERVER_DB}'"
echo "  api started"

# --- (g) health gate ----------------------------------------------------------
if [[ ${HEALTH} -eq 1 ]]; then
  echo "[7/7] wait for API health"
  for i in $(seq 1 30); do
    if ssh "${SSH_OPTS[@]}" "${SERVER_HOST}" "curl -sf http://127.0.0.1:8001/health" >/dev/null 2>&1; then
      echo "  /health: 200 (after ${i}s)"; break
    fi
    [[ "$i" -eq 30 ]] && { echo "ERROR: API did not become healthy in 30s" >&2; exit 1; }
    sleep 1
  done
else
  echo "[7/7] health check skipped (--no-restart-health)"
fi

echo "DONE — local ${LOCAL_DB} ($(hsize "${LOCAL_DB}")) pushed to ${SERVER_HOST}:${SERVER_DB}."
echo "Next: run scripts/sync-db-from-server.sh after any further server-side change."
