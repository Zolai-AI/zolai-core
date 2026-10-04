#!/usr/bin/env bash
# sync-db-from-server.sh — pull data/zolai.db from pcore-server → local workspace.
#
# POST-DEPLOY NOTE: run this after **every** pcore-server deploy or any other
# DB-affecting update (providers seed, migrations, observation build, review
# writes, …) so the local dev DB matches the server. See the deploy section of
# `docs/planning/AI_AGENTS_RBAC_PLAN.md` §F and the "pull to local" section of
# `docs/governance/backup-strategy.md`.
#
# Safety (live WAL DB — never raw-copy a hot file):
#   (a) server side: consistent snapshot via `sqlite3 .backup` (WAL-safe),
#   (b) rsync the snapshot down to a local temp file (same filesystem),
#   (c) back up the existing local DB first (scripts/backup-zolai.sh if present,
#       else data/backups/zolai-<ts>.db.gz),
#   (d) `PRAGMA integrity_check` on the pulled file — abort unless `ok`,
#   (e) `--dry-run` prints the plan and exits without touching anything,
#   (f) `set -euo pipefail`; every size is echoed.
#
# Usage:
#   scripts/sync-db-from-server.sh [--dry-run] [--target PATH] [--keep-local-wal]
#
# Env overrides:
#   ZOLAI_SYNC_HOST       SSH alias/host        (default: pcore-server)
#   ZOLAI_SYNC_SERVER_DB  remote DB path        (default: /home/ubuntu/zolai-core/data/zolai.db)
#   ZOLAI_SYNC_SSH_KEY    identity file          (default: ~/.ssh/LightsailDefaultKey-ap-southeast-1.pem)
#   ZOLAI_DATA_ROOT       local data dir root    (default: <workspace>/data)
set -euo pipefail

SERVER_HOST="${ZOLAI_SYNC_HOST:-pcore-server}"
SERVER_DB="${ZOLAI_SYNC_SERVER_DB:-/home/ubuntu/zolai-core/data/zolai.db}"
SSH_KEY="${ZOLAI_SYNC_SSH_KEY:-$HOME/.ssh/LightsailDefaultKey-ap-southeast-1.pem}"
REMOTE_SNAPSHOT="/tmp/zolai-sync.db"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"        # zolai-core
WORKSPACE_ROOT="$(cd "${REPO_ROOT}/.." && pwd)"    # workspace root (canonical data/)

DRY_RUN=0
KEEP_WAL=0
TARGET=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run|-n) DRY_RUN=1; shift ;;
    --keep-local-wal) KEEP_WAL=1; shift ;;
    --target) TARGET="${2:-}"; shift 2 ;;
    --help|-h)
      sed -n '2,25p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *) echo "ERROR: unknown argument '$1' (try --help)" >&2; exit 2 ;;
  esac
done

# Local target: ZOLAI_DATA_ROOT wins, then the workspace data/ dir (the
# canonical store used by backup-zolai.sh and zolai config), then this repo's own data/.
if [[ -n "${TARGET}" ]]; then
  LOCAL_DB="$TARGET"
elif [[ -n "${ZOLAI_DATA_ROOT:-}" ]]; then
  LOCAL_DB="${ZOLAI_DATA_ROOT%/}/zolai.db"
elif [[ -d "${WORKSPACE_ROOT}/data" ]]; then
  LOCAL_DB="${WORKSPACE_ROOT}/data/zolai.db"
else
  LOCAL_DB="${REPO_ROOT}/data/zolai.db"
fi
LOCAL_DIR="$(dirname "${LOCAL_DB}")"

SSH_OPTS=()
if [[ -f "${SSH_KEY}" ]]; then
  SSH_OPTS=(-i "${SSH_KEY}" -o IdentitiesOnly=yes)
else
  SSH_OPTS=(-o IdentitiesOnly=no)
fi

hsize() { # human size, missing file → "-"
  if [[ -e "$1" ]]; then du -h "$1" | cut -f1; else echo "-"; fi
}

echo "== sync-db-from-server =="
echo "  host      : ${SERVER_HOST}"
echo "  server db : ${SERVER_DB}"
echo "  snapshot  : ${SERVER_HOST}:${REMOTE_SNAPSHOT}  (sqlite3 .backup)"
echo "  local db  : ${LOCAL_DB}  (size: $(hsize "${LOCAL_DB}"))"
echo "  mode      : $([[ ${DRY_RUN} -eq 1 ]] && echo 'DRY-RUN (no changes)' || echo 'APPLY')"

if [[ ${DRY_RUN} -eq 1 ]]; then
  cat <<EOF
  [dry-run] would run, in order:
    1. ssh ${SERVER_HOST} 'sqlite3 ${SERVER_DB} ".backup ${REMOTE_SNAPSHOT}"'   # WAL-safe server snapshot
    2. rsync -a -e ssh ${SERVER_HOST}:${REMOTE_SNAPSHOT} ${LOCAL_DB}.incoming
    3. sqlite3 ${LOCAL_DB}.incoming 'PRAGMA integrity_check;'  # must be 'ok' or abort
    4. backup local DB first: scripts/backup-zolai.sh (else data/backups/zolai-<ts>.db.gz)
    5. mv ${LOCAL_DB}.incoming ${LOCAL_DB}  (+ drop stale -wal/-shm)
    6. ssh ${SERVER_HOST} 'rm -f ${REMOTE_SNAPSHOT}'
  Nothing was changed.
EOF
  exit 0
fi

command -v sqlite3 >/dev/null 2>&1 || { echo "ERROR: sqlite3 not found in PATH" >&2; exit 1; }
command -v rsync   >/dev/null 2>&1 || { echo "ERROR: rsync not found in PATH" >&2; exit 1; }
command -v ssh     >/dev/null 2>&1 || { echo "ERROR: ssh not found in PATH" >&2; exit 1; }

mkdir -p "${LOCAL_DIR}"
INCOMING="${LOCAL_DB}.incoming.$$"
cleanup() { rm -f "${INCOMING}" 2>/dev/null || true; }
trap cleanup EXIT

# --- (a) consistent server snapshot (never raw-copy a live WAL file) ----------
echo "[1/6] server snapshot → ${REMOTE_SNAPSHOT}"
ssh "${SSH_OPTS[@]}" "${SERVER_HOST}" \
  "set -e; sqlite3 '${SERVER_DB}' '.backup ${REMOTE_SNAPSHOT}'; ls -l '${REMOTE_SNAPSHOT}'"
REMOTE_SIZE="$(ssh "${SSH_OPTS[@]}" "${SERVER_HOST}" "stat -c %s '${REMOTE_SNAPSHOT}'" || echo unknown)"
echo "  server snapshot size: ${REMOTE_SIZE} bytes"

# --- (b) rsync the snapshot down --------------------------------------------
echo "[2/6] rsync ${SERVER_HOST}:${REMOTE_SNAPSHOT} → ${INCOMING}"
rsync -a -e "ssh ${SSH_OPTS[*]}" "${SERVER_HOST}:${REMOTE_SNAPSHOT}" "${INCOMING}"
echo "  pulled: $(hsize "${INCOMING}") ($(stat -c %s "${INCOMING}") bytes)"

if [[ ! -s "${INCOMING}" ]]; then
  echo "ERROR: pulled snapshot is empty" >&2
  exit 1
fi

# --- (d) integrity check BEFORE replacing -----------------------------------
echo "[3/6] PRAGMA integrity_check on pulled file"
INTEGRITY="$(sqlite3 "${INCOMING}" 'PRAGMA integrity_check;' 2>&1 || true)"
if [[ "${INTEGRITY}" != "ok" ]]; then
  echo "ERROR: integrity_check failed — refusing to replace the local DB:" >&2
  echo "  ${INTEGRITY}" >&2
  exit 1
fi
echo "  integrity: ok"

# --- (c) back up the local DB before overwriting ----------------------------
if [[ -f "${LOCAL_DB}" ]]; then
  echo "[4/6] backing up existing local DB first"
  if [[ -x "${WORKSPACE_ROOT}/scripts/backup-zolai.sh" ]]; then
    "${WORKSPACE_ROOT}/scripts/backup-zolai.sh"
  else
    TS="$(date +%Y-%m-%d_%H%M)"
    BACKUP_DIR="${LOCAL_DIR}/backups"
    mkdir -p "${BACKUP_DIR}"
    BACKUP="${BACKUP_DIR}/zolai-${TS}.db.gz"
    sqlite3 "${LOCAL_DB}" ".backup '${BACKUP_DIR}/zolai-${TS}.db'"
    gzip -f "${BACKUP_DIR}/zolai-${TS}.db"
    echo "  local backup → ${BACKUP} ($(hsize "${BACKUP}"))"
  fi
else
  echo "[4/6] no existing local DB at ${LOCAL_DB} — nothing to back up"
fi

# --- replace (atomic within the same filesystem) ----------------------------
echo "[5/6] replace ${LOCAL_DB}"
echo "  before: $(hsize "${LOCAL_DB}")"
mv -f "${INCOMING}" "${LOCAL_DB}"
if [[ ${KEEP_WAL} -eq 0 ]]; then
  # The old WAL/SHM belong to the replaced file — keeping them would corrupt it.
  rm -f "${LOCAL_DB}-wal" "${LOCAL_DB}-shm"
fi
echo "  after : $(hsize "${LOCAL_DB}")"

POST="$(sqlite3 "${LOCAL_DB}" 'PRAGMA integrity_check;' 2>&1 || true)"
[[ "${POST}" == "ok" ]] || { echo "ERROR: local DB failed integrity_check: ${POST}" >&2; exit 1; }
echo "  local integrity: ok"

# --- (f) drop the remote snapshot -------------------------------------------
echo "[6/6] remove remote snapshot"
ssh "${SSH_OPTS[@]}" "${SERVER_HOST}" "rm -f '${REMOTE_SNAPSHOT}'" || true

echo "DONE — ${LOCAL_DB} synced from ${SERVER_HOST} ($(hsize "${LOCAL_DB}"))."
