#!/usr/bin/env bash
# ============================================================================
# Local dev stack — start/stop/restart the API and the Studio, like on the host.
# ============================================================================
# Mirrors the two pcore-server systemd units:
#
#   zolai-core-dev.service
#     uvicorn zolai.api.server:app --host 127.0.0.1 --port 8001
#   zolai-studio-dev.service
#     bun run dev --host 127.0.0.1 --port 5173   (ZOLAI_UPSTREAM=:8001)
#
# Usage:
#   scripts/dev/local-stack.sh start   [api|studio|all]
#   scripts/dev/local-stack.sh stop    [api|studio|all]
#   scripts/dev/local-stack.sh restart [api|studio|all]
#   scripts/dev/local-stack.sh status  [api|studio|all]
#   scripts/dev/local-stack.sh logs    [api|studio|all] [lines]
#
# State (pid files, logs) lives in $ZOLAI_DEV_RUN_DIR (default /tmp/zolai-dev),
# so nothing is written into the checkout.
#
# Environment overrides:
#   ZOLAI_API_PORT      API port             (default 8001)
#   ZOLAI_STUDIO_PORT   Studio port          (default 5173)
#   ZOLAI_UPSTREAM      API URL the studio proxies to (default :$ZOLAI_API_PORT)
#   ZOLAI_STUDIO_DIR    Studio checkout      (default ../zolai-explorer)
#   ZOLAI_DEV_RUN_DIR   pid/log directory    (default /tmp/zolai-dev)
#   ZOLAI_DATA_ROOT     data root            (default $WORKSPACE/data)
#   ZOLAI_DB_PATH       canonical database   (default $ZOLAI_DATA_ROOT/zolai.db)
#   ZOLAI_ENV_FILE      systemd-style env file sourced before the API starts
#                       (default: .env.dev if present; .env is loaded by the app)
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE="$(cd "$SCRIPT_DIR/../.." && pwd)"
WORKSPACE="$(cd "$CORE/.." && pwd)"

API_PORT="${ZOLAI_API_PORT:-8001}"
STUDIO_PORT="${ZOLAI_STUDIO_PORT:-5173}"
UPSTREAM="${ZOLAI_UPSTREAM:-http://127.0.0.1:${API_PORT}}"
STUDIO_DIR="${ZOLAI_STUDIO_DIR:-$WORKSPACE/zolai-explorer}"
RUN_DIR="${ZOLAI_DEV_RUN_DIR:-/tmp/zolai-dev}"

R='\033[0;31m' G='\033[0;32m' Y='\033[1;33m' NC='\033[0m'
say()  { echo -e "  $*"; }
ok()   { echo -e "  ${G}$*${NC}"; }
warn() { echo -e "  ${Y}$*${NC}"; }
err()  { echo -e "  ${R}$*${NC}" >&2; }

alive() {
  local pid="${1:-}"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  [[ "$(ps -o stat= -p "$pid" 2>/dev/null | tr -d ' ')" != Z* ]]
}

pidfile() { echo "$RUN_DIR/$1.pid"; }
logfile() { echo "$RUN_DIR/$1.log"; }

port_pid() {
  local port="$1"
  ss -ltnpH "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1
}

wait_http() {
  # wait_http URL SECONDS PIDFILE LOGFILE  → 0 ready, 1 failed
  local url="$1" secs="$2" pf="$3" log="$4" code pid
  for ((i = 0; i < secs; i++)); do
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 2 "$url" 2>/dev/null || true)"
    case "$code" in 2* | 3*) return 0 ;; esac
    pid="$(cat "$pf" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && ! alive "$pid"; then
      err "process exited while starting — see $log"
      return 1
    fi
    sleep 1
  done
  return 1
}

api_python() {
  if [[ -x "$CORE/.venv/bin/python" ]]; then
    echo "$CORE/.venv/bin/python"
  else
    command -v python3
  fi
}

# ── start ────────────────────────────────────────────────────────────────────
start_api() {
  local pf log py port_owner
  pf="$(pidfile api)"
  log="$(logfile api)"

  if [[ -f "$pf" ]] && alive "$(cat "$pf")"; then
    ok "API already running (pid $(cat "$pf"), :$API_PORT)"
    return 0
  fi
  port_owner="$(port_pid "$API_PORT" || true)"
  if [[ -n "$port_owner" ]]; then
    err "port $API_PORT is already taken by pid $port_owner:"
    ps -o pid,cmd= -p "$port_owner" | sed 's/^/    /' >&2
    return 1
  fi

  py="$(api_python)" || { err "no python found (expected $CORE/.venv/bin/python)"; return 1; }
  mkdir -p "$RUN_DIR"

  if [[ -n "${ZOLAI_ENV_FILE:-}" || -f "$CORE/.env.dev" ]]; then
    local envfile="${ZOLAI_ENV_FILE:-$CORE/.env.dev}"
    # systemd's EnvironmentFile is shell-compatible; the app loads .env itself.
    set -a
    # shellcheck disable=SC1090
    source "$envfile"
    set +a
    say "env: $envfile"
  fi

  # One database for the whole dev stack. Without this the resolver falls back
  # to its own defaults, which is how a stray zolai-core/data/zolai.db appeared
  # next to the real 2.9 GB workspace store. An explicit env file still wins.
  export ZOLAI_DATA_ROOT="${ZOLAI_DATA_ROOT:-$WORKSPACE/data}"
  export ZOLAI_DB_PATH="${ZOLAI_DB_PATH:-$ZOLAI_DATA_ROOT/zolai.db}"
  say "db: $ZOLAI_DB_PATH"

  say "starting API  :$API_PORT (nginx parity, 127.0.0.1)"
  (
    cd "$CORE"
    exec setsid "$py" -m uvicorn zolai.api.server:app \
      --host 127.0.0.1 --port "$API_PORT" >>"$log" 2>&1
  ) &
  echo "$!" >"$pf"

  if wait_http "http://127.0.0.1:$API_PORT/health" 90 "$pf" "$log"; then
    ok "API ready  http://127.0.0.1:$API_PORT/health  (pid $(cat "$pf"))"
  else
    err "API did not answer /health in 90s — last log lines:"
    tail -n 20 "$log" >&2 || true
    stop_api >/dev/null 2>&1 || true
    return 1
  fi
}

start_studio() {
  local pf log bun
  pf="$(pidfile studio)"
  log="$(logfile studio)"

  if [[ -f "$pf" ]] && alive "$(cat "$pf")"; then
    ok "Studio already running (pid $(cat "$pf"), :$STUDIO_PORT)"
    return 0
  fi
  if [[ -n "$(port_pid "$STUDIO_PORT" || true)" ]]; then
    err "port $STUDIO_PORT is already taken by pid $(port_pid "$STUDIO_PORT")"
    return 1
  fi
  [[ -d "$STUDIO_DIR" ]] || { err "studio checkout not found: $STUDIO_DIR"; return 1; }
  bun="$(command -v bun || true)"
  [[ -n "$bun" ]] || { err "bun not found (https://bun.sh)"; return 1; }

  if [[ ! -d "$STUDIO_DIR/node_modules" ]]; then
    say "installing studio dependencies (bun install)..."
    (cd "$STUDIO_DIR" && "$bun" install) || { err "bun install failed"; return 1; }
  fi

  mkdir -p "$RUN_DIR"
  say "starting Studio :$STUDIO_PORT  (upstream $UPSTREAM)"
  (
    cd "$STUDIO_DIR"
    exec setsid env ZOLAI_UPSTREAM="$UPSTREAM" \
      "$bun" run dev --host 127.0.0.1 --port "$STUDIO_PORT" >>"$log" 2>&1
  ) &
  echo "$!" >"$pf"

  if wait_http "http://127.0.0.1:$STUDIO_PORT/" 60 "$pf" "$log"; then
    ok "Studio ready  http://127.0.0.1:$STUDIO_PORT/  (pid $(cat "$pf"))"
  else
    err "Studio did not answer in 60s — last log lines:"
    tail -n 20 "$log" >&2 || true
    stop_studio >/dev/null 2>&1 || true
    return 1
  fi
}

# ── stop ─────────────────────────────────────────────────────────────────────
stop_marker() {
  # stop one service given its expected cmdline marker and port
  local svc="$1" marker="$2" port="$3" pf pid
  pf="$(pidfile "$svc")"
  pid=""

  if [[ -f "$pf" ]]; then
    pid="$(cat "$pf")"
    if alive "$pid"; then
      say "stopping $svc (pid $pid)..."
      kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
      for _ in $(seq 1 10); do alive "$pid" || break; sleep 1; done
      if alive "$pid"; then
        warn "$svc ignored SIGTERM — killing"
        kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
      fi
      ok "$svc stopped"
    else
      warn "$svc pid $pid was stale"
    fi
    rm -f "$pf"
    return 0
  fi

  # No pid file: only kill a listener whose cmdline proves it is ours.
  pid="$(port_pid "$port" || true)"
  if [[ -n "$pid" ]] && tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null | grep -q "$marker"; then
    say "stopping stray $svc (pid $pid, :$port)"
    kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 10); do alive "$pid" || break; sleep 1; done
    alive "$pid" && kill -KILL "$pid" 2>/dev/null || true
    ok "$svc stopped"
  else
    say "$svc not running"
  fi
}

stop_api()    { stop_marker api zolai.api.server "$API_PORT"; }
stop_studio() { stop_marker studio "run dev" "$STUDIO_PORT"; }

# ── status ───────────────────────────────────────────────────────────────────
status_one() {
  local svc="$1" port="$2" url="$3" pf pid code state uptime
  pf="$(pidfile "$svc")"
  pid="$(cat "$pf" 2>/dev/null || true)"
  if alive "$pid"; then
    uptime="$(ps -o etime= -p "$pid" | tr -d ' ')"
    state="running (pid $pid, up $uptime)"
  else
    state="stopped"
  fi
  code="$(curl -s -o /dev/null -w '%{http_code}' -m 2 "$url" 2>/dev/null || true)"
  printf '  %-7s :%-5s %-28s http=%s  %s\n' "$svc" "$port" "$state" "${code:-000}" "$url"
}

status_all() {
  status_one api "$API_PORT" "http://127.0.0.1:$API_PORT/health"
  status_one studio "$STUDIO_PORT" "http://127.0.0.1:$STUDIO_PORT/"
  say "logs: $(logfile api)  $(logfile studio)"
}

# ── dispatch ─────────────────────────────────────────────────────────────────
cmd="${1:-status}"
target="${2:-all}"
lines="${3:-80}"

case "$target" in
  api | studio | all) ;;
  *) err "unknown target '$target' (api|studio|all)"; exit 2 ;;
esac

case "$cmd" in
  start)
    if [[ "$target" == all || "$target" == api ]]; then start_api; fi
    if [[ "$target" == all || "$target" == studio ]]; then start_studio; fi
    ;;
  stop)
    if [[ "$target" == all || "$target" == api ]]; then stop_api; fi
    if [[ "$target" == all || "$target" == studio ]]; then stop_studio; fi
    ;;
  restart)
    if [[ "$target" == all || "$target" == api ]]; then stop_api; start_api; fi
    if [[ "$target" == all || "$target" == studio ]]; then stop_studio; start_studio; fi
    ;;
  status) status_all ;;
  logs)
    case "$target" in
      api) tail -n "$lines" "$(logfile api)" 2>/dev/null || echo "(no api log)" ;;
      studio) tail -n "$lines" "$(logfile studio)" 2>/dev/null || echo "(no studio log)" ;;
      all)
        echo "── api ──"; tail -n "$lines" "$(logfile api)" 2>/dev/null || echo "(no api log)"
        echo "── studio ──"; tail -n "$lines" "$(logfile studio)" 2>/dev/null || echo "(no studio log)"
        ;;
    esac
    ;;
  *)
    err "usage: $(basename "$0") {start|stop|restart|status|logs} [api|studio|all] [lines]"
    exit 2
    ;;
esac
