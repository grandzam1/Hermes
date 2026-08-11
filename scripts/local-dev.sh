#!/usr/bin/env bash
# Local (no-Docker) Hermes control script.
# Usage:
#   ./scripts/local-dev.sh start|stop|restart|status
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$ROOT/packages/hermes-api"
APP_DIR="$ROOT/packages/hermes-app"
LOG_DIR="${HERMES_LOG_DIR:-/tmp}"
PID_DIR="${HERMES_PID_DIR:-/tmp}"

API_PID="$PID_DIR/hermes-api.pid"
CELERY_PID="$PID_DIR/hermes-celery.pid"
APP_PID="$PID_DIR/hermes-app.pid"
API_LOG="$LOG_DIR/hermes-api.log"
CELERY_LOG="$LOG_DIR/hermes-celery.log"
APP_LOG="$LOG_DIR/hermes-app.log"

API_HOST="${HERMES_API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
APP_HOST="${HERMES_APP_HOST:-127.0.0.1}"
# Default 5174 because cobalt often uses 5173 on this machine
APP_PORT="${HERMES_DEV_PORT:-5174}"

export PATH="${HOME}/.local/bin:${PATH}"

ok() { printf '  ✓ %s\n' "$*"; }
bad() { printf '  ✗ %s\n' "$*"; }
info() { printf '→ %s\n' "$*"; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

is_pid_running() {
  local pid="${1:-}"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

stop_pidfile() {
  local pidfile="$1"
  local label="$2"
  if [[ -f "$pidfile" ]]; then
    local pid
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if is_pid_running "$pid"; then
      kill "$pid" 2>/dev/null || true
      sleep 0.4
      if is_pid_running "$pid"; then
        kill -9 "$pid" 2>/dev/null || true
      fi
      info "Stopped $label (pid $pid)"
    fi
    rm -f "$pidfile"
  fi
}

# Kill leftover Hermes processes that may not match pidfiles (uv wrappers, workers).
stop_matching() {
  local pattern="$1"
  local label="$2"
  local pids
  pids="$(ps -eo pid=,args= | awk -v pat="$pattern" 'index($0, pat) {print $1}' || true)"
  if [[ -z "${pids// }" ]]; then
    return 0
  fi
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  sleep 0.5
  pids="$(ps -eo pid=,args= | awk -v pat="$pattern" 'index($0, pat) {print $1}' || true)"
  if [[ -n "${pids// }" ]]; then
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
  fi
  info "Cleared leftover $label processes"
}

ensure_redis() {
  if redis-cli ping >/dev/null 2>&1; then
    return 0
  fi
  info "Starting Redis..."
  if command -v redis-server >/dev/null 2>&1; then
    redis-server --daemonize yes
    sleep 0.5
  elif command -v sudo >/dev/null 2>&1; then
    sudo service redis-server start >/dev/null 2>&1 || true
    sleep 0.5
  fi
  redis-cli ping >/dev/null 2>&1
}

wait_http() {
  local url="$1"
  local label="$2"
  local tries="${3:-30}"
  local i
  for i in $(seq 1 "$tries"); do
    if curl -sf "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  bad "$label did not become ready ($url)"
  return 1
}

wait_log() {
  local logfile="$1"
  local pattern="$2"
  local label="$3"
  local tries="${4:-40}"
  local i
  for i in $(seq 1 "$tries"); do
    if [[ -f "$logfile" ]] && grep -Eq "$pattern" "$logfile"; then
      return 0
    fi
    sleep 0.5
  done
  bad "$label did not become ready (log: $logfile)"
  return 1
}

cmd_stop() {
  info "Stopping Hermes local services..."
  stop_pidfile "$APP_PID" "frontend"
  stop_pidfile "$CELERY_PID" "celery"
  stop_pidfile "$API_PID" "api"

  stop_matching "$API_DIR" "api/celery"
  stop_matching "$APP_DIR.*vite\|vite --host ${APP_HOST} --port ${APP_PORT}" "frontend"

  # Free ports if still held
  if command -v fuser >/dev/null 2>&1; then
    fuser -k "${API_PORT}/tcp" >/dev/null 2>&1 || true
    fuser -k "${APP_PORT}/tcp" >/dev/null 2>&1 || true
  fi
  ok "Stop complete"
}

cmd_start() {
  require_cmd uv
  require_cmd pnpm
  require_cmd redis-cli
  require_cmd curl

  if [[ ! -f "$API_DIR/.env" ]]; then
    if [[ -f "$ROOT/.env" ]]; then
      cp "$ROOT/.env" "$API_DIR/.env"
      info "Copied root .env -> packages/hermes-api/.env"
    else
      echo "Missing $API_DIR/.env (copy from .env.example first)" >&2
      exit 1
    fi
  fi

  mkdir -p "$API_DIR/data" "$API_DIR/downloads" "$API_DIR/temp" "$API_DIR/cookies"

  ensure_redis || {
    bad "Redis is not running"
    exit 1
  }
  ok "Redis up"

  info "Starting API on ${API_HOST}:${API_PORT}..."
  : >"$API_LOG"
  (
    cd "$API_DIR"
    nohup uv run uvicorn app.main:app --reload --host "$API_HOST" --port "$API_PORT" >>"$API_LOG" 2>&1 &
    echo $! >"$API_PID"
  )
  wait_http "http://${API_HOST}:${API_PORT}/health" "API" || {
    tail -n 30 "$API_LOG" || true
    exit 1
  }
  ok "API healthy"

  info "Starting Celery worker..."
  : >"$CELERY_LOG"
  (
    cd "$API_DIR"
    nohup uv run celery -A app.tasks.celery_app worker \
      --loglevel=info \
      --concurrency=1 \
      --hostname="hermes-worker@%h" \
      --queues=hermes.downloads,hermes.cleanup,hermes.default \
      >>"$CELERY_LOG" 2>&1 &
    echo $! >"$CELERY_PID"
  )
  wait_log "$CELERY_LOG" 'ready\.' "Celery" || {
    tail -n 40 "$CELERY_LOG" || true
    exit 1
  }
  ok "Celery ready"

  info "Starting frontend on ${APP_HOST}:${APP_PORT}..."
  : >"$APP_LOG"
  (
    cd "$APP_DIR"
    nohup pnpm exec vite --host "$APP_HOST" --port "$APP_PORT" >>"$APP_LOG" 2>&1 &
    echo $! >"$APP_PID"
  )
  wait_http "http://${APP_HOST}:${APP_PORT}/" "Frontend" || {
    tail -n 30 "$APP_LOG" || true
    exit 1
  }
  ok "Frontend up"

  echo
  cmd_status
}

cmd_restart() {
  cmd_stop
  sleep 1
  cmd_start
}

cmd_status() {
  echo "Hermes local status"
  echo "-------------------"

  if redis-cli ping >/dev/null 2>&1; then
    ok "Redis: PONG"
  else
    bad "Redis: down"
  fi

  if curl -sf "http://${API_HOST}:${API_PORT}/health" >/dev/null 2>&1; then
    local health
    health="$(curl -sf "http://${API_HOST}:${API_PORT}/health")"
    ok "API: $health"
  else
    bad "API: down (http://${API_HOST}:${API_PORT}/health)"
  fi

  if [[ -f "$CELERY_PID" ]] && is_pid_running "$(cat "$CELERY_PID")" && grep -Eq 'ready\.' "$CELERY_LOG" 2>/dev/null; then
    local cookies
    cookies="$(grep -E '^HERMES_COOKIES_JSON=' "$API_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
    ok "Celery: running (pid $(cat "$CELERY_PID"))"
    if [[ -n "$cookies" ]]; then
      ok "Cookies config: $cookies"
    else
      bad "Cookies config: HERMES_COOKIES_JSON not set"
    fi
  else
    bad "Celery: down"
  fi

  if curl -sf "http://${APP_HOST}:${APP_PORT}/" >/dev/null 2>&1; then
    ok "Frontend: http://${APP_HOST}:${APP_PORT}/"
  else
    bad "Frontend: down (http://${APP_HOST}:${APP_PORT}/)"
  fi

  echo
  echo "Logs:"
  echo "  API     $API_LOG"
  echo "  Celery  $CELERY_LOG"
  echo "  App     $APP_LOG"
}

usage() {
  cat <<EOF
Usage: $(basename "$0") <command>

Commands:
  start     Start Redis (if needed), API, Celery, frontend
  stop      Stop API, Celery, frontend
  restart   Stop then start everything
  status    Health-check all services

Examples:
  ./scripts/local-dev.sh restart
  ./scripts/local-dev.sh status

Env overrides:
  HERMES_DEV_PORT=5174   Frontend port (default 5174)
  API_PORT=8000          API port
EOF
}

main() {
  local cmd="${1:-}"
  case "$cmd" in
    start) cmd_start ;;
    stop) cmd_stop ;;
    restart) cmd_restart ;;
    status) cmd_status ;;
    -h|--help|help|"") usage ;;
    *)
      echo "Unknown command: $cmd" >&2
      usage
      exit 1
      ;;
  esac
}

main "$@"
