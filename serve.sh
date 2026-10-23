#!/usr/bin/env bash
# fass serve launcher — idempotent, pidfile-tracked, exact-PID only.
#
#   ./serve.sh           launch; no-op if a healthy server already answers
#   ./serve.sh --force   kill the current server by its exact PID, relaunch
#
# Safety: never pattern-kills (no pkill -f). The current server is located
# via the pidfile, then by scanning /proc cmdlines for this repo's
# `.venv/bin/python -m fass serve`; if the port is held by an unrelated
# process the script refuses and exits 1. A server that ignores SIGTERM is
# left alone (no SIGKILL), and the script aborts instead of relaunching.
#
# Stop manually:   kill "$(cat /tmp/fass-serve.pid)"
# Env overrides:   FASS_HOST (0.0.0.0)   FASS_PORT (8000)
#                  FASS_LOG (/tmp/fass-serve.log)
#                  FASS_PIDFILE (/tmp/fass-serve.pid)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${FASS_HOST:-0.0.0.0}"
PORT="${FASS_PORT:-8000}"
LOG="${FASS_LOG:-/tmp/fass-serve.log}"
PIDFILE="${FASS_PIDFILE:-/tmp/fass-serve.pid}"
PY="$REPO/.venv/bin/python"
FORCE=0
[[ "${1:-}" == "--force" || "${1:-}" == "-f" ]] && FORCE=1

say() { printf '\n%s\n' "$*"; }
health() { curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; }
port_open() {
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$PORT\$"
  else
    (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null && { exec 3>&- 3<&-; return 0; }
    return 1
  fi
}
is_our_server() {
  # exact process identity: cmdline STARTS with this venv's python followed
  # by `-m fass serve` (anchored, so a shell grepping for the server can't
  # match itself)
  local p="$1" cmd
  [[ -r "/proc/$p/cmdline" ]] || return 1
  cmd="$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null || true)"
  [[ "$cmd" == "$PY -m fass serve"* ]]
}
find_pid() {
  local p
  if [[ -f "$PIDFILE" ]]; then
    p="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [[ -n "$p" ]] && is_our_server "$p"; then echo "$p"; return 0; fi
  fi
  for p in /proc/[0-9]*; do
    p="${p#/proc/}"
    if is_our_server "$p"; then echo "$p"; return 0; fi
  done
  return 1
}

pid="$(find_pid || true)"

if health && [[ "$FORCE" -eq 0 ]]; then
  say "fass already healthy on $HOST:$PORT${pid:+ — PID $pid}. Nothing to do."
  exit 0
fi

if [[ -n "$pid" ]]; then
  say "stopping current server PID $pid by exact PID"
  kill "$pid"
  for _ in $(seq 1 40); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
  if kill -0 "$pid" 2>/dev/null; then
    echo "PID $pid ignored SIGTERM — aborting (not sending SIGKILL, not relaunching)" >&2
    exit 1
  fi
fi

if port_open; then
  echo "port $PORT is held by an unrelated process — refusing to touch it" >&2
  exit 1
fi

[[ -x "$PY" ]] || { echo "missing $PY — create the venv first (python3 -m venv .venv && pip install -e .)" >&2; exit 1; }

say "launching fass serve on $HOST:$PORT (log: $LOG)"
setsid nohup "$PY" -m fass serve --host "$HOST" --port "$PORT" >>"$LOG" 2>&1 < /dev/null &
pid=$!
echo "$pid" > "$PIDFILE"

for _ in $(seq 1 40); do
  if health; then
    say "up — PID $pid (pidfile: $PIDFILE)"
    exit 0
  fi
  kill -0 "$pid" 2>/dev/null || break
  sleep 0.25
done
echo "server PID $pid did not become healthy within 10 s — last log lines:" >&2
tail -n 20 "$LOG" >&2
exit 1
